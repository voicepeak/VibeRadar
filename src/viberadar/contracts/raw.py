"""采集原始条目契约（PRD 8.1）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, HttpUrl, field_validator

from .common import SCHEMA_VERSION, StrictModel, ensure_aware


class RawItem(StrictModel):
    source_key: str
    external_id: str
    source_url: HttpUrl
    title: str
    target_url: HttpUrl | None = None
    description: str | None = None
    author: str | None = None
    published_at: datetime | None = None  # timezone-aware UTC
    fetched_at: datetime                  # timezone-aware UTC
    metrics: dict[str, int | float | None] = Field(default_factory=dict)
    raw_metadata: dict = Field(default_factory=dict)  # 字段白名单；不存完整 payload
    query_key: str | None = None
    schema_version: str = SCHEMA_VERSION

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _aware(cls, v: datetime | None, info):
        if v is None:
            return None
        return ensure_aware(v, info.field_name)

    @field_validator("title", "external_id", "source_key")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("必填字段不能为空")
        return v.strip()


class MetricSnapshot(StrictModel):
    raw_item_id: str
    captured_at: datetime
    metrics: dict[str, int | float | None] = Field(default_factory=dict)

    @field_validator("captured_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "captured_at")
