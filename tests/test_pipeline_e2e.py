"""端到端集成：完整期号、源故障、重放、重启持久化（PRD 11.3）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from viberadar.compose import build_edition_view, freeze_edition, publish_edition
from viberadar.pipeline import run_pipeline
from viberadar.store import (
    get_session_factory,
    init_db,
    make_engine,
    make_session_factory,
    repo_editions,
    repo_pipeline,
)

from .helpers import (
    CUTOFF,
    StubLLM,
    disable_external_sources,
    html_transport,
    manual_entries,
)

EDITION_DATE = date(2026, 10, 12)


def _run(session, session_factory, settings, entries=None, transports=None, **kwargs):
    llm = StubLLM(session_factory, settings)
    return run_pipeline(
        session,
        EDITION_DATE,
        settings=settings,
        cutoff_at=CUTOFF,
        llm=llm,
        manual_entries=entries if entries is not None else manual_entries(6),
        transports=transports or {"__enrich__": html_transport()},
        owner="test",
        **kwargs,
    )


def test_full_pipeline_to_published_edition(session, session_factory, temp_settings):
    disable_external_sources(temp_settings)
    report = _run(session, session_factory, temp_settings)
    assert report.stages["ingest"].output_count == 6
    assert report.stages["classify"].output_count == 6
    assert report.candidate_count == 6
    assert report.auto_selected == 6

    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings)
    assert edition.content_status == "normal"
    publish_edition(session, edition, now=CUTOFF)
    view = build_edition_view(session, edition)
    assert len(view.items) == 6
    assert all(item.access_text for item in view.items)
    assert all(item.links.get("project") for item in view.items)
    assert view.items[0].highlight is True


def test_replay_dry_run_preserves_published_snapshot(session, session_factory, temp_settings):
    disable_external_sources(temp_settings)
    report = _run(session, session_factory, temp_settings)
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings)
    publish_edition(session, edition, now=CUTOFF)
    before = repo_editions.list_items(session, edition.id, revision=1)
    snapshot_before = [dict(row.content_json) for row in before]
    published_before = edition.published_at

    replay = _run(
        session,
        session_factory,
        temp_settings,
        skip_ingest=True,
        dry_run=True,
        replay_of=report.run_id,
    )
    assert replay.run_id != report.run_id
    after = repo_editions.list_items(session, edition.id, revision=1)
    assert [dict(row.content_json) for row in after] == snapshot_before
    refreshed = repo_editions.get_edition(session, edition.id)
    assert refreshed.published_at == published_before
    assert refreshed.status == "published"


def test_source_failure_does_not_block_other_sources(session, session_factory, temp_settings):
    disable_external_sources(temp_settings)
    temp_settings.sources.sources["hn"].enabled = True

    def auth_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    report = _run(
        session,
        session_factory,
        temp_settings,
        transports={"hn": httpx.MockTransport(auth_fail), "__enrich__": html_transport()},
    )
    ingest = report.stages["ingest"]
    assert ingest.status.value == "partial"
    assert ingest.detail["sources"]["hn"]["status"] == "failed"
    assert report.auto_selected == 6  # 其他源正常


def test_all_sources_failed_produces_empty_edition(session, session_factory, temp_settings):
    disable_external_sources(temp_settings)
    temp_settings.sources.sources["hn"].enabled = True

    def auth_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    report = _run(
        session,
        session_factory,
        temp_settings,
        entries=[],
        transports={"hn": httpx.MockTransport(auth_fail), "__enrich__": html_transport()},
    )
    assert report.candidate_count == 0
    assert report.auto_selected == 0
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings)
    assert edition.content_status == "empty"


def test_republish_cooldown_blocks_same_product_next_day(session, session_factory, temp_settings):
    disable_external_sources(temp_settings)
    _run(session, session_factory, temp_settings)
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings)
    publish_edition(session, edition, now=CUTOFF)

    next_day = EDITION_DATE + timedelta(days=1)
    llm = StubLLM(session_factory, temp_settings)
    report = run_pipeline(
        session,
        next_day,
        settings=temp_settings,
        cutoff_at=CUTOFF + timedelta(days=1),
        llm=llm,
        manual_entries=manual_entries(6),
        transports={"__enrich__": html_transport()},
        owner="test",
    )
    # 同一批产品 30 天内不能凭普通重复入选
    assert report.auto_selected == 0
    assert report.candidate_count > 0  # 仍进入复核池供人工判断重要更新


def test_database_restart_keeps_published_edition(tmp_path, temp_settings):
    db_path = tmp_path / "restart.sqlite3"
    engine = make_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        disable_external_sources(temp_settings)
        report = _run(session, factory, temp_settings)
        edition = freeze_edition(session, EDITION_DATE, settings=temp_settings)
        publish_edition(session, edition, now=CUTOFF)
        edition_id = edition.id
        session.commit()
    engine.dispose()

    engine2 = make_engine(db_path)
    init_db(engine2)
    factory2 = make_session_factory(engine2)
    with factory2() as session:
        refreshed = repo_editions.get_edition(session, edition_id)
        assert refreshed is not None and refreshed.status == "published"
        view = build_edition_view(session, refreshed)
        assert len(view.items) == 6
    engine2.dispose()


def test_run_records_stages_and_versions(session, session_factory, temp_settings):
    disable_external_sources(temp_settings)
    report = _run(session, session_factory, temp_settings)
    run = repo_pipeline.get_pipeline_run(session, report.run_id)
    assert run.config_hash == temp_settings.config_hash
    assert run.prompt_hash == temp_settings.prompt_hash
    stages = {s.stage for s in repo_pipeline.list_stages(session, report.run_id)}
    assert {"ingest", "normalize", "classify", "enrich", "rank", "select"} <= stages


def test_manual_injection_rejects_private_urls(session, temp_settings):
    from viberadar.ingest import run_source

    report = run_source(
        session,
        "manual",
        since=CUTOFF - timedelta(hours=24),
        until=CUTOFF,
        settings=temp_settings,
        manual_entries=[
            {"url": "http://127.0.0.1:8080/secret", "title": "internal"},
            {"url": "http://169.254.169.254/latest/meta-data", "title": "metadata"},
        ],
    )
    assert report.created == 0
    assert all("拒绝不安全链接" in err for err in report.errors)
