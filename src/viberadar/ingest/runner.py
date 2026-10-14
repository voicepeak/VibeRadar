"""采集编排：按源执行、水位推进、逐源报告（PRD M1/5.1）。

一个源失败不能中止其余源；水位仅在成功落库后推进；
中断恢复从持久化水位补采，重叠窗口 2 小时以唯一键去重。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from viberadar.contracts import StageStatus, utcnow
from viberadar.settings import Settings, get_settings

from .base import Adapter
from .github import GitHubAdapter
from .hn import HNAdapter
from .manual import ManualAdapter
from .producthunt import ProductHuntAdapter
from .producthunt_leaderboard import ProductHuntLeaderboardAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    "hn": HNAdapter,
    "github": GitHubAdapter,
    "manual": ManualAdapter,
    "producthunt": ProductHuntAdapter,
    "producthunt_leaderboard": ProductHuntLeaderboardAdapter,
}


@dataclass
class IngestReport:
    source_key: str
    status: StageStatus
    raw_fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    watermark: dict = field(default_factory=dict)
    request_count: int = 0
    quota_note: str | None = None
    coverage_note: str | None = None
    duration_ms: int = 0


def build_adapter(
    adapter_name: str,
    config: dict,
    transport=None,
    manual_entries: list[dict] | None = None,
) -> Adapter:
    if adapter_name not in ADAPTERS:
        raise ValueError(f"未实现的适配器: {adapter_name}（配置不能自动产生协议）")
    cls = ADAPTERS[adapter_name]
    if cls is ManualAdapter:
        return ManualAdapter(config, transport, entries=manual_entries)
    return cls(config, transport)


def run_source(
    session: Session,
    source_key: str,
    since: datetime,
    until: datetime,
    settings: Settings | None = None,
    transport=None,
    manual_entries: list[dict] | None = None,
    release_transactions: bool = False,
) -> IngestReport:
    from viberadar.store import repo_ingest

    settings = settings or get_settings()
    source_def = settings.sources.sources.get(source_key)
    if source_def is None:
        raise KeyError(f"未知源: {source_key}")
    if not source_def.enabled and source_def.adapter != "manual":
        return IngestReport(
            source_key=source_key,
            status=StageStatus.SKIPPED,
            errors=["源已停用"],
        )

    repo_ingest.ensure_source(
        session,
        source_key,
        source_def.label,
        tier=source_def.tier,
        enabled=source_def.enabled,
        config_version=settings.config_hash,
    )
    watermark = repo_ingest.get_watermark(session, source_key)
    adapter = build_adapter(source_def.adapter, source_def.config, transport, manual_entries)
    started = utcnow()
    if release_transactions:
        session.commit()  # Feed polling must remain available during slow source requests.
    outcome = adapter.fetch(since, until, watermark)
    created = updated = 0
    for item in outcome.items:
        _, is_new = repo_ingest.upsert_raw_item(session, item)
        if is_new:
            created += 1
        else:
            updated += 1

    if outcome.status == StageStatus.SUCCESS:
        new_watermark = {**watermark, **outcome.watermark}
        repo_ingest.set_watermark(session, source_key, new_watermark, success_at=utcnow())
    duration = int((utcnow() - started).total_seconds() * 1000)
    return IngestReport(
        source_key=source_key,
        status=outcome.status,
        raw_fetched=len(outcome.items),
        created=created,
        updated=updated,
        errors=list(outcome.errors),
        watermark=outcome.watermark,
        request_count=outcome.request_count,
        quota_note=outcome.quota_note,
        coverage_note=outcome.coverage_note,
        duration_ms=duration,
    )


def run_all_sources(
    session: Session,
    since: datetime,
    until: datetime,
    settings: Settings | None = None,
    only: list[str] | None = None,
    transports: dict | None = None,
    manual_entries: list[dict] | None = None,
    release_transactions: bool = False,
    on_source=None,
) -> list[IngestReport]:
    settings = settings or get_settings()
    keys = only or [s.key for s in settings.sources.enabled()]
    reports: list[IngestReport] = []
    for key in keys:
        if on_source:
            on_source()
        try:
            report = run_source(
                session,
                key,
                since,
                until,
                settings=settings,
                transport=(transports or {}).get(key),
                manual_entries=manual_entries if key == "manual" else None,
                release_transactions=release_transactions,
            )
        except Exception as exc:  # noqa: BLE001 —— 单源失败不中止其余源
            report = IngestReport(
                source_key=key,
                status=StageStatus.FAILED,
                errors=[f"{type(exc).__name__}: {exc}"],
            )
        reports.append(report)
        if release_transactions:
            session.commit()
        if on_source:
            on_source()
    return reports


def effective_window(
    settings: Settings, until: datetime, resumed_from: datetime | None = None
) -> datetime:
    """水位向前重叠 2 小时；中断恢复从持久化水位补采（PRD 5.1）。"""
    overlap = timedelta(hours=settings.pipeline.window.watermark_overlap_hours)
    if resumed_from is not None:
        return min(resumed_from, until - overlap)
    return until - timedelta(hours=settings.pipeline.window.main_hours) - overlap
