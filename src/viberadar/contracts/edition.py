"""期号、快照、阶段结果契约（PRD 5.3/5.4/8.1）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from .common import (
    SCHEMA_VERSION,
    ContentStatus,
    EditionStatus,
    StageStatus,
    StrictModel,
    ensure_aware,
)


class PipelineRun(StrictModel):
    run_id: str
    edition_id: str | None = None
    window_start: datetime
    window_end: datetime
    cutoff: datetime
    status: StageStatus = StageStatus.SUCCESS
    code_version: str
    config_hash: str
    prompt_hash: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None
    replay_of: str | None = None
    dry_run: bool = False
    schema_version: str = SCHEMA_VERSION

    @field_validator(
        "window_start", "window_end", "cutoff", "started_at", "finished_at"
    )
    @classmethod
    def _aware(cls, v: datetime | None, info):
        if v is None:
            return None
        return ensure_aware(v, info.field_name)


class StageResult(StrictModel):
    run_id: str
    stage: str
    status: StageStatus
    input_count: int = 0
    output_count: int = 0
    error_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    cache_key: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


class DeliveryAttempt(StrictModel):
    id: str
    edition_id: str
    channel: str
    destination: str
    dispatch_version: int = 1
    status: str
    external_message_id: str | None = None
    request_hash: str | None = None
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    schema_version: str = SCHEMA_VERSION

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "datetime")


class EditionItemView(StrictModel):
    """公开卡片快照（不可变；修订创建新 revision）。"""

    id: str
    edition_id: str
    revision: int = 1
    position: int
    section: str = "tool"
    product_id: str
    slug: str
    name: str
    category: str
    category_label: str
    one_liner: str
    user_story: str = ""
    why_interesting: str
    mode: str
    access_text: str
    steps: list[str] = Field(default_factory=list)
    time_promise: str | None = None
    mobile_note: str | None = None
    evidence_status: str
    source_links: list[dict[str, str]] = Field(default_factory=list)
    checked_at: datetime
    final_score: float = 0.0
    signals: dict[str, Any] = Field(default_factory=dict)
    highlight: bool = False
    revision_note: str | None = None
    retracted: bool = False
    edition_date: date | None = None
    links: dict[str, str] = Field(default_factory=dict)
    trial: dict | None = None

    @field_validator("checked_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "checked_at")


class EditionView(StrictModel):
    id: str
    date: date
    timezone: str
    status: EditionStatus
    content_status: ContentStatus
    revision: int = 1
    intro: str = ""
    published_at: datetime | None = None
    source_coverage: str | None = None
    items: list[EditionItemView] = Field(default_factory=list)

    @field_validator("published_at")
    @classmethod
    def _aware(cls, v: datetime | None):
        if v is None:
            return None
        return ensure_aware(v, "published_at")
