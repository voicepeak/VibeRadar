"""GitHub 新仓库适配器（PRD 附录 B.2）。

采用多组 query 的分层发现；记录查询与覆盖限制，不称全量。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import httpx

from viberadar.contracts import RawItem, utcnow

from .base import Adapter, FetchOutcome
from .fetch import validate_url_safety

RESULT_CAP = 1000  # 单查询最多返回 1000 条（官方限制）


class GitHubAdapter(Adapter):
    source_key = "github"
    adapter_name = "github"

    def fetch(self, since: datetime, until: datetime, watermark: dict) -> FetchOutcome:
        endpoint = self.config.get(
            "endpoint", "https://api.github.com/search/repositories"
        )
        validate_url_safety(endpoint)
        per_page = int(self.config.get("per_page", 100))
        max_pages = int(self.config.get("max_pages", 5))
        timeout = float(self.config.get("request_timeout_seconds", 20))
        queries = self.config.get("queries") or []
        token = os.environ.get("GITHUB_TOKEN")

        outcome = FetchOutcome(
            watermark={"window_end": until.astimezone(timezone.utc).isoformat()}
        )
        fetched_at = utcnow()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            outcome.quota_note = "未配置 GITHUB_TOKEN；未认证 search 限额更低"

        since_date = since.astimezone(timezone.utc).strftime("%Y-%m-%d")
        cap_hit = False
        with httpx.Client(timeout=timeout, transport=self.transport, headers=headers) as client:
            for query_def in queries:
                query_key = query_def.get("query_key", "default")
                q = query_def.get("q", "").replace("{since_date}", since_date)
                if not q:
                    continue
                for page in range(1, max_pages + 1):
                    params = {
                        "q": q,
                        "sort": "created",
                        "order": "desc",
                        "per_page": per_page,
                        "page": page,
                    }
                    data = None
                    for attempt in range(3):
                        try:
                            response = client.get(endpoint, params=params)
                        except httpx.HTTPError as exc:
                            if attempt >= 2:
                                outcome.errors.append(f"[{query_key}] 网络错误: {exc}")
                                break
                            time.sleep(min(4.0, 2.0**attempt))
                            continue
                        outcome.request_count += 1
                        if response.status_code == 429:
                            wait = float(response.headers.get("retry-after") or 2)
                            if attempt >= 2:
                                outcome.errors.append(f"[{query_key}] 429 限流")
                                break
                            time.sleep(min(30.0, wait))
                            continue
                        if response.status_code in (401, 403):
                            outcome.errors.append(
                                f"[{query_key}] 鉴权失败: {response.status_code}（进入告警）"
                            )
                            break
                        if response.status_code != 200:
                            outcome.errors.append(
                                f"[{query_key}] HTTP {response.status_code}"
                            )
                            break
                        data = response.json()
                        break
                    if data is None:
                        break

                    if data.get("incomplete_results"):
                        cap_hit = True
                    total = int(data.get("total_count") or 0)
                    if total > RESULT_CAP and page == 1:
                        cap_hit = True
                        outcome.coverage_note = (
                            f"[{query_key}] total_count={total} 超过 API 1000 条上限，"
                            "按创建时间降序截断，覆盖不完整"
                        )
                    items = data.get("items") or []
                    for repo in items:
                        item = self._to_item(repo, fetched_at, query_key, since, until)
                        if item is not None:
                            outcome.items.append(item)
                    if len(items) < per_page:
                        break
                    if page * per_page >= min(total, RESULT_CAP):
                        break

        if cap_hit:
            outcome.complete = False
            if outcome.coverage_note is None:
                outcome.coverage_note = "存在不完整查询结果，覆盖受限"
        return outcome

    def _to_item(
        self,
        repo: dict,
        fetched_at: datetime,
        query_key: str,
        since: datetime,
        until: datetime,
    ) -> RawItem | None:
        repo_id = repo.get("id")
        full_name = repo.get("full_name") or ""
        html_url = repo.get("html_url") or ""
        if not repo_id or not full_name:
            return None
        created_at = repo.get("created_at")
        published_at = None
        if created_at:
            try:
                published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                published_at = None
        # 回库后再次按 UTC 边界过滤
        if published_at is not None and not (since <= published_at < until):
            return None
        owner = (repo.get("owner") or {}).get("login")
        return RawItem(
            source_key=self.source_key,
            external_id=str(repo_id),
            source_url=html_url,
            title=full_name,
            target_url=html_url,
            description=repo.get("description"),
            author=owner,
            published_at=published_at,
            fetched_at=fetched_at,
            metrics={
                "stars": repo.get("stargazers_count"),
                "forks": repo.get("forks_count"),
            },
            raw_metadata={
                "repo_id": repo_id,
                "full_name": full_name,
                "topics": repo.get("topics") or [],
                "language": repo.get("language"),
                "created_at": created_at,
            },
            query_key=query_key,
        )
