"""事件写入与统计（PRD M7/12）。

事件定义：edition_view/card_impression/outbound_click/tried/interest/
boring/try_failed/save/unsave/report_issue。
client_event_id 去重；聚合按期号/条目/事件处理当前态。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from viberadar.contracts import EventPayload
from viberadar.store import repo_feedback


def ingest_event(
    session: Session,
    payload: EventPayload,
    actor_hash: str | None,
    identity_available: bool,
) -> tuple[str, bool]:
    """返回 (event_id, created)。重复 client_event_id 原事件成功返回不重复计数。"""
    row, created = repo_feedback.insert_event(
        session,
        payload,
        actor_hash=actor_hash,
        identity_available=identity_available,
    )
    return row.id, created


def edition_stats(session: Session, edition_id: str) -> dict:
    return repo_feedback.aggregate_edition_feedback(session, edition_id)
