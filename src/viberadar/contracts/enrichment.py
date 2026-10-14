"""富化、门槛与体验指南契约（PRD 4.4/4.5/M4/附录 D.3）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator
from .trial import TryTier

from .common import (
    SCHEMA_VERSION,
    MobileSupport,
    Pricing,
    StrictModel,
    TryMode,
    VerificationStatus,
    ensure_aware,
)


class AccessInfo(StrictModel):
    pricing: Pricing = Pricing.UNKNOWN
    mobile_support: MobileSupport = MobileSupport.UNKNOWN
    login_required: bool | None = None
    api_key_required: bool | None = None
    waitlist: bool | None = None
    self_host_required: bool | None = None
    platforms: list[str] = Field(default_factory=list)
    setup_minutes: int | None = None
    checked_at: datetime

    @field_validator("checked_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v, "checked_at")

    @field_validator("setup_minutes")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("setup_minutes 不能为负")
        return v

    def display_text(self) -> str:
        """读者语言描述（PRD 4.4）：费用 · 登录/安装 · 平台。"""
        pricing_map = {
            Pricing.FREE: "免费",
            Pricing.FREEMIUM: "免费+付费",
            Pricing.PAID: "收费",
            Pricing.UNKNOWN: "收费未确认",
        }
        parts = [pricing_map[self.pricing]]
        if self.login_required is True:
            parts.append("需登录")
        if self.api_key_required is True:
            parts.append("需要API key")
        if self.waitlist is True:
            parts.append("需候补")
        if self.self_host_required is True:
            parts.append("需本地安装")
        if self.platforms:
            parts.append(" / ".join(self.platforms))
        return " · ".join(parts)


class TryStep(StrictModel):
    text: str

    @field_validator("text")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("步骤不能为空")
        return v.strip()


class Claim(StrictModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class InterestDims(StrictModel):
    """五维 rubric（PRD 7.1）。0–5；未知用 null 与 0 分分开记录。"""

    creativity: int | None = Field(default=None, ge=0, le=5)
    playability: int | None = Field(default=None, ge=0, le=5)
    completeness: int | None = Field(default=None, ge=0, le=5)
    surprise: int | None = Field(default=None, ge=0, le=5)
    niche: int | None = Field(default=None, ge=0, le=5)

    def llm_interest(self) -> float | None:
        values = [
            self.creativity,
            self.playability,
            self.completeness,
            self.surprise,
            self.niche,
        ]
        if any(v is None for v in values):
            return None
        return sum(v for v in values if v is not None) / 25.0

    def taste_match(self, weights: dict[str, float]) -> float | None:
        mapping = {
            "creativity": self.creativity,
            "playability": self.playability,
            "completeness": self.completeness,
            "surprise": self.surprise,
            "niche": self.niche,
        }
        total = 0.0
        for key, weight in weights.items():
            value = mapping.get(key)
            if value is None:
                return None
            total += weight * value
        return total / 5.0


class InterestAssessment(StrictModel):
    item_id: str
    dims: InterestDims
    evidence_refs: list[str] = Field(default_factory=list)
    why: str = ""
    needs_review: bool = False


class CardCopyEn(StrictModel):
    """英文模式卡片文案（与中文主字段一一对应；不混排中文）。"""

    one_liner: str
    why_interesting: str = ""
    user_story: str = ""
    steps: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("one_liner")
    @classmethod
    def _one_liner_len(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("one_liner(en) 不能为空")
        if len(v) > 120:
            raise ValueError("one_liner(en) 必须 ≤120 字符")
        return v


class Enrichment(StrictModel):
    try_tier: "TryTier | None" = None
    one_liner: str                      # 中文一句话描述 ≤40 字
    why_interesting: str
    user_story: str = ""                # 中文具体用户故事（谁在什么场景用它得到什么）
    en: CardCopyEn | None = None        # 英文模式文案
    mode: TryMode
    steps: list[TryStep] = Field(default_factory=list, max_length=4)
    access: AccessInfo
    claims: list[Claim] = Field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verified_by: str | None = None
    verified_at: datetime | None = None
    verified_device: str | None = None
    verified_condition: str | None = None
    verified_duration_seconds: int | None = None
    time_promise: str | None = None     # 仅 direct 且已实测时允许“约30秒开始体验”
    prompt_version: str | None = None
    model_version: str | None = None
    schema_version: str = SCHEMA_VERSION

    @field_validator("one_liner")
    @classmethod
    def _one_liner_len(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("one_liner 不能为空")
        if len(v) > 40:
            raise ValueError("one_liner 必须 ≤40 字")
        return v

    @field_validator("verified_at")
    @classmethod
    def _aware(cls, v: datetime | None):
        if v is None:
            return None
        return ensure_aware(v, "verified_at")

    @field_validator("time_promise")
    @classmethod
    def _promise_requires_tested(cls, v: str | None, info) -> str | None:
        if v is None:
            return None
        data = info.data
        if data.get("mode") != TryMode.DIRECT:
            raise ValueError("时间承诺仅适用于 direct 试玩")
        if data.get("verification_status") not in (
            VerificationStatus.HUMAN_TESTED,
            VerificationStatus.PROBE_TESTED,
        ):
            raise ValueError("未经实际验证不能承诺体验时间（PRD 4.5）")
        return v

    def claim_evidence_errors(self, valid_evidence_ids: set[str]) -> list[str]:
        """程序检查引用完整性（PRD M4）：断言必须引用已存在的摘录。"""
        errors: list[str] = []
        for claim in self.claims:
            if not claim.evidence_ids:
                errors.append(f"claim 缺少证据引用: {claim.text[:30]}")
                continue
            for eid in claim.evidence_ids:
                if eid not in valid_evidence_ids:
                    errors.append(f"claim 引用了不存在的证据: {eid}")
        return errors


SectionKey = Literal["tool", "creative"]
