"""采集仓储：源状态、水位、原始条目去重、热度快照（PRD M1）。"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.contracts import MetricSnapshot, RawItem, new_id

from .models import MetricSnapshotRow, RawItemRow, Source


def ensure_source(
    session: Session,
    key: str,
    label: str,
    tier: int = 1,
    enabled: bool = True,
    config_version: str | None = None,
) -> Source:
    row = session.get(Source, key)
    if row is None:
        row = Source(
            key=key, label=label, tier=tier, enabled=enabled, config_version=config_version
        )
        session.add(row)
        session.flush()
    else:
        row.label = label
        row.tier = tier
        row.config_version = config_version
    return row


def set_source_enabled(session: Session, key: str, enabled: bool, notes: str | None = None) -> None:
    row = session.get(Source, key)
    if row is None:
        raise KeyError(f"未知源: {key}")
    row.enabled = enabled
    if notes is not None:
        row.notes = notes


def get_watermark(session: Session, key: str) -> dict:
    row = session.get(Source, key)
    return dict(row.watermark or {}) if row else {}


def set_watermark(
    session: Session,
    key: str,
    watermark: dict,
    success_at: datetime | None = None,
) -> None:
    row = session.get(Source, key)
    if row is None:
        raise KeyError(f"未知源: {key}")
    row.watermark = watermark
    if success_at is not None:
        row.last_success_at = success_at
    session.flush()


def _content_hash(item: RawItem) -> str:
    payload = f"{item.title}\x00{item.description or ''}\x00{item.target_url or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upsert_raw_item(session: Session, item: RawItem) -> tuple[RawItemRow, bool]:
    """按 (source_key, external_id) 去重；返回 (行, 是否新建)。"""
    row = session.scalar(
        select(RawItemRow).where(
            RawItemRow.source_key == item.source_key,
            RawItemRow.external_id == item.external_id,
        )
    )
    created = False
    if row is None:
        row = RawItemRow(
            id=new_id("raw"),
            source_key=item.source_key,
            external_id=item.external_id,
            title=item.title,
            source_url=str(item.source_url),
            target_url=str(item.target_url) if item.target_url else None,
            description=item.description,
            author=item.author,
            published_at=item.published_at,
            first_fetched_at=item.fetched_at,
            last_fetched_at=item.fetched_at,
            content_hash=_content_hash(item),
            metadata_json=dict(item.raw_metadata),
            query_key=item.query_key,
        )
        session.add(row)
        session.flush()
        created = True
    else:
        row.last_fetched_at = item.fetched_at
        new_hash = _content_hash(item)
        if new_hash != row.content_hash:
            # 描述快照可更新；身份字段保持
            row.title = item.title
            row.description = item.description
            row.target_url = str(item.target_url) if item.target_url else row.target_url
            row.content_hash = new_hash
        if item.published_at is not None and row.published_at is None:
            row.published_at = item.published_at
        if item.query_key and not row.query_key:
            row.query_key = item.query_key

    if item.metrics:
        append_metrics(session, row.id, item.fetched_at, item.metrics)
    return row, created


def append_metrics(
    session: Session,
    raw_item_id: str,
    captured_at: datetime,
    metrics: dict,
) -> bool:
    """热度数据追加到独立快照（不覆盖历史，支持速度计算）。"""
    latest = session.scalar(
        select(MetricSnapshotRow)
        .where(MetricSnapshotRow.raw_item_id == raw_item_id)
        .order_by(MetricSnapshotRow.captured_at.desc())
        .limit(1)
    )
    if latest is not None and latest.metrics == metrics:
        return False
    session.add(
        MetricSnapshotRow(
            id=new_id("mtr"),
            raw_item_id=raw_item_id,
            captured_at=captured_at,
            metrics=dict(metrics),
        )
    )
    session.flush()
    return True


def record_metric_snapshot(session: Session, snapshot: MetricSnapshot) -> MetricSnapshotRow:
    row = MetricSnapshotRow(
        id=new_id("mtr"),
        raw_item_id=snapshot.raw_item_id,
        captured_at=snapshot.captured_at,
        metrics=dict(snapshot.metrics),
    )
    session.add(row)
    session.flush()
    return row


def get_raw_item(session: Session, raw_item_id: str) -> RawItemRow | None:
    return session.get(RawItemRow, raw_item_id)


def list_raw_items_between(
    session: Session,
    start: datetime,
    end: datetime,
    source_keys: list[str] | None = None,
) -> list[RawItemRow]:
    """窗口内条目：以来源发布时间优先，缺失用首次发现时间（PRD 4.3）。"""
    stmt = select(RawItemRow).where(
        ((RawItemRow.published_at >= start) & (RawItemRow.published_at < end))
        | ((RawItemRow.published_at.is_(None)) & (RawItemRow.first_fetched_at >= start) & (RawItemRow.first_fetched_at < end))
    )
    if source_keys:
        stmt = stmt.where(RawItemRow.source_key.in_(source_keys))
    return list(session.scalars(stmt.order_by(RawItemRow.first_fetched_at)))


def latest_metrics(session: Session, raw_item_id: str, limit: int = 5) -> list[MetricSnapshotRow]:
    return list(
        session.scalars(
            select(MetricSnapshotRow)
            .where(MetricSnapshotRow.raw_item_id == raw_item_id)
            .order_by(MetricSnapshotRow.captured_at.desc())
            .limit(limit)
        )
    )
