"""期号仓储：期号状态机、条目快照、跳转链接、发送账本、修订（PRD M6/8.3）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.contracts import DeliveryStatus, EditionStatus, new_id, utcnow

from .models import (
    DeliveryAttemptRow,
    DeliveryLogRow,
    EditionItemRow,
    EditionRevisionRow,
    EditionRow,
    OutboundLinkRow,
)

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    EditionStatus.DRAFT.value: {EditionStatus.REVIEWING.value, EditionStatus.READY.value, EditionStatus.FAILED.value},
    EditionStatus.REVIEWING.value: {EditionStatus.READY.value, EditionStatus.DRAFT.value, EditionStatus.FAILED.value},
    EditionStatus.READY.value: {EditionStatus.PUBLISHED.value, EditionStatus.DRAFT.value, EditionStatus.FAILED.value},
    EditionStatus.PUBLISHED.value: set(),
    EditionStatus.FAILED.value: {EditionStatus.DRAFT.value},
}


class EditionStateError(RuntimeError):
    pass


def get_edition_by_date(session: Session, edition_date: date, tz: str) -> EditionRow | None:
    return session.scalar(
        select(EditionRow).where(EditionRow.date == edition_date, EditionRow.timezone == tz)
    )


def get_edition(session: Session, edition_id: str) -> EditionRow | None:
    return session.get(EditionRow, edition_id)


def create_edition(
    session: Session,
    edition_date: date,
    tz: str,
    config_version: str | None = None,
    run_id: str | None = None,
) -> EditionRow:
    existing = get_edition_by_date(session, edition_date, tz)
    if existing is not None:
        return existing
    now = utcnow()
    row = EditionRow(
        id=new_id("ed"),
        date=edition_date,
        timezone=tz,
        status=EditionStatus.DRAFT.value,
        content_status="normal",
        mode="auto",
        revision=1,
        config_version=config_version,
        run_id=run_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def transition(session: Session, edition: EditionRow, new_status: str) -> EditionRow:
    if new_status not in _ALLOWED_TRANSITIONS.get(edition.status, set()):
        raise EditionStateError(f"非法状态迁移: {edition.status} → {new_status}（PRD 5.4）")
    edition.status = new_status
    edition.updated_at = utcnow()
    session.flush()
    return edition


def mark_published(session: Session, edition: EditionRow, published_at: datetime) -> EditionRow:
    if edition.status == EditionStatus.PUBLISHED.value:
        return edition
    transition(session, edition, EditionStatus.PUBLISHED.value)
    edition.published_at = published_at
    session.flush()
    return edition


def set_content_status(session: Session, edition: EditionRow, content_status: str) -> None:
    edition.content_status = content_status
    edition.updated_at = utcnow()
    session.flush()


def replace_items(
    session: Session,
    edition: EditionRow,
    revision: int,
    items: list[dict],
) -> list[EditionItemRow]:
    """写入（或替换同一 revision 的）条目快照；调用方负责冻结语义。"""
    existing = list(
        session.scalars(
            select(EditionItemRow).where(
                EditionItemRow.edition_id == edition.id,
                EditionItemRow.revision == revision,
            )
        )
    )
    if existing:
        raise EditionStateError(
            f"revision {revision} 已冻结，不能覆盖；请创建新修订（PRD 5.2）"
        )
    now = utcnow()
    rows: list[EditionItemRow] = []
    for idx, item in enumerate(items):
        row = EditionItemRow(
            id=new_id("edi"),
            edition_id=edition.id,
            revision=revision,
            position=idx + 1,
            section=item.get("section", "tool"),
            product_id=item["product_id"],
            event_id=item.get("event_id"),
            final_score=Decimal(str(item.get("final_score", 0))),
            signals=item.get("signals"),
            content_json=item["content"],
            highlight=item.get("highlight", False),
            revision_note=item.get("revision_note"),
            retracted=item.get("retracted", False),
            created_at=now,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def list_items(
    session: Session, edition_id: str, revision: int | None = None
) -> list[EditionItemRow]:
    stmt = select(EditionItemRow).where(EditionItemRow.edition_id == edition_id)
    if revision is not None:
        stmt = stmt.where(EditionItemRow.revision == revision)
    return list(session.scalars(stmt.order_by(EditionItemRow.revision, EditionItemRow.position)))


def get_item(session: Session, item_id: str) -> EditionItemRow | None:
    return session.get(EditionItemRow, item_id)


def latest_revision(session: Session, edition_id: str) -> int:
    rows = session.scalars(
        select(EditionItemRow.revision)
        .where(EditionItemRow.edition_id == edition_id)
        .order_by(EditionItemRow.revision.desc())
        .limit(1)
    ).all()
    return rows[0] if rows else 0


def add_revision_record(
    session: Session,
    edition: EditionRow,
    revision: int,
    reason: str,
    field_diff: dict,
    notify_again: bool = False,
) -> EditionRevisionRow:
    row = EditionRevisionRow(
        id=new_id("rev"),
        edition_id=edition.id,
        revision=revision,
        reason=reason,
        field_diff=field_diff,
        notify_again=notify_again,
        corrected_at=utcnow(),
    )
    session.add(row)
    edition.revision = max(edition.revision, revision)
    edition.updated_at = utcnow()
    session.flush()
    return row


def create_outbound_link(
    session: Session, edition_item_id: str | None, target_url: str
) -> OutboundLinkRow:
    row = OutboundLinkRow(
        link_id=new_id("lnk"),
        edition_item_id=edition_item_id,
        target_url=target_url,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def get_outbound_link(session: Session, link_id: str) -> OutboundLinkRow | None:
    return session.get(OutboundLinkRow, link_id)


def pending_editions_before(session: Session, utc_now: datetime) -> list[EditionRow]:
    return list(
        session.scalars(
            select(EditionRow).where(
                EditionRow.status.in_([EditionStatus.DRAFT.value, EditionStatus.REVIEWING.value, EditionStatus.READY.value]),
                EditionRow.created_at <= utc_now,
            )
        )
    )


# ---------- 发送账本 ----------


def get_or_create_delivery(
    session: Session,
    edition_id: str,
    channel: str,
    destination: str,
    dispatch_version: int = 1,
) -> tuple[DeliveryAttemptRow, bool]:
    row = session.scalar(
        select(DeliveryAttemptRow).where(
            DeliveryAttemptRow.edition_id == edition_id,
            DeliveryAttemptRow.channel == channel,
            DeliveryAttemptRow.destination == destination,
            DeliveryAttemptRow.dispatch_version == dispatch_version,
        )
    )
    if row is not None:
        return row, False
    now = utcnow()
    row = DeliveryAttemptRow(
        id=new_id("dlv"),
        edition_id=edition_id,
        channel=channel,
        destination=destination,
        dispatch_version=dispatch_version,
        status=DeliveryStatus.PENDING.value,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row, True


def update_delivery_status(
    session: Session,
    attempt: DeliveryAttemptRow,
    status: str,
    external_message_id: str | None = None,
    error: str | None = None,
    request_hash: str | None = None,
    http_status: int | None = None,
) -> DeliveryAttemptRow:
    attempt.status = status
    if external_message_id:
        attempt.external_message_id = external_message_id
    if error is not None:
        attempt.last_error = error[:2000]
    if request_hash:
        attempt.request_hash = request_hash
    attempt.attempt_count += 1
    attempt.updated_at = utcnow()
    session.add(
        DeliveryLogRow(
            id=new_id("dlg"),
            delivery_attempt_id=attempt.id,
            request_hash=request_hash,
            status=status,
            http_status=http_status,
            error=(error or None) and error[:2000],
            created_at=utcnow(),
        )
    )
    session.flush()
    return attempt


def list_deliveries(session: Session, edition_id: str) -> list[DeliveryAttemptRow]:
    return list(
        session.scalars(
            select(DeliveryAttemptRow)
            .where(DeliveryAttemptRow.edition_id == edition_id)
            .order_by(DeliveryAttemptRow.created_at)
        )
    )


def list_deliveries_by_status(session: Session, status: str) -> list[DeliveryAttemptRow]:
    return list(
        session.scalars(
            select(DeliveryAttemptRow)
            .where(DeliveryAttemptRow.status == status)
            .order_by(DeliveryAttemptRow.updated_at)
        )
    )
