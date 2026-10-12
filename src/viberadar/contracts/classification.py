"""分类与垃圾内容判断契约（PRD M3/附录 D.1）。"""

from __future__ import annotations

from pydantic import Field, field_validator

from .common import SCHEMA_VERSION, StrictModel


class Classification(StrictModel):
    item_id: str
    relevant: bool | None = None
    category: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    spam_score: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    needs_review: bool = False
    model_confidence: float | None = None    # 保留原模型分数（人工确认不覆盖）
    schema_version: str = SCHEMA_VERSION

    @field_validator("category")
    @classmethod
    def _category_allowed(cls, v: str | None) -> str | None:
        if v is None:
            return None
        from viberadar.settings import taxonomy_keys

        allowed = taxonomy_keys()
        if v not in allowed:
            raise ValueError(f"category 不在 taxonomy 中: {v}")
        return v


class ClassificationBatch(StrictModel):
    results: list[Classification]

    @field_validator("results")
    @classmethod
    def _unique_items(cls, v: list[Classification]) -> list[Classification]:
        seen = set()
        for c in v:
            if c.item_id in seen:
                raise ValueError(f"item_id 重复: {c.item_id}")
            seen.add(c.item_id)
        return v
