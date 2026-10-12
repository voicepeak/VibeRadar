"""Web 集成：页面、API、反馈、跳转与安全（PRD 4.6/9.1/M8）。"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from viberadar.compose import freeze_edition, publish_edition
from viberadar.store import get_session_factory, repo_editions, repo_pipeline, repo_products

NOW = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)
EDITION_DATE = date(2026, 10, 12)


@pytest.fixture()
def seeded(db_path, temp_settings):
    factory = get_session_factory(str(db_path))
    with factory() as session:
        for i in range(6):
            product = repo_products.create_product(
                session,
                f"WebTool{i}",
                None,
                None,
                f"url:https://example.com/web{i}",
                NOW,
            )
            content = {
                "name": f"WebTool{i}",
                "category": ["agent-hygiene", "coding-agent", "mcp-skill", "app-builder", "creative-toy", "editor-extension"][i % 6],
                "category_label": "编码 Agent",
                "one_liner": f"WebTool{i} 的一句话",
                "why_interesting": "值得看的理由",
                "mode": "demo",
                "steps": ["打开入口", "查看演示"],
                "access_text": "免费 · 需登录",
                "time_promise": None,
                "mobile_note": "手机体验未确认",
                "evidence_status": "doc_checked",
                "checked_at": NOW.isoformat(),
                "source_links": [{"label": "来源", "url": f"https://example.com/src{i}"}],
            }
            repo_pipeline.upsert_candidate(
                session,
                EDITION_DATE,
                "run_web",
                product.id,
                None,
                True,
                0.9 - i * 0.01,
                {},
                content,
            )
        edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
        publish_edition(session, edition, now=NOW)
        session.commit()
        return {"edition_id": edition.id, "factory": factory}


def _first_item(db_path):
    factory = get_session_factory(str(db_path))
    with factory() as session:
        edition = repo_editions.get_edition_by_date(session, EDITION_DATE, "Asia/Shanghai")
        items = repo_editions.list_items(session, edition.id, revision=1)
        return items[0].id, dict(items[0].content_json or {})


def test_unknown_path_renders_site_404(client):
    r = client.get("/definitely-not-a-page")
    assert r.status_code == 404
    assert "未找到" in r.text
    api = client.get("/api/v1/definitely-nope")
    assert api.status_code == 404
    payload = api.json()
    assert payload["code"] == "not_found"
    assert "request_id" in payload and payload["retryable"] is False


def test_recent_editions_entries_and_no_future_dates(client, seeded, db_path, temp_settings):
    """首页有最近期号入口；日期页往期回顾只含更早期号。"""
    factory = get_session_factory(str(db_path))
    with factory() as session:
        earlier = repo_editions.create_edition(session, date(2026, 10, 11), "Asia/Shanghai")
        repo_editions.transition(session, earlier, "reviewing")
        repo_editions.transition(session, earlier, "ready")
        repo_editions.mark_published(session, earlier, NOW)
        session.commit()

    home = client.get("/")
    assert "最近期号" in home.text
    assert "2026-10-11" in home.text and "2026-10-12" in home.text

    today_page = client.get("/d/2026-10-12")
    assert "往期回顾" in today_page.text and "2026-10-11" in today_page.text

    earlier_page = client.get("/d/2026-10-11")
    assert "往期回顾" not in earlier_page.text  # 更早一期没有往期
    assert "2026-10-12" not in earlier_page.text  # 不把更新期号当往期
    assert "暂时没有可回顾的往期" in earlier_page.text


def test_card_actions_simplified_and_reasons_on_feedback_page(client, seeded, db_path):
    item_id, _ = _first_item(db_path)
    edition = client.get("/d/2026-10-12")
    assert 'data-action="tried"' in edition.text
    assert 'data-action="save"' in edition.text
    assert 'data-action="interest"' not in edition.text
    assert "fail-reasons" not in edition.text
    assert f'href="/feedback/{item_id}"' in edition.text

    feedback = client.get(f"/feedback/{item_id}")
    assert "fail-reasons" in feedback.text
    assert re.search(r'data-reasons-for="[^"]+"\s+hidden', feedback.text)
    assert 'data-action="interest"' in feedback.text


def test_pages_render(client, seeded, db_path):
    home = client.get("/")
    assert home.status_code == 200
    assert "2026-10-12" in home.text
    assert "WebTool" in home.text

    edition = client.get("/d/2026-10-12")
    assert edition.status_code == 200
    assert "工具与工作流" in edition.text or "创意与实验" in edition.text
    assert "免费 · 需登录" in edition.text

    missing = client.get("/d/2026-10-13")
    assert missing.status_code == 404

    product_page = client.get("/p/webtool0")
    assert product_page.status_code == 200
    assert "历史期号" in product_page.text

    saved = client.get("/saved")
    assert saved.status_code == 200
    assert "localStorage" in saved.text or "本浏览器" in saved.text

    item_id, _ = _first_item(db_path)
    feedback = client.get(f"/feedback/{item_id}")
    assert feedback.status_code == 200
    assert "玩过了" in feedback.text


def test_edition_api_etag_and_404(client, seeded):
    r = client.get("/api/v1/editions/2026-10-12")
    assert r.status_code == 200
    payload = r.json()
    assert len(payload["items"]) == 6
    etag = r.headers["etag"]
    again = client.get("/api/v1/editions/2026-10-12", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert client.get("/api/v1/editions/2020-01-01").status_code == 404
    assert client.get("/api/v1/editions/not-a-date").status_code == 404


def test_events_dedup_and_rate_limit(client, seeded):
    body = {
        "client_event_id": str(uuid.uuid4()),
        "event_type": "edition_view",
        "ref": "web",
    }
    r1 = client.post("/api/v1/events", json=body)
    r2 = client.post("/api/v1/events", json=body)
    assert r1.status_code == 200 and r1.json()["deduped"] is False
    assert r2.status_code == 200 and r2.json()["deduped"] is True
    # ref 白名单
    bad = client.post("/api/v1/events", json={**body, "client_event_id": str(uuid.uuid4()), "ref": "evil"})
    assert bad.status_code == 400


def test_feedback_flow_and_refresh(client, seeded, db_path):
    item_id, _ = _first_item(db_path)
    assert client.put(f"/api/v1/feedback/{item_id}", json={"tried": True}).status_code == 200
    state = client.put(f"/api/v1/feedback/{item_id}", json={"rating_state": "interest"}).json()
    assert state["rating_state"] == "interest" and state["tried"] is True
    state = client.put(f"/api/v1/feedback/{item_id}", json={"rating_state": None}).json()
    assert state["rating_state"] is None
    refreshed = client.get(f"/api/v1/feedback/{item_id}")
    assert refreshed.json()["tried"] is True
    assert refreshed.headers.get("cache-control") == "private, no-store"


def test_put_feedback_requires_same_origin(app, seeded, db_path):
    raw_client = TestClient(app)  # 无 Origin/Referer
    item_id, _ = _first_item(db_path)
    r = raw_client.put(f"/api/v1/feedback/{item_id}", json={"tried": True})
    assert r.status_code == 403


def test_issue_submission(client, seeded, db_path):
    item_id, _ = _first_item(db_path)
    r = client.post(
        "/api/v1/issues",
        json={"edition_item_id": item_id, "issue_type": "pricing_wrong", "note": "官网收费"},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "new"


def test_go_redirect_and_unknown(client, seeded, db_path):
    item_id, content = _first_item(db_path)
    link_id = content["links"]["project"]
    r = client.get(f"/go/{link_id}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://")
    assert client.get("/go/lnk_missing", follow_redirects=False).status_code == 404


def test_product_merge_redirect(client, seeded, db_path):
    factory = get_session_factory(str(db_path))
    with factory() as session:
        a = repo_products.get_product_by_slug(session, "webtool0")
        b = repo_products.get_product_by_slug(session, "webtool1")
        old_slug = a.slug
        repo_products.merge_products(
            session, a.id, b.id, reason="同产品", operator="curator", rule_version="manual-v1", now=NOW
        )
        session.commit()
        new_slug = b.slug
    r = client.get(f"/p/{old_slug}", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"/p/{new_slug}"
    api = client.get(f"/api/v1/products/{old_slug}")
    assert api.json()["status"] == "merged"


def test_robots_and_sitemap(client, seeded):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Disallow: /saved" in robots.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert "/d/2026-10-12" in sitemap.text
    assert "/saved" not in sitemap.text


def test_drafts_not_public(db_path, temp_settings, client):
    factory = get_session_factory(str(db_path))
    with factory() as session:
        repo_editions.create_edition(session, date(2026, 10, 20), "Asia/Shanghai")
        session.commit()
    assert client.get("/d/2026-10-20").status_code == 404
    assert client.get("/api/v1/editions/2026-10-20").status_code == 404
