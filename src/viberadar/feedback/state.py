"""反馈当前态写入与读取（PRD M7/9.1）。

- tried 独立于评价；rating 单选最新态且允许改选或撤回（null = 撤回）。
- 返回卡片或刷新后读取当前匿名 actor 的反馈态。
- “没玩起来”可选原因；未填写保持未知，不由系统猜测。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from viberadar.contracts import (
    FeedbackStatePayload,
    FeedbackStateView,
    RatingState,
)
from viberadar.store import repo_feedback


def set_feedback_state(
    session: Session,
    actor_hash: str,
    edition_item_id: str,
    payload: FeedbackStatePayload,
) -> FeedbackStateView:
    update_tried = "tried" in payload.model_fields_set
    update_rating = "rating_state" in payload.model_fields_set
    if update_rating and payload.rating_state in (RatingState.INTEREST, RatingState.BORING):
        failure_reason = None
        failure_note = None
    else:
        failure_reason = payload.failure_reason
        failure_note = payload.failure_note
    row = repo_feedback.upsert_feedback_state(
        session,
        actor_hash=actor_hash,
        edition_item_id=edition_item_id,
        tried=payload.tried,
        rating_state=payload.rating_state.value if payload.rating_state else None,
        failure_reason=failure_reason.value if failure_reason else None,
        failure_note=failure_note,
        payload_json=payload.model_dump(mode="json", exclude_none=True),
        update_tried=update_tried,
        update_rating=update_rating,
    )
    return _to_view(row)


def get_feedback_state_view(
    session: Session, actor_hash: str | None, edition_item_id: str
) -> FeedbackStateView:
    if actor_hash is None:
        return FeedbackStateView(edition_item_id=edition_item_id, actor_available=False)
    row = repo_feedback.get_feedback_state(session, actor_hash, edition_item_id)
    if row is None:
        return FeedbackStateView(edition_item_id=edition_item_id, actor_available=True)
    return _to_view(row)


def _to_view(row) -> FeedbackStateView:  # noqa: ANN001
    return FeedbackStateView(
        edition_item_id=row.edition_item_id,
        tried=row.tried,
        rating_state=RatingState(row.rating_state) if row.rating_state else None,
        failure_reason=row.failure_reason,
        failure_note=row.failure_note,
        updated_at=row.updated_at,
        actor_available=True,
    )
