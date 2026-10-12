"""反馈仓储：事件去重、当前态、报错（PRD M7）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from viberadar.contracts import EventPayload, new_id, utcnow

from .models import (
    ContentIssueRow,
    EventRow,
    FeedbackAuditRow,
    FeedbackStateRow,
)


def insert_event(
    session: Session,
    payload: EventPayload,
    actor_hash: str | None,
    identity_available: bool,
    value: dict | None = None,
    server_at: datetime | None = None,
) -> tuple[EventRow, bool]:
    """client_event_id 去重；重复投递返回原事件不重复计数（PRD M7）。"""
    existing = session.scalar(
        select(EventRow).where(EventRow.client_event_id == payload.client_event_id)
    )
    if existing is not None:
        return existing, False
    row = EventRow(
        id=new_id("evt"),
        client_event_id=payload.client_event_id,
        actor_hash=actor_hash,
        edition_id=payload.edition_id,
        edition_item_id=payload.edition_item_id,
        event_type=payload.event_type.value,
        value=value,
        ref=payload.ref,
        server_at=server_at or utcnow(),
        identity_available=identity_available,
    )
    session.add(row)
    session.flush()
    return row, True


def get_feedback_state(
    session: Session, actor_hash: str, edition_item_id: str
) -> FeedbackStateRow | None:
    return session.scalar(
        select(FeedbackStateRow).where(
            FeedbackStateRow.actor_hash == actor_hash,
            FeedbackStateRow.edition_item_id == edition_item_id,
        )
    )


def upsert_feedback_state(
    session: Session,
    actor_hash: str,
    edition_item_id: str,
    tried: bool | None,
    rating_state: str | None,
    failure_reason: str | None,
    failure_note: str | None,
    payload_json: dict,
    update_tried: bool = True,
    update_rating: bool = True,
) -> FeedbackStateRow:
    """当前态写入 + 审计事件（改选/撤回历史只用于审计，不重复计分）。"""
    row = get_feedback_state(session, actor_hash, edition_item_id)
    now = utcnow()
    if row is None:
        row = FeedbackStateRow(
            id=new_id("fbs"),
            actor_hash=actor_hash,
            edition_item_id=edition_item_id,
            tried=tried,
            rating_state=rating_state,
            failure_reason=failure_reason,
            failure_note=failure_note,
            version=1,
            updated_at=now,
        )
        session.add(row)
    else:
        if update_tried:
            row.tried = tried
        if update_rating:
            # rating_state 语义：显式 null = 撤回；显式设置覆盖旧态
            row.rating_state = rating_state
            if rating_state is None:
                row.failure_reason = None
                row.failure_note = None
            else:
                row.failure_reason = failure_reason
                row.failure_note = failure_note
        row.version += 1
        row.updated_at = now
    session.add(
        FeedbackAuditRow(
            id=new_id("fba"),
            actor_hash=actor_hash,
            edition_item_id=edition_item_id,
            action_json=payload_json,
            created_at=now,
        )
    )
    session.flush()
    return row


TRIED_EVENT_TYPES = {"tried"}
RATING_EVENT_TYPES = {"interest", "boring", "try_failed"}


def aggregate_edition_feedback(session: Session, edition_id: str) -> dict:
    """按当前态聚合（PRD M7：不把三次动作视为三人）。"""
    item_ids = {
        row[0]
        for row in session.execute(
            select(EventRow.edition_item_id).where(
                EventRow.edition_id == edition_id, EventRow.edition_item_id.is_not(None)
            )
        )
    }
    from .models import EditionItemRow

    item_ids.update(
        row[0]
        for row in session.execute(
            select(EditionItemRow.id).where(EditionItemRow.edition_id == edition_id)
        )
    )
    views = session.scalar(
        select(func.count(func.distinct(EventRow.actor_hash))).where(
            EventRow.edition_id == edition_id,
            EventRow.event_type == "edition_view",
            EventRow.identity_available.is_(True),
        )
    ) or 0
    clicks = session.scalar(
        select(func.count(func.distinct(EventRow.actor_hash))).where(
            EventRow.edition_id == edition_id,
            EventRow.event_type == "outbound_click",
            EventRow.identity_available.is_(True),
        )
    ) or 0
    stats = {
        "views": int(views),
        "clickers": int(clicks),
        "items": {},
    }
    for item_id in item_ids:
        if item_id is None:
            continue
        rows = session.scalars(
            select(FeedbackStateRow).where(FeedbackStateRow.edition_item_id == item_id)
        ).all()
        stats["items"][item_id] = {
            "tried": sum(1 for r in rows if r.tried is True),
            "interest": sum(1 for r in rows if r.rating_state == "interest"),
            "boring": sum(1 for r in rows if r.rating_state == "boring"),
            "try_failed": sum(1 for r in rows if r.rating_state == "try_failed"),
        }
    return stats


# ---------- 内容报错 ----------


def create_content_issue(
    session: Session,
    edition_item_id: str,
    issue_type: str,
    note: str,
) -> ContentIssueRow:
    row = ContentIssueRow(
        id=new_id("iss"),
        edition_item_id=edition_item_id,
        issue_type=issue_type,
        note=note,
        submitted_at=utcnow(),
        status="new",
    )
    session.add(row)
    session.flush()
    return row


def get_content_issue(session: Session, issue_id: str) -> ContentIssueRow | None:
    return session.get(ContentIssueRow, issue_id)


def list_content_issues(
    session: Session, status: str | None = None, limit: int = 100
) -> list[ContentIssueRow]:
    stmt = select(ContentIssueRow).order_by(ContentIssueRow.submitted_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(ContentIssueRow.status == status)
    return list(session.scalars(stmt))


def resolve_content_issue(
    session: Session,
    issue_id: str,
    status: str,
    resolution_note: str,
    operator: str,
    edition_revision_id: str | None,
) -> ContentIssueRow:
    row = session.get(ContentIssueRow, issue_id)
    if row is None:
        raise KeyError(f"报错不存在: {issue_id}")
    row.status = status
    row.resolution_note = resolution_note
    row.operator = operator
    row.edition_revision_id = edition_revision_id
    row.resolved_at = utcnow()
    session.flush()
    return row


def issues_for_item(session: Session, edition_item_id: str) -> list[ContentIssueRow]:
    return list(
        session.scalars(
            select(ContentIssueRow)
            .where(ContentIssueRow.edition_item_id == edition_item_id)
            .order_by(ContentIssueRow.submitted_at)
        )
    )
