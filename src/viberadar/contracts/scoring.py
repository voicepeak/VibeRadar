"""排序信号与分数（PRD 7.2）。"""

from __future__ import annotations

from pydantic import Field, field_validator

from .common import StrictModel


class SignalValue(StrictModel):
    value: float = Field(ge=0.0, le=1.0)
    missing: bool = False
    reason: str | None = None


class ScoreSignals(StrictModel):
    source_signal: SignalValue
    velocity: SignalValue
    novelty: SignalValue
    llm_interest: SignalValue
    taste_match: SignalValue
    spam_score: SignalValue


class BonusItem(StrictModel):
    name: str
    value: float
    reason: str


class ScoreBreakdown(StrictModel):
    product_id: str
    signals: ScoreSignals
    base_score: float
    bonuses: list[BonusItem] = Field(default_factory=list)
    final_score: float
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("final_score", "base_score")
    @classmethod
    def _bounded(cls, v: float) -> float:
        return max(0.0, min(1.0, v))
