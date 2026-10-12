"""Product Hunt 日榜公开页适配器（默认停用；启用前须完成条款与 robots 核查）。

只解析公开榜单页的排名条目（名称/短语/链接），不调用或绕过官方 API；
页面按太平洋时间的自然日组织，条目时间依赖源页面，无逐条发布时间。
页面结构变化时返回 incomplete 并进入告警，不静默降级为空结果。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from html import unescape
from zoneinfo import ZoneInfo

import httpx

from viberadar.contracts import RawItem, utcnow

from .base import Adapter, FetchOutcome
from .fetch import FetchTooLarge, UnsafeURLError, safe_get, validate_url_safety

DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_ENDPOINT = "https://www.producthunt.com/leaderboard/daily/{year}/{month}/{day}"
TAGLINE_WINDOW = 900

_ANCHOR_RE = re.compile(r'<a[^>]+href="/products/([^"]+)"[^>]*>(.*?)</a>', re.S)
_RANK_RE = re.compile(r"^(\d{1,3})\.\s+(.+)$")
_TAGLINE_RE = re.compile(r'<span[^>]*class="[^"]*text-second[^"]*"[^>]*>(.*?)</span>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_leaderboard_html(page_html: str) -> list[dict]:
    """解析榜单页：排名条目形如 <a href="/products/{slug}">N. 名称</a> + 相邻短语。"""
    entries: list[dict] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(page_html):
        slug = match.group(1).strip()
        if not slug or slug in seen:
            continue
        inner = _plain_text(match.group(2))
        rank_match = _RANK_RE.match(inner)
        if not rank_match:
            continue  # 无排名的侧栏/推荐链接不计入榜单
        tagline = None
        window = page_html[match.end(): match.end() + TAGLINE_WINDOW]
        tagline_match = _TAGLINE_RE.search(window)
        if tagline_match:
            tagline = _plain_text(tagline_match.group(1)) or None
        entries.append(
            {
                "slug": slug,
                "rank": int(rank_match.group(1)),
                "name": rank_match.group(2).strip(),
                "tagline": tagline,
            }
        )
        seen.add(slug)
    return entries


class ProductHuntLeaderboardAdapter(Adapter):
    source_key = "producthunt_leaderboard"
    adapter_name = "producthunt_leaderboard"

    def fetch(self, since: datetime, until: datetime, watermark: dict) -> FetchOutcome:
        tz_name = self.config.get("timezone", DEFAULT_TIMEZONE)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 —— 时区配置错误退回默认，不阻断采集
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        board_date: date = until.astimezone(tz).date()
        template = self.config.get("endpoint", DEFAULT_ENDPOINT)
        endpoint = template.format(
            year=board_date.year, month=board_date.month, day=board_date.day
        )
        validate_url_safety(endpoint)
        timeout = float(self.config.get("request_timeout_seconds", 20))

        outcome = FetchOutcome(
            watermark={
                "window_end": until.astimezone(timezone.utc).isoformat(),
                "leaderboard_date": board_date.isoformat(),
            }
        )
        fetched_at = utcnow()
        headers = {
            "user-agent": "VibeRadarBot/0.1 (research; contact via repository)",
            "accept": "text/html",
        }
        try:
            response = safe_get(
                endpoint,
                transport=self.transport,
                timeout=timeout,
                max_bytes=2_000_000,
                headers=headers,
            )
        except (httpx.HTTPError, FetchTooLarge, UnsafeURLError) as exc:
            outcome.errors.append(f"抓取失败: {exc}")
            outcome.complete = False
            return outcome
        outcome.request_count = 1
        if response.status_code != 200:
            outcome.errors.append(f"HTTP {response.status_code}")
            outcome.complete = False
            return outcome

        entries = parse_leaderboard_html(response.text or "")
        if not entries:
            outcome.errors.append("榜单页未解析到排名条目（页面结构可能变化，进入告警）")
            outcome.complete = False
            return outcome

        for entry in entries:
            outcome.items.append(self._to_item(entry, board_date, fetched_at))
        return outcome

    def _to_item(self, entry: dict, board_date: date, fetched_at: datetime) -> RawItem:
        slug = entry["slug"]
        return RawItem(
            source_key=self.source_key,
            external_id=f"{board_date.isoformat()}:{slug}",
            source_url=f"https://www.producthunt.com/products/{slug}",
            title=entry["name"],
            target_url=None,
            description=entry["tagline"],
            author=None,
            published_at=None,  # 榜单页不提供逐条发布时间
            fetched_at=fetched_at,
            metrics={"rank": entry["rank"]},
            raw_metadata={"slug": slug, "leaderboard_date": board_date.isoformat()},
            query_key="ph_leaderboard",
        )
