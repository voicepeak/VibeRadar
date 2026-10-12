"""产品实体、发布事件、证据与合并记录（PRD 4.3/8.2/M2）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from .common import (
    SCHEMA_VERSION,
    LaunchKind,
    ProductStatus,
    StrictModel,
    ensure_aware,
)


class Product(StrictModel):
    id: str
    slug: str
    name: str
    canonical_url: str | None = None
    repo_id: str | None = None
    first_seen_at: datetime
    category: str | None = None
    summary: str | None = None
    status: ProductStatus = ProductStatus.ACTIVE
    retraction_note: str | None = None
    schema_version: str = SCHEMA_VERSION

    @field_validator("first_seen_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "first_seen_at")


class LaunchEvent(StrictModel):
    id: str
    product_id: str
    kind: LaunchKind
    event_at: datetime | None = None       # 来源发布时间（不自动等于产品诞生时间）
    first_seen_at: datetime
    event_fingerprint: str
    evidence_ids: list[str] = Field(default_factory=list)
    late_discovery: bool = False           # 晚发现标记（PRD 4.3）
    time_unconfirmed: bool = False         # 首次发现，发布时间未确认
    schema_version: str = SCHEMA_VERSION

    @field_validator("event_at", "first_seen_at")
    @classmethod
    def _aware(cls, v: datetime | None, info):
        if v is None:
            return None
        return ensure_aware(v, info.field_name)


class Evidence(StrictModel):
    id: str
    raw_item_id: str | None = None
    source_url: str
    excerpt: str
    content_hash: str
    retrieved_at: datetime
    evidence_kind: str = "excerpt"         # excerpt/readme/landing/doc
    schema_version: str = SCHEMA_VERSION

    @field_validator("retrieved_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "retrieved_at")

    @field_validator("excerpt")
    @classmethod
    def _bounded(cls, v: str) -> str:
        if len(v) > 200:
            # PRD 10.4：每条源材料累计 ≤200 字短摘录
            return v[:200]
        return v


class MergeAction(StrictModel):
    id: str
    merged_from_product_id: str
    merged_into_product_id: str
    relations_snapshot: dict = Field(default_factory=dict)
    reason: str
    rule_version: str
    operator: str
    undone: bool = False
    undone_at: datetime | None = None
    created_at: datetime
    schema_version: str = SCHEMA_VERSION

    @field_validator("created_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "created_at")
