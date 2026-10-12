"""Hacker News (Show HN) 适配器（PRD 附录 B.1）。

主通道不加主题关键词；以发布行为获取窗口内 Show HN，降低漏检。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from viberadar.contracts import RawItem, utcnow

from .base import Adapter, FetchOutcome
from .fetch import validate_url_safety

MAX_RETRIES_5XX = 2


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("retry-after")
    if not value:
        return 1.0
    try:
        return min(30.0, float(value))
    except ValueError:
        return 1.0


class HNAdapter(Adapter):
    source_key = "hn"
    adapter_name = "hn"

    def fetch(self, since: datetime, until: datetime, watermark: dict) -> FetchOutcome:
        endpoint = self.config.get("endpoint", "https://hn.algolia.com/api/v1/search_by_date")
        validate_url_safety(endpoint)
        tags = self.config.get("tags", "show_hn")
        per_page = int(self.config.get("hits_per_page", 100))
        max_pages = int(self.config.get("max_pages", 10))
        timeout = float(self.config.get("request_timeout_seconds", 20))
        since_ts = int(since.timestamp())
        until_ts = int(until.timestamp())

        outcome = FetchOutcome(watermark={"window_end": until.astimezone(timezone.utc).isoformat()})
        fetched_at = utcnow()
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            for page in range(max_pages):
                params = {
                    "tags": tags,
                    "numericFilters": f"created_at_i>={since_ts},created_at_i<{until_ts}",
                    "hitsPerPage": per_page,
                    "page": page,
                }
                data = None
                for attempt in range(MAX_RETRIES_5XX + 1):
                    try:
                        response = client.get(endpoint, params=params)
                    except httpx.HTTPError as exc:
                        if attempt >= MAX_RETRIES_5XX:
                            outcome.errors.append(f"网络错误: {exc}")
                            outcome.complete = False
                            break
                        time.sleep(min(4.0, 2.0**attempt))
                        continue
                    outcome.request_count += 1
                    if response.status_code == 429:
                        wait = _retry_after_seconds(response)
                        if attempt >= MAX_RETRIES_5XX:
                            outcome.errors.append("429 限流，稍后重试（服从 Retry-After）")
                            outcome.complete = False
                            break
                        time.sleep(wait)
                        continue
                    if response.status_code >= 500:
                        if attempt >= MAX_RETRIES_5XX:
                            outcome.errors.append(f"5xx: {response.status_code}")
                            outcome.complete = False
                            break
                        time.sleep(min(4.0, 2.0**attempt))
                        continue
                    if response.status_code in (401, 403):
                        outcome.errors.append(f"鉴权失败: {response.status_code}（进入告警）")
                        outcome.complete = False
                        break
                    if response.status_code != 200:
                        outcome.errors.append(f"HTTP {response.status_code}")
                        outcome.complete = False
                        break
                    data = response.json()
                    break
                if data is None:
                    break

                hits = data.get("hits", [])
                for hit in hits:
                    item = self._to_item(hit, fetched_at)
                    if item is not None:
                        outcome.items.append(item)
                nb_pages = int(data.get("nbPages") or 0)
                if page + 1 >= nb_pages or not hits:
                    break
                if page + 1 >= max_pages:
                    outcome.complete = False
                    outcome.coverage_note = "达到分页上限，覆盖可能不完整"
        return outcome

    def _to_item(self, hit: dict, fetched_at: datetime) -> RawItem | None:
        object_id = str(hit.get("objectID") or "").strip()
        title = (hit.get("title") or hit.get("story_title") or "").strip()
        if not object_id or not title:
            return None
        created = hit.get("created_at_i")
        published_at = (
            datetime.fromtimestamp(int(created), tz=timezone.utc) if created else None
        )
        target_url = hit.get("url")
        if target_url:
            try:
                validate_url_safety(target_url)
            except ValueError:
                target_url = None
        return RawItem(
            source_key=self.source_key,
            external_id=object_id,
            source_url=f"https://news.ycombinator.com/item?id={object_id}",
            title=title,
            target_url=target_url,
            description=(hit.get("story_text") or None),
            author=hit.get("author"),
            published_at=published_at,
            fetched_at=fetched_at,
            metrics={
                "points": hit.get("points"),
                "num_comments": hit.get("num_comments"),
            },
            raw_metadata={
                "objectID": object_id,
                "tags": hit.get("_tags"),
            },
            query_key="show_hn",
        )
