"""产品实体仓储：产品、发布事件、来源关联、证据、合并（PRD M2）。"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.contracts import Evidence, MergeAction, new_id, utcnow

from .models import (
    EvidenceRow,
    EventSourceRow,
    LaunchEventRow,
    MergeActionRow,
    PendingRelationRow,
    ProductRow,
    ProductSourceRow,
    RawItemRow,
    SlugRedirectRow,
)


def slugify(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).strip().lower()
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:120] or "product"


def unique_slug(session: Session, base: str) -> str:
    candidate = base
    n = 2
    while session.scalar(select(ProductRow.id).where(ProductRow.slug == candidate)):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def find_product_by_canonical_key(session: Session, key: str) -> ProductRow | None:
    return session.scalar(
        select(ProductRow).where(ProductRow.canonical_key == key, ProductRow.status == "active")
    )


def find_product_by_repo_id(session: Session, repo_id: str) -> ProductRow | None:
    return session.scalar(
        select(ProductRow).where(ProductRow.repo_id == repo_id, ProductRow.status == "active")
    )


def find_products_by_name(session: Session, name: str) -> list[ProductRow]:
    return list(
        session.scalars(
            select(ProductRow).where(ProductRow.name == name, ProductRow.status == "active")
        )
    )


def get_product(session: Session, product_id: str) -> ProductRow | None:
    return session.get(ProductRow, product_id)


def get_product_by_slug(session: Session, slug: str) -> ProductRow | None:
    return session.scalar(select(ProductRow).where(ProductRow.slug == slug))


def get_slug_redirect(session: Session, slug: str) -> SlugRedirectRow | None:
    return session.get(SlugRedirectRow, slug)


def create_product(
    session: Session,
    name: str,
    canonical_url: str | None,
    repo_id: str | None,
    canonical_key: str | None,
    first_seen_at: datetime,
    category: str | None = None,
    now: datetime | None = None,
) -> ProductRow:
    now = now or first_seen_at
    row = ProductRow(
        id=new_id("prd"),
        slug=unique_slug(session, slugify(name)),
        name=name.strip(),
        canonical_url=canonical_url,
        repo_id=repo_id,
        first_seen_at=first_seen_at,
        category=category,
        status="active",
        canonical_key=canonical_key,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def create_launch_event(
    session: Session,
    product_id: str,
    kind: str,
    event_at: datetime | None,
    first_seen_at: datetime,
    event_fingerprint: str,
    late_discovery: bool = False,
    time_unconfirmed: bool = False,
) -> tuple[LaunchEventRow, bool]:
    """fingerprint = 源ID/版本号或人工规则，不能仅用标题（PRD 8.3）。"""
    existing = session.scalar(
        select(LaunchEventRow).where(
            LaunchEventRow.product_id == product_id,
            LaunchEventRow.event_fingerprint == event_fingerprint,
        )
    )
    if existing is not None:
        return existing, False
    row = LaunchEventRow(
        id=new_id("evt"),
        product_id=product_id,
        kind=kind,
        event_at=event_at,
        first_seen_at=first_seen_at,
        event_fingerprint=event_fingerprint,
        late_discovery=late_discovery,
        time_unconfirmed=time_unconfirmed,
        status="active",
        created_at=first_seen_at,
    )
    session.add(row)
    session.flush()
    return row, True


def get_launch_event(session: Session, event_id: str) -> LaunchEventRow | None:
    return session.get(LaunchEventRow, event_id)


def link_product_source(
    session: Session,
    product_id: str,
    raw_item_id: str,
    match_method: str,
    confidence: float = 1.0,
    now: datetime | None = None,
) -> ProductSourceRow:
    """源条目只关联一个当前产品（unique raw_item_id）。"""
    existing = session.scalar(
        select(ProductSourceRow).where(ProductSourceRow.raw_item_id == raw_item_id)
    )
    if existing is not None:
        return existing
    row = ProductSourceRow(
        id=new_id("psrc"),
        product_id=product_id,
        raw_item_id=raw_item_id,
        match_method=match_method,
        confidence=confidence,
        created_at=now or utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def link_event_source(
    session: Session, launch_event_id: str, raw_item_id: str, now: datetime
) -> EventSourceRow:
    row = EventSourceRow(
        id=new_id("evsrc"), launch_event_id=launch_event_id, raw_item_id=raw_item_id, created_at=now
    )
    session.add(row)
    session.flush()
    return row


def product_source_count(session: Session, product_id: str) -> int:
    rows = session.scalars(
        select(ProductSourceRow).where(ProductSourceRow.product_id == product_id)
    ).all()
    return len(rows)


def distinct_source_keys(session: Session, product_id: str) -> set[str]:
    rows = session.scalars(
        select(RawItemRow.source_key)
        .join(ProductSourceRow, ProductSourceRow.raw_item_id == RawItemRow.id)
        .where(ProductSourceRow.product_id == product_id)
    ).all()
    return set(rows)


def add_evidence(session: Session, evidence: Evidence) -> EvidenceRow:
    row = EvidenceRow(
        id=evidence.id,
        raw_item_id=evidence.raw_item_id,
        source_url=evidence.source_url,
        excerpt=evidence.excerpt[:200],
        content_hash=evidence.content_hash,
        retrieved_at=evidence.retrieved_at,
        evidence_kind=evidence.evidence_kind,
        created_at=evidence.retrieved_at,
    )
    session.add(row)
    session.flush()
    return row


def list_evidence(session: Session, evidence_ids: list[str]) -> list[EvidenceRow]:
    if not evidence_ids:
        return []
    return list(session.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(evidence_ids))))


def evidence_for_product(session: Session, product_id: str) -> list[EvidenceRow]:
    return list(
        session.scalars(
            select(EvidenceRow)
            .join(RawItemRow, RawItemRow.id == EvidenceRow.raw_item_id)
            .join(ProductSourceRow, ProductSourceRow.raw_item_id == RawItemRow.id)
            .where(ProductSourceRow.product_id == product_id)
        )
    )


def add_pending_relation(
    session: Session,
    product_id_a: str,
    product_id_b: str,
    relation_type: str,
    signals: dict,
    now: datetime,
) -> PendingRelationRow:
    a, b = sorted([product_id_a, product_id_b])
    existing = session.scalar(
        select(PendingRelationRow).where(
            PendingRelationRow.product_id_a == a,
            PendingRelationRow.product_id_b == b,
            PendingRelationRow.status == "open",
        )
    )
    if existing is not None:
        return existing
    row = PendingRelationRow(
        id=new_id("rel"),
        product_id_a=a,
        product_id_b=b,
        relation_type=relation_type,
        signals=signals,
        status="open",
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row


def list_pending_relations(session: Session, status: str = "open") -> list[PendingRelationRow]:
    return list(
        session.scalars(select(PendingRelationRow).where(PendingRelationRow.status == status))
    )


def resolve_pending_relation(session: Session, relation_id: str, status: str) -> None:
    row = session.get(PendingRelationRow, relation_id)
    if row:
        row.status = status
        session.flush()


def merge_products(
    session: Session,
    merged_from_id: str,
    merged_into_id: str,
    reason: str,
    operator: str,
    rule_version: str,
    now: datetime,
) -> MergeActionRow:
    """合并记录保留前后实体与关联快照，支持撤销（PRD M2）。"""
    source = session.get(ProductRow, merged_from_id)
    target = session.get(ProductRow, merged_into_id)
    if source is None or target is None:
        raise KeyError("合并实体不存在")

    relations = {
        "sources": [
            {"id": r.id, "raw_item_id": r.raw_item_id}
            for r in session.scalars(
                select(ProductSourceRow).where(ProductSourceRow.product_id == merged_from_id)
            )
        ],
        "slug": source.slug,
        "summary": source.summary,
        "canonical_url": source.canonical_url,
        "repo_id": source.repo_id,
    }
    action = MergeActionRow(
        id=new_id("mrg"),
        merged_from_product_id=merged_from_id,
        merged_into_product_id=merged_into_id,
        relations_snapshot=relations,
        reason=reason,
        rule_version=rule_version,
        operator=operator,
        undone=False,
        created_at=now,
    )
    session.add(action)

    for rel in session.scalars(
        select(ProductSourceRow).where(ProductSourceRow.product_id == merged_from_id)
    ):
        rel.product_id = merged_into_id
    source.status = "merged"
    source.merged_into_id = merged_into_id
    source.updated_at = now
    session.add(
        SlugRedirectRow(
            old_slug=source.slug,
            product_id=merged_into_id,
            merge_action_id=action.id,
            created_at=now,
        )
    )
    session.flush()
    return action


def undo_merge(session: Session, merge_action_id: str, operator: str, now: datetime) -> None:
    action = session.get(MergeActionRow, merge_action_id)
    if action is None or action.undone:
        raise KeyError("合并记录不存在或已撤销")
    snapshot = action.relations_snapshot or {}
    for entry in snapshot.get("sources", []):
        rel = session.get(ProductSourceRow, entry["id"])
        if rel is not None:
            rel.product_id = action.merged_from_product_id
    source = session.get(ProductRow, action.merged_from_product_id)
    if source is not None:
        source.status = "active"
        source.merged_into_id = None
        source.updated_at = now
    redirect = session.get(SlugRedirectRow, snapshot.get("slug"))
    if redirect is not None:
        session.delete(redirect)
    action.undone = True
    action.undone_at = now
    session.flush()


def update_product_summary(
    session: Session, product_id: str, summary: str, category: str | None, now: datetime
) -> None:
    row = session.get(ProductRow, product_id)
    if row is None:
        return
    row.summary = summary
    if category:
        row.category = category
    row.updated_at = now
    session.flush()


def list_products_for_events(
    session: Session, event_ids: list[str]
) -> dict[str, LaunchEventRow]:
    if not event_ids:
        return {}
    rows = session.scalars(select(LaunchEventRow).where(LaunchEventRow.id.in_(event_ids)))
    return {r.id: r for r in rows}
