"""反馈事件、当前态与报错契约（PRD M7/12.2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .common import (
    SCHEMA_VERSION,
    EventType,
    FailureReason,
    IssueStatus,
    IssueType,
    RatingState,
    StrictModel,
    ensure_aware,
)

_CHANNEL_WHITELIST = {"web", "feishu", "rss", "manual"}


class EventPayload(StrictModel):
    """v1 事件载荷（PRD 12.2）。actor 由服务端从第一方标识派生，不可自报。"""

    client_event_id: str
    schema_version: str = SCHEMA_VERSION
    event_type: EventType
    edition_id: str | None = None
    edition_item_id: str | None = None
    ref: str = "web"
    client_at: datetime | None = None

    @field_validator("ref")
    @classmethod
    def _ref_whitelist(cls, v: str) -> str:
        if v not in _CHANNEL_WHITELIST:
            raise ValueError("ref 只能取 web/feishu/rss/manual")
        return v

    @field_validator("client_at")
    @classmethod
    def _aware(cls, v: datetime | None):
        if v is None:
            return None
        return ensure_aware(v, "client_at")

    @field_validator("client_event_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 128:
            raise ValueError("client_event_id 非法")
        return v


class FeedbackStatePayload(StrictModel):
    tried: bool | None = None                     # 独立于评价
    rating_state: RatingState | None = None       # 单选最新态；null 表示撤回
    failure_reason: FailureReason | None = None
    failure_note: str | None = None


class FeedbackStateView(StrictModel):
    edition_item_id: str
    tried: bool | None = None
    rating_state: RatingState | None = None
    failure_reason: FailureReason | None = None
    failure_note: str | None = None
    updated_at: datetime | None = None
    actor_available: bool = True

    @field_validator("updated_at")
    @classmethod
    def _aware(cls, v: datetime | None):
        if v is None:
            return None
        return ensure_aware(v, "updated_at")


class ContentIssueCreate(StrictModel):
    edition_item_id: str
    issue_type: IssueType
    note: str = ""

    @field_validator("note")
    @classmethod
    def _bounded(cls, v: str) -> str:
        return v.strip()[:500]


class ContentIssueView(StrictModel):
    id: str
    edition_item_id: str
    issue_type: IssueType
    note: str
    submitted_at: datetime
    status: IssueStatus
    resolution_note: str | None = None
    edition_revision_id: str | None = None

    @field_validator("submitted_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "submitted_at")


class EventRecord(StrictModel):
    id: str
    client_event_id: str
    actor_hash: str | None
    edition_id: str | None
    edition_item_id: str | None
    event_type: EventType
    value: dict[str, Any] | None = None
    ref: str
    server_at: datetime
    identity_available: bool = True

    @field_validator("server_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "server_at")
