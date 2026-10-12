"""Product Hunt 适配器（PRD 附录 B.3；默认停用）。

凭据与条款核查通过后启用；无凭据/授权不清晰时停用，不把榜单页解析
作为绕过 API 限制的自动兜底。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from viberadar.contracts import RawItem, utcnow

from .base import Adapter, FetchOutcome
from .fetch import validate_url_safety

QUERY = """
query LaunchWindow($postedAfter: DateTime!, $postedBefore: DateTime!, $cursor: String) {
  posts(order: NEWEST, postedAfter: $postedAfter, postedBefore: $postedBefore,
        first: 50, after: $cursor) {
    edges { node { id name tagline url website votesCount createdAt
                   topics { edges { node { name } } } } cursor }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class ProductHuntAdapter(Adapter):
    source_key = "producthunt"
    adapter_name = "producthunt"

    def fetch(self, since: datetime, until: datetime, watermark: dict) -> FetchOutcome:
        outcome = FetchOutcome(watermark={"window_end": until.astimezone(timezone.utc).isoformat()})
        token = os.environ.get("PRODUCTHUNT_TOKEN")
        if not token:
            outcome.errors.append("缺少 PRODUCTHUNT_TOKEN；源保持停用（不绕过限制）")
            outcome.complete = False
            return outcome
        endpoint = self.config.get(
            "endpoint", "https://api.producthunt.com/v2/api/graphql"
        )
        validate_url_safety(endpoint)
        timeout = float(self.config.get("request_timeout_seconds", 20))
        fetched_at = utcnow()
        cursor = None
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=timeout, transport=self.transport, headers=headers) as client:
            while True:
                variables = {
                    "postedAfter": since.isoformat(),
                    "postedBefore": until.isoformat(),
                    "cursor": cursor,
                }
                try:
                    response = client.post(
                        endpoint, json={"query": QUERY, "variables": variables}
                    )
                except httpx.HTTPError as exc:
                    outcome.errors.append(f"网络错误: {exc}")
                    outcome.complete = False
                    break
                outcome.request_count += 1
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    outcome.errors.append(f"429 限流（retry-after={retry_after}）")
                    outcome.complete = False
                    break
                if response.status_code in (401, 403):
                    outcome.errors.append(f"鉴权失败: {response.status_code}（进入告警）")
                    outcome.complete = False
                    break
                if response.status_code != 200:
                    outcome.errors.append(f"HTTP {response.status_code}")
                    outcome.complete = False
                    break
                data = response.json()
                if data.get("errors"):
                    outcome.errors.append(f"GraphQL 错误: {data['errors'][:1]}")
                    outcome.complete = False
                    break
                posts = data.get("data", {}).get("posts", {})
                edges = posts.get("edges") or []
                for edge in edges:
                    item = self._to_item(edge.get("node") or {}, fetched_at)
                    if item is not None:
                        outcome.items.append(item)
                page_info = posts.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")
        return outcome

    def _to_item(self, node: dict, fetched_at: datetime) -> RawItem | None:
        post_id = node.get("id")
        name = (node.get("name") or "").strip()
        if not post_id or not name:
            return None
        created_raw = node.get("createdAt")
        published_at = None
        if created_raw:
            try:
                published_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                published_at = None
        website = node.get("website") or node.get("url")
        return RawItem(
            source_key=self.source_key,
            external_id=str(post_id),
            source_url=node.get("url") or f"https://www.producthunt.com/posts/{post_id}",
            title=name,
            target_url=website,
            description=node.get("tagline"),
            author=None,
            published_at=published_at,
            fetched_at=fetched_at,
            metrics={"votes": node.get("votesCount")},
            raw_metadata={"topics": node.get("topics")},
            query_key="ph_window",
        )
