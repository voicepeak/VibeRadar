"""Incremental analysis, deduplication, cooldown, ranking and SSR/API parity."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import pytest
from sqlalchemy import func, select
from viberadar.contracts import utcnow
from viberadar.pipeline.feed import reserve_refresh, execute_refresh, feed_view, refresh_feed, rerank
from viberadar.store import repo_ops
from viberadar.store.models import FeedItemRow, RefreshRunRow, RawItemRow, EnrichmentRow, EvidenceRow
from tests.helpers import StubLLM, disable_external_sources, html_transport


def populate(factory, settings, count=3):
    disable_external_sources(settings)
    now = utcnow()
    entries = [{"url": f"https://example.com/feed{i}", "title": f"Feed Tool {i}",
                "description": "New creative AI tool with demo and useful workflow",
                "published_at": (now-timedelta(hours=1)).isoformat()} for i in range(count)]
    gateway = StubLLM(factory, settings)
    reservation = reserve_refresh(factory, "cli", now)
    execute_refresh(factory, settings, reservation["run_id"], llm=gateway,
                    manual_entries=entries, transports={"__enrich__": html_transport()})
    return reservation["run_id"], gateway, entries, now


def test_undated_discovery_is_analyzed_in_the_first_refresh(session_factory, temp_settings):
    disable_external_sources(temp_settings)
    llm = StubLLM(session_factory, temp_settings)
    run = reserve_refresh(session_factory)
    execute_refresh(session_factory, temp_settings, run["run_id"], llm=llm,
                    manual_entries=[{"url": "https://example.com/undated", "title": "Undated Tool"}],
                    transports={"__enrich__": html_transport()})
    with session_factory() as session:
        result = session.get(RefreshRunRow, run["run_id"])
        assert result.new_items == 1 and result.status == "analyzed", result.error
        assert session.scalar(select(FeedItemRow)).state == "active"


def test_incremental_zero_llm_and_unique_product(session_factory, temp_settings):
    run_id, llm, entries, now = populate(session_factory, temp_settings)
    with session_factory() as s:
        run = s.get(RefreshRunRow, run_id)
        assert run.status == "analyzed", run.error
        assert run.new_items == 3 and run.llm_calls == len(llm.calls)
        rows = list(s.scalars(select(FeedItemRow)))
        assert len(rows) == 3
        before = {r.id: r.content_json for r in rows}
        enrichments = s.scalar(select(func.count()).select_from(EnrichmentRow))
        evidence = s.scalar(select(func.count()).select_from(EvidenceRow))
    llm.calls.clear()
    reservation = reserve_refresh(session_factory, "cli", now+timedelta(minutes=4))
    execute_refresh(session_factory, temp_settings, reservation["run_id"], llm=llm,
                    manual_entries=entries, transports={"__enrich__": html_transport()})
    with session_factory() as s:
        run = s.get(RefreshRunRow, reservation["run_id"])
        assert run.status == "no_changes", run.error
        assert run.llm_calls == 0 and not llm.calls
        assert {r.id: r.content_json for r in s.scalars(select(FeedItemRow))} == before
        assert s.scalar(select(func.count()).select_from(RawItemRow)) == 3
        assert s.scalar(select(func.count()).select_from(EnrichmentRow)) == enrichments
        assert s.scalar(select(func.count()).select_from(EvidenceRow)) == evidence
        assert run.delivery == "pending(TODO feishu)"


def test_refresh_reservation_race_and_cooldown(session_factory, temp_settings):
    now = utcnow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve_refresh(session_factory, now=now), range(2)))
    assert sum(bool(r.get("created")) for r in results) == 1
    assert len({r["run_id"] for r in results}) == 1
    disable_external_sources(temp_settings)
    execute_refresh(session_factory, temp_settings, results[0]["run_id"], skip_ingest=True)
    assert reserve_refresh(session_factory, now=now+timedelta(seconds=1))["status"] == "cooldown"


def test_budget_and_no_llm_leave_pending(session_factory, temp_settings):
    disable_external_sources(temp_settings)
    with session_factory() as s:
        repo_ops.record_cost(s, "x", 1, 0, 1, "v", Decimal("999"))
        s.commit()
    run_id, llm, _, now = populate(session_factory, temp_settings, 1)
    with session_factory() as s:
        assert s.get(RefreshRunRow, run_id).budget_exhausted
        assert s.scalar(select(FeedItemRow)).state == "pending"
    assert not llm.calls
    reservation = reserve_refresh(session_factory, now=now+timedelta(minutes=4))
    execute_refresh(session_factory, temp_settings, reservation["run_id"], skip_ingest=True, no_llm=True, llm=llm)
    assert not llm.calls


def test_deep_limit_and_backlog(session_factory, temp_settings):
    temp_settings.pipeline.limits.deep_enrich_limit = 2
    run_id, llm, _, now = populate(session_factory, temp_settings, 3)
    with session_factory() as s:
        assert s.get(RefreshRunRow, run_id).new_items == 2
        assert s.scalar(select(func.count()).select_from(FeedItemRow).where(FeedItemRow.state == "pending")) == 1
    second = reserve_refresh(session_factory, now=now+timedelta(minutes=4))
    execute_refresh(session_factory, temp_settings, second["run_id"], llm=llm, skip_ingest=True,
                    transports={"__enrich__": html_transport()})
    with session_factory() as s:
        assert s.get(RefreshRunRow, second["run_id"]).new_items == 1


def test_sections_dedupe_sticky_archive_and_cursor(session_factory, temp_settings):
    _, _, _, now = populate(session_factory, temp_settings, 25)
    with session_factory() as s:
        rows = list(s.scalars(select(FeedItemRow)))
        for i, row in enumerate(rows):
            row.event_at = now - timedelta(hours=60)
            row.must_read_until = None
            row.signals = {"signals": {"source_signal": {"value": .9, "missing": False},
                                       "velocity": {"value": .9 if i == 0 else .7, "missing": False}}}
        s.flush()
        view = feed_view(s, temp_settings, now=now)
        ids = [x["id"] for section in view["sections"] for x in section["items"]]
        assert len(set(ids)) == len(ids)
        assert len(view["sections"][1]["items"]) == 1
        assert view["next_cursor"]
        page2 = feed_view(s, temp_settings, cursor=view["next_cursor"], now=now)
        more = [x["id"] for x in page2["sections"][-1]["items"]]
        assert not set(ids) & set(more)
        with pytest.raises(ValueError):
            feed_view(s, temp_settings, cursor="invalid")
        rows[0].event_at = now-timedelta(days=8)
        assert feed_view(s, temp_settings, now=now)["total"] == 24


def test_ssr_feed_api_refresh_and_no_js(client, app, temp_settings):
    run_id, _, _, _ = populate(app.state.session_factory, temp_settings, 2)
    home = client.get("/")
    assert home.status_code == 200 and 'action="/refresh"' in home.text
    assert 'class="card-details"' in home.text and "Feed Tool" in home.text
    data = client.get("/api/v1/feed").json()
    assert data["total"] == 2 and "Feed Tool" in data["html"]
    assert "Must read" in client.get("/?lang=en").text
    assert client.post("/api/v1/refresh").json()["status"] == "cooldown"
    assert client.post("/api/v1/refresh", headers={"origin": "https://other.test"}).status_code == 403
    assert client.get("/api/v1/feed?cursor=garbage").status_code == 400
    assert client.get("/api/v1/refresh/unknown").status_code == 404
    cooldown=client.post("/refresh",follow_redirects=True)
    assert "Recently refreshed" in cooldown.text and cooldown.status_code==200


def test_velocity_above_one_and_sticky_must_read(session_factory, temp_settings):
    from viberadar.store.models import MetricSnapshotRow
    from viberadar.contracts import new_id
    populate(session_factory, temp_settings, 6)
    now = utcnow()
    with session_factory() as s:
        rows = list(s.scalars(select(FeedItemRow)))
        for i, row in enumerate(rows):
            for hours, stars in ((-2, 100), (0, 100+(i+1)*50)):
                s.add(MetricSnapshotRow(id=new_id("met"), raw_item_id=row.raw_item_id,
                    captured_at=now+timedelta(hours=hours), metrics={"stars": stars}))
        rows[0].must_read_until = now+timedelta(hours=12)
        sticky = rows[0].must_read_until
        s.flush()
        rerank(s, temp_settings, now)
        assert rows[0].must_read_until == sticky
        hottest = rows[-1]
        assert hottest.signals["signals"]["velocity"]["value"] >= .8
        assert hottest.signals["signals"]["source_signal"]["value"] >= .6
        view = feed_view(s, temp_settings, section="trending", now=now)
        assert any(x["id"] == hottest.id for x in view["sections"][0]["items"])


def test_existing_enrichment_reused_without_fetch_or_llm(session_factory, temp_settings):
    from sqlalchemy import delete
    _, llm, _, now = populate(session_factory, temp_settings, 2)
    with session_factory() as s:
        s.execute(delete(FeedItemRow))  # Simulate archives that were enriched before Feed existed.
        s.commit()
    llm.calls.clear()
    r = reserve_refresh(session_factory, now=now+timedelta(minutes=4))
    import httpx
    def forbidden(request):
        raise AssertionError("Existing product must not be fetched again")
    execute_refresh(session_factory, temp_settings, r["run_id"], skip_ingest=True,
                    llm=llm, transports={"__enrich__": httpx.MockTransport(forbidden)})
    with session_factory() as s:
        run = s.get(RefreshRunRow, r["run_id"])
        assert run.new_items == 2 and run.llm_calls == 0, run.error
    assert not llm.calls


def test_old_active_hn_metrics_still_sampled_when_unchanged(session_factory, temp_settings):
    import httpx
    from viberadar.store.models import Source, MetricSnapshotRow
    from viberadar.pipeline.feed import refresh_metrics
    run_id, _, _, now = populate(session_factory, temp_settings, 1)
    with session_factory() as s:
        s.add(Source(key="hn", label="HN", enabled=True))
        s.flush()
        row = s.scalar(select(FeedItemRow))
        raw = s.get(RawItemRow, row.raw_item_id)
        raw.source_key, raw.external_id = "hn", "12345"
        row.event_at = now-timedelta(hours=60)  # Outside the discovery overlap, inside Trending's window.
        s.commit()
    temp_settings.sources.sources["hn"].enabled = True
    r = reserve_refresh(session_factory, now=now+timedelta(minutes=4))
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"points": 342, "children": []}))
    with session_factory() as s:
        run = s.get(RefreshRunRow, r["run_id"])
        refresh_metrics(s, temp_settings, run, transport)
        refresh_metrics(s, temp_settings, run, transport)
        assert s.scalar(select(func.count()).select_from(MetricSnapshotRow)) == 2
        assert s.scalar(select(FeedItemRow)).content_json["name"].startswith("Feed Tool")


def test_reused_enrichment_still_rejects_spam(session_factory, temp_settings):
    from sqlalchemy import delete
    from viberadar.store.models import ClassificationRow
    _, llm, _, now = populate(session_factory, temp_settings, 1)
    with session_factory() as s:
        classification = s.scalar(select(ClassificationRow))
        classification.spam_score = .9
        s.execute(delete(FeedItemRow))
        s.commit()
    llm.calls.clear()
    reservation = reserve_refresh(session_factory, now=now+timedelta(minutes=4))
    execute_refresh(session_factory, temp_settings, reservation["run_id"], skip_ingest=True, llm=llm)
    with session_factory() as s:
        assert s.scalar(select(FeedItemRow)).state == "hidden"
        assert s.get(RefreshRunRow,reservation["run_id"]).llm_calls == 0


@pytest.mark.parametrize("near_budget", [False, True])
def test_feed_real_gateway_transport_and_budget(session_factory, temp_settings, near_budget):
    import json, httpx
    from viberadar.llm import LLMConfig, LLMProfile, LLMPrice, LLMGateway, load_store
    disable_external_sources(temp_settings)
    load_store(temp_settings).save(LLMConfig(active="test", profiles={"test": LLMProfile(name="test",
        api_key="sk-fixture",base_url="https://llm.test/v1", model_fast="test-fast",model_strong="test-strong",
        price=LLMPrice(input=1,output=2))}))
    if near_budget:
        temp_settings.pricing.budget["daily_hard_cap_usd"] = .001
    fake = StubLLM(session_factory,temp_settings)
    seen = []
    def handler(request):
        seen.append(request)
        envelope = json.loads(request.content)
        payload = json.loads(envelope["messages"][-1]["content"].split("输入数据：\n",1)[1])
        phase = "classify" if "items" in payload else "guide" if "product" in payload else "interest"
        result = fake.chat_json(prompt_name=phase,system="",payload=payload,model="fast")
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(result.data)}}],
            "usage":{"prompt_tokens":100,"completion_tokens":100}})
    gateway = LLMGateway(session_factory,temp_settings,provider="config",transport=httpx.MockTransport(handler))
    now = utcnow()
    entry = {"url":"https://example.com/gateway", "title":"Gateway sample", "description":"New product for AI workflows",
             "published_at":(now-timedelta(hours=1)).isoformat()}
    r = reserve_refresh(session_factory,now=now)
    execute_refresh(session_factory,temp_settings,r["run_id"],manual_entries=[entry],llm=gateway,
                    transports={"__enrich__":html_transport()})
    with session_factory() as s:
        run = s.get(RefreshRunRow,r["run_id"])
        assert run.status != "failed", run.error
        if near_budget:
            assert run.budget_exhausted and run.llm_calls == 0 and not seen
            assert s.scalar(select(FeedItemRow)).state == "pending"
        else:
            assert run.new_items == 1 and run.llm_calls == len(seen) == 3
            assert s.scalar(select(FeedItemRow)).state == "active"
            assert repo_ops.daily_cost(s) > 0


def test_future_publications_not_displayed(session_factory,temp_settings):
    _,_,_,now=populate(session_factory,temp_settings,1)
    with session_factory() as s:
        row=s.scalar(select(FeedItemRow))
        row.event_at=now+timedelta(days=1)
        s.flush()
        assert feed_view(s,temp_settings,now=now)["total"]==0
