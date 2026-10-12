"""Product Hunt 日榜适配器：解析与错误路径（离线 mock transport）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from viberadar.ingest import ProductHuntLeaderboardAdapter, parse_leaderboard_html

CST = timezone(timedelta(hours=8))
UNTIL = datetime(2026, 10, 12, 7, 30, tzinfo=CST)  # 太平洋时间 2026-10-11

FIXTURE = """
<html><body>
<a href="/products/alpha"><span class="absolute inset-0" data-target="true"></span>1. Alpha Tool</a>
<svg><path d="M0 0"></path></svg>
<span class="mt-0.5 block text-base text-secondary">Great &amp; fast alpha thing</span>
<a href="/products/beta"><span class="absolute inset-0" data-target="true"></span>2. Beta App</a>
<span class="mt-0.5 block text-base text-secondary">第二个产品的短语</span>
<a href="/products/lovable">Lovable</a>
</body></html>
"""


def test_parse_leaderboard_html_filters_unranked_links():
    entries = parse_leaderboard_html(FIXTURE)
    assert [entry["slug"] for entry in entries] == ["alpha", "beta"]
    assert entries[0] == {
        "slug": "alpha",
        "rank": 1,
        "name": "Alpha Tool",
        "tagline": "Great & fast alpha thing",
    }
    assert entries[1]["tagline"] == "第二个产品的短语"


def test_adapter_fetch_builds_items_and_requests_board_date():
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=FIXTURE, headers={"content-type": "text/html"})

    adapter = ProductHuntLeaderboardAdapter(
        config={"timezone": "America/Los_Angeles"},
        transport=httpx.MockTransport(handler),
    )
    outcome = adapter.fetch(since=UNTIL - timedelta(hours=24), until=UNTIL, watermark={})
    assert outcome.complete
    assert not outcome.errors
    assert seen_urls and seen_urls[0].endswith("/leaderboard/daily/2026/10/11")
    assert len(outcome.items) == 2
    first = outcome.items[0]
    assert first.external_id == "2026-10-11:alpha"
    assert first.title == "Alpha Tool"
    assert first.description == "Great & fast alpha thing"
    assert first.metrics["rank"] == 1
    assert str(first.source_url).endswith("/products/alpha")
    assert first.query_key == "ph_leaderboard"


def test_adapter_http_error_marks_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    adapter = ProductHuntLeaderboardAdapter(transport=httpx.MockTransport(handler))
    outcome = adapter.fetch(since=UNTIL - timedelta(hours=24), until=UNTIL, watermark={})
    assert not outcome.complete
    assert outcome.items == []
    assert outcome.errors


def test_adapter_structure_change_marks_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nothing here</html>", headers={"content-type": "text/html"})

    adapter = ProductHuntLeaderboardAdapter(transport=httpx.MockTransport(handler))
    outcome = adapter.fetch(since=UNTIL - timedelta(hours=24), until=UNTIL, watermark={})
    assert not outcome.complete
    assert any("未解析到排名条目" in error for error in outcome.errors)
