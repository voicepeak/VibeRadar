"""人工注入适配器（PRD 3.1/15.1）。

从 JSONL 文件或直接传入条目清单；拒绝私网 URL（E20）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from viberadar.contracts import RawItem, utcnow

from .base import Adapter, FetchOutcome
from .fetch import UnsafeURLError, validate_url_safety


class ManualAdapter(Adapter):
    source_key = "manual"
    adapter_name = "manual"

    def __init__(self, config: dict | None = None, transport=None, entries: list[dict] | None = None) -> None:
        super().__init__(config, transport)
        self._entries = entries

    def fetch(self, since: datetime, until: datetime, watermark: dict) -> FetchOutcome:
        outcome = FetchOutcome(watermark={"window_end": until.astimezone(timezone.utc).isoformat()})
        fetched_at = utcnow()
        entries = self._entries
        if entries is None:
            path = self.config.get("path")
            entries = []
            if path:
                path_obj = Path(path)
                if not path_obj.exists():
                    outcome.errors.append(f"人工注入文件不存在: {path}")
                    return outcome
                for line_no, line in enumerate(
                    path_obj.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        outcome.errors.append(f"第 {line_no} 行 JSON 非法: {exc}")

        for entry in entries:
            url = (entry.get("url") or entry.get("target_url") or "").strip()
            if not url:
                outcome.errors.append("人工条目缺少 url")
                continue
            try:
                validate_url_safety(url)
            except UnsafeURLError as exc:
                outcome.errors.append(f"拒绝不安全链接 {url}: {exc}（E20）")
                continue
            published_at = None
            if entry.get("published_at"):
                try:
                    published_at = datetime.fromisoformat(entry["published_at"])
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    outcome.errors.append(f"published_at 非法: {entry['published_at']}")
            external_id = entry.get("external_id") or hashlib.sha1(
                url.encode("utf-8")
            ).hexdigest()[:24]
            outcome.items.append(
                RawItem(
                    source_key=self.source_key,
                    external_id=external_id,
                    source_url=url,
                    title=(entry.get("title") or url).strip(),
                    target_url=url,
                    description=entry.get("description"),
                    author=entry.get("author"),
                    published_at=published_at,
                    fetched_at=fetched_at,
                    metrics=entry.get("metrics") or {},
                    raw_metadata={"manual": True, "operator": entry.get("operator")},
                    query_key="manual",
                )
            )
        return outcome
