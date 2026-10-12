"""实体解析：原始条目 → 产品实体 + 发布事件（PRD M2/4.3）。

高置信自动合并：同一 repo ID；同一规范化入口（无冲突）。完全同名或语义相似
只产生待复核关系。发布事件 fingerprint 用源 ID，不能仅用标题。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.contracts import LaunchKind, utcnow
from viberadar.settings import Settings
from viberadar.store import repo_products
from viberadar.store.models import EditionItemRow, EditionRow, LaunchEventRow, ProductRow, RawItemRow

from .normalize import IdentityHints, extract_identity

SOURCE_EVENT_KIND = {
    "hn": LaunchKind.LAUNCH,
    "github": LaunchKind.REPO_CREATED,
    "manual": LaunchKind.DISCOVERY,
    "producthunt": LaunchKind.LAUNCH,
}


@dataclass
class ResolvedItem:
    raw_item: RawItemRow
    product: ProductRow
    event: LaunchEventRow
    hints: IdentityHints
    product_created: bool
    event_created: bool
    exclusion_reason: str | None = None


@dataclass
class ResolveReport:
    resolved: list[ResolvedItem]
    created_products: int = 0
    created_events: int = 0
    pending_relations: int = 0
    errors: list[str] | None = None


def resolve_entities(
    session: Session,
    raw_items: list[RawItemRow],
    settings: Settings,
    window_start: datetime,
    cutoff: datetime,
    now: datetime | None = None,
) -> ResolveReport:
    now = now or utcnow()
    late_days = settings.pipeline.window.late_discovery_days
    report = ResolveReport(resolved=[], errors=[])

    for raw in raw_items:
        entry_url = raw.target_url or raw.source_url
        hints = extract_identity(
            entry_url, raw.title, raw.metadata_json or {}, raw.source_key
        )

        product = _find_product(session, hints)
        product_created = False
        if product is None:
            name = _display_name(raw, hints)
            product = repo_products.create_product(
                session,
                name=name,
                canonical_url=raw.target_url or None,
                repo_id=hints.repo_id,
                canonical_key=hints.canonical_key,
                first_seen_at=raw.first_fetched_at,
                now=now,
            )
            product_created = True
            report.created_products += 1
        else:
            if hints.repo_id and not product.repo_id:
                product.repo_id = hints.repo_id
            if not product.canonical_url and raw.target_url:
                product.canonical_url = raw.target_url

        repo_products.link_product_source(
            session,
            product.id,
            raw.id,
            match_method="identity" if not product_created else "created",
            confidence=1.0 if hints.canonical_key else 0.5,
            now=now,
        )

        # 完全同名不同身份 → 待复核关系（不自动合并）
        if hints.identity_kind in ("url", "github_repo"):
            for other in repo_products.find_products_by_name(session, product.name):
                if other.id != product.id and other.canonical_key != product.canonical_key:
                    repo_products.add_pending_relation(
                        session,
                        product.id,
                        other.id,
                        relation_type="same_name",
                        signals={
                            "name": product.name,
                            "keys": [product.canonical_key, other.canonical_key],
                        },
                        now=now,
                    )
                    report.pending_relations += 1

        kind = SOURCE_EVENT_KIND.get(raw.source_key, LaunchKind.DISCOVERY)
        event_at = raw.published_at
        time_unconfirmed = event_at is None
        late_discovery = False
        exclusion: str | None = None
        if event_at is not None:
            if event_at < window_start:
                age = cutoff - event_at
                if age <= timedelta(days=late_days):
                    late_discovery = True
                else:
                    exclusion = "超过 7 天的旧工具不作为新品自动发布（PRD 4.3）"
        fingerprint = f"{raw.source_key}:{raw.external_id}"
        event, event_created = repo_products.create_launch_event(
            session,
            product_id=product.id,
            kind=kind.value,
            event_at=event_at,
            first_seen_at=raw.first_fetched_at,
            event_fingerprint=fingerprint,
            late_discovery=late_discovery,
            time_unconfirmed=time_unconfirmed,
        )
        if event_created:
            report.created_events += 1
            if exclusion and event.event_at is None:
                pass
        if exclusion:
            # 记录但标记状态，选择阶段排除
            event.status = "stale"
        repo_products.link_event_source(session, event.id, raw.id, now=now)

        report.resolved.append(
            ResolvedItem(
                raw_item=raw,
                product=product,
                event=event,
                hints=hints,
                product_created=product_created,
                event_created=event_created,
                exclusion_reason=exclusion,
            )
        )
    return report


def _find_product(session: Session, hints: IdentityHints) -> ProductRow | None:
    if hints.repo_id:
        found = repo_products.find_product_by_repo_id(session, hints.repo_id)
        if found is not None:
            return found
    if hints.canonical_key:
        return repo_products.find_product_by_canonical_key(session, hints.canonical_key)
    return None


def _display_name(raw: RawItemRow, hints: IdentityHints) -> str:
    if hints.repo_full_name:
        return hints.repo_full_name
    title = raw.title.strip()
    return title[:200] if title else (hints.host or raw.external_id)


def was_published_recently(
    session: Session,
    product_id: str,
    before: datetime,
    cooldown_days: int,
) -> bool:
    """已刊登产品 30 天内再次入选需有可验证的重要更新（PRD 4.3）。"""
    threshold = before - timedelta(days=cooldown_days)
    rows = session.scalars(
        select(EditionItemRow.id)
        .join(EditionRow, EditionRow.id == EditionItemRow.edition_id)
        .where(
            EditionItemRow.product_id == product_id,
            EditionRow.status == "published",
            EditionRow.published_at.is_not(None),
            EditionRow.published_at >= threshold,
        )
    ).all()
    return bool(rows)


def has_update_event(session: Session, product_id: str, event_id: str | None) -> bool:
    if event_id:
        event = session.get(LaunchEventRow, event_id)
        if event is not None and event.kind == LaunchKind.UPDATE.value:
            return True
    exists = session.scalar(
        select(LaunchEventRow.id).where(
            LaunchEventRow.product_id == product_id,
            LaunchEventRow.kind == LaunchKind.UPDATE.value,
        )
    )
    return exists is not None
