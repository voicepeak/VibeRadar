"""反馈：事件去重、当前态、聚合与限流（PRD M7/E16–E18/E29）。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from viberadar.contracts import (
    EventPayload,
    EventType,
    FailureReason,
    FeedbackStatePayload,
    RatingState,
)
from viberadar.feedback import (
    derive_actor_hash,
    edition_stats,
    get_feedback_state_view,
    ingest_event,
    limiter,
    set_feedback_state,
)
from viberadar.store import repo_editions, repo_products

NOW = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc)


def _seed_item(session):
    edition = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    product = repo_products.create_product(
        session, "P", None, None, "url:https://example.com/p", NOW
    )
    rows = repo_editions.replace_items(
        session,
        edition,
        edition.revision,
        [{"product_id": product.id, "content": {"name": "P"}}],
    )
    return edition, rows[0]


def test_event_dedup_by_client_event_id(session):
    payload = EventPayload(
        client_event_id="evt-1", event_type=EventType.EDITION_VIEW, ref="web"
    )
    row1, created1 = ingest_event(session, payload, "actor1", True)
    row2, created2 = ingest_event(session, payload, "actor1", True)
    assert created1 is True and created2 is False
    assert row1 == row2


def test_feedback_current_state_flow(session):
    edition, item = _seed_item(session)
    actor = "actor-hash"
    set_feedback_state(
        session, actor, item.id, FeedbackStatePayload(tried=True)
    )
    view = set_feedback_state(
        session, actor, item.id, FeedbackStatePayload(rating_state=RatingState.INTEREST)
    )
    assert view.tried is True and view.rating_state == RatingState.INTEREST
    # 改选
    view = set_feedback_state(
        session, actor, item.id, FeedbackStatePayload(rating_state=RatingState.TRY_FAILED, failure_reason=FailureReason.ENTRY_DEAD)
    )
    assert view.rating_state == RatingState.TRY_FAILED
    assert view.failure_reason == FailureReason.ENTRY_DEAD
    # 撤回评价，tried 保留
    view = set_feedback_state(
        session, actor, item.id, FeedbackStatePayload(rating_state=None)
    )
    assert view.rating_state is None and view.tried is True


def test_feedback_retract_tried(session):
    edition, item = _seed_item(session)
    actor = "actor-x"
    set_feedback_state(session, actor, item.id, FeedbackStatePayload(tried=True))
    view = set_feedback_state(session, actor, item.id, FeedbackStatePayload(tried=None))
    assert view.tried is None


def test_feedback_aggregation_counts_current_state_only(session):
    edition, item = _seed_item(session)
    # 同一 actor 三次动作 → 只计最新态
    set_feedback_state(session, "a1", item.id, FeedbackStatePayload(tried=True))
    set_feedback_state(session, "a1", item.id, FeedbackStatePayload(rating_state=RatingState.INTEREST))
    set_feedback_state(session, "a1", item.id, FeedbackStatePayload(rating_state=RatingState.BORING))
    set_feedback_state(session, "a2", item.id, FeedbackStatePayload(tried=True))
    set_feedback_state(session, "a3", item.id, FeedbackStatePayload(rating_state=RatingState.TRY_FAILED))
    stats = edition_stats(session, edition.id)
    assert stats["items"][item.id]["tried"] == 2
    assert stats["items"][item.id]["boring"] == 1
    assert stats["items"][item.id]["try_failed"] == 1
    assert stats["items"][item.id]["interest"] == 0


def test_actor_hash_is_server_derived():
    a = derive_actor_hash("secret", "aid-1")
    b = derive_actor_hash("secret", "aid-2")
    c = derive_actor_hash("other", "aid-1")
    assert a != b and a != c


def test_rate_limiter_blocks_after_limit():
    limiter._buckets.clear()
    for _ in range(5):
        assert limiter.allow("test-key", limit=5) is True
    assert limiter.allow("test-key", limit=5) is False
    limiter._buckets.clear()


def test_get_state_without_actor_marks_unavailable(session):
    edition, item = _seed_item(session)
    view = get_feedback_state_view(session, None, item.id)
    assert view.actor_available is False
