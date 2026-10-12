"""存储约束与状态机（PRD 8.3/5.4）。"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from viberadar.contracts import RawItem, new_id, utcnow
from viberadar.store import repo_editions, repo_ingest, repo_pipeline, repo_products
from viberadar.store.models import ProductRow

NOW = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc)


def _raw(external_id: str, source_key: str = "manual") -> RawItem:
    return RawItem(
        source_key=source_key,
        external_id=external_id,
        source_url=f"https://example.com/{external_id}",
        title=f"Tool {external_id}",
        fetched_at=NOW,
    )


def test_raw_item_unique_key_dedup(session, temp_settings):
    from viberadar.store import repo_ingest as ri

    ri.ensure_source(session, "manual", "人工注入")
    row1, created1 = ri.upsert_raw_item(session, _raw("a"))
    row2, created2 = ri.upsert_raw_item(session, _raw("a"))
    assert created1 is True and created2 is False
    assert row1.id == row2.id
    assert len(session.query(ri.RawItemRow).all()) == 1


def test_metric_snapshots_append_not_overwrite(session):
    repo_ingest.ensure_source(session, "manual", "人工注入")
    row, _ = repo_ingest.upsert_raw_item(session, _raw("m"))
    repo_ingest.append_metrics(session, row.id, NOW, {"points": 10})
    repo_ingest.append_metrics(session, row.id, NOW, {"points": 20})
    snapshots = repo_ingest.latest_metrics(session, row.id)
    assert len(snapshots) == 2
    assert snapshots[0].metrics["points"] == 20


def test_edition_unique_date_timezone(session):
    e1 = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    e2 = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    assert e1.id == e2.id


def test_delivery_unique_dispatch_key(session):
    edition = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    a1, created1 = repo_editions.get_or_create_delivery(session, edition.id, "feishu", "webhook", 1)
    a2, created2 = repo_editions.get_or_create_delivery(session, edition.id, "feishu", "webhook", 1)
    assert created1 and not created2 and a1.id == a2.id
    a3, created3 = repo_editions.get_or_create_delivery(session, edition.id, "feishu", "webhook", 2)
    assert created3 and a3.id != a1.id


def test_edition_state_machine_illegal_transition(session):
    edition = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    repo_editions.transition(session, edition, "reviewing")
    repo_editions.transition(session, edition, "ready")
    repo_editions.mark_published(session, edition, NOW)
    with pytest.raises(repo_editions.EditionStateError):
        repo_editions.transition(session, edition, "draft")
    # mark_published 幂等（重复调用不报错、不改变状态）
    again = repo_editions.mark_published(session, edition, NOW)
    assert again.status == "published"


def test_replace_items_rejects_overwrite(session):
    edition = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    product = repo_products.create_product(
        session, "N", None, None, "url:https://example.com", NOW
    )
    repo_editions.replace_items(session, edition, 1, [{"product_id": product.id, "content": {}}])
    with pytest.raises(repo_editions.EditionStateError):
        repo_editions.replace_items(
            session, edition, 1, [{"product_id": product.id, "content": {}}]
        )


def test_launch_event_fingerprint_dedup(session):
    product = repo_products.create_product(
        session, "P", None, None, "url:https://example.com/p", NOW
    )
    e1, c1 = repo_products.create_launch_event(
        session, product.id, "launch", NOW, NOW, "hn:1"
    )
    e2, c2 = repo_products.create_launch_event(
        session, product.id, "launch", NOW, NOW, "hn:1"
    )
    assert c1 and not c2 and e1.id == e2.id


def test_merge_and_undo_restores_links(session):
    p1 = repo_products.create_product(session, "A", None, None, "url:https://a.com", NOW)
    p2 = repo_products.create_product(session, "B", None, None, "url:https://b.com", NOW)
    repo_ingest.ensure_source(session, "manual", "人工注入")
    raw, _ = repo_ingest.upsert_raw_item(session, _raw("merge1"))
    repo_products.link_product_source(session, p1.id, raw.id, "created")
    action = repo_products.merge_products(
        session, p1.id, p2.id, reason="同名", operator="curator", rule_version="manual-v1", now=NOW
    )
    assert repo_products.get_slug_redirect(session, p1.slug) is not None
    repo_products.undo_merge(session, action.id, operator="curator", now=NOW)
    assert repo_products.get_slug_redirect(session, p1.slug) is None
    sources = session.query(ProductRow).filter_by(id=p1.id).one()
    assert sources.status == "active"


def test_pipeline_run_and_stage_records(session):
    run = repo_pipeline.create_pipeline_run(
        session, new_id("run"), NOW, NOW, NOW, code_version="0.1.0", config_hash="abc"
    )
    from viberadar.contracts import StageResult, StageStatus

    repo_pipeline.record_stage(
        session,
        StageResult(run_id=run.run_id, stage="ingest", status=StageStatus.SUCCESS, output_count=3),
    )
    stages = repo_pipeline.list_stages(session, run.run_id)
    assert stages[0].stage == "ingest" and stages[0].status == "success"


def test_concurrent_writes_no_lock_error(tmp_path):
    """回归：并发写不得出现 database is locked（BEGIN IMMEDIATE + busy_timeout）。"""
    import threading

    from viberadar.contracts import EventPayload, EventType
    from viberadar.store import init_db, make_engine, make_session_factory, repo_feedback

    db_path = tmp_path / "concurrent.sqlite3"
    engine = make_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    errors: list[str] = []

    def worker(worker_id: int) -> None:
        try:
            for i in range(8):
                with factory() as sess:
                    payload = EventPayload(
                        client_event_id=f"conc-{worker_id}-{i}",
                        event_type=EventType.EDITION_VIEW,
                        ref="web",
                    )
                    repo_feedback.insert_event(sess, payload, f"actor-{worker_id}", True)
                    sess.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [], errors
    engine.dispose()


def test_default_engine_uses_settings_db_path_and_persists(tmp_path, monkeypatch):
    """回归：CLI/默认调用必须落文件库并跨进程持久化，不能静默使用内存库。"""
    import viberadar.settings as settings_mod
    from viberadar.store import get_engine, get_session_factory

    monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path / "data")
    settings_mod.reload_settings()
    try:
        engine = get_engine(reset=True)
        factory = get_session_factory()
        with factory() as sess:
            repo_editions.create_edition(sess, date(2026, 10, 12), "Asia/Shanghai")
            sess.commit()
        db_file = tmp_path / "data" / "viberadar.sqlite3"
        assert db_file.exists(), "默认引擎必须写入 settings.db_path 文件库"

        engine.dispose()
        engine2 = get_engine(reset=True)
        factory2 = get_session_factory()
        with factory2() as sess:
            edition = repo_editions.get_edition_by_date(sess, date(2026, 10, 12), "Asia/Shanghai")
            assert edition is not None
        engine2.dispose()
    finally:
        settings_mod.reload_settings()
