"""评分公式（PRD 7.2）：确定性、显式缺失回退、加分上限。"""

from __future__ import annotations

from viberadar.contracts import BonusItem, ScoreBreakdown, ScoreSignals, SignalValue


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def compute_breakdown(
    product_id: str,
    signals: ScoreSignals,
    weights: dict[str, float],
    bonuses: list[BonusItem],
    max_total_bonus: float = 0.08,
) -> ScoreBreakdown:
    """base = 0.20*source + 0.15*velocity + 0.25*novelty + 0.25*interest + 0.15*taste - 0.30*spam。"""
    base = (
        weights.get("source_signal", 0.20) * signals.source_signal.value
        + weights.get("velocity", 0.15) * signals.velocity.value
        + weights.get("novelty", 0.25) * signals.novelty.value
        + weights.get("llm_interest", 0.25) * signals.llm_interest.value
        + weights.get("taste_match", 0.15) * signals.taste_match.value
        + weights.get("spam_score", -0.30) * signals.spam_score.value
    )
    budget = max_total_bonus
    applied: list[BonusItem] = []
    for bonus in bonuses:
        if budget <= 0:
            break
        value = min(bonus.value, budget)
        if value <= 0:
            continue
        applied.append(BonusItem(name=bonus.name, value=value, reason=bonus.reason))
        budget -= value
    final = clamp(base + sum(b.value for b in applied))
    missing = [
        name
        for name, sig in {
            "source_signal": signals.source_signal,
            "velocity": signals.velocity,
            "llm_interest": signals.llm_interest,
            "taste_match": signals.taste_match,
        }.items()
        if sig.missing
    ]
    return ScoreBreakdown(
        product_id=product_id,
        signals=signals,
        base_score=clamp(base),
        bonuses=applied,
        final_score=final,
        missing_fields=missing,
    )


def bonus_cross_source(
    distinct_source_count: int, min_sources: int = 2, value: float = 0.03
) -> BonusItem | None:
    if distinct_source_count >= min_sources:
        return BonusItem(
            name="cross_source",
            value=value,
            reason=f"独立来源≥{min_sources}且已确认同产品",
        )
    return None


def bonus_low_heat_creative(
    creativity: int | None,
    interest: float | None,
    heat_percentile: SignalValue,
    heat_missing: bool,
    min_creativity: int = 4,
    min_interest: float = 0.7,
    heat_max: float = 0.25,
    value: float = 0.03,
) -> BonusItem | None:
    """缺热度不视为低热度保护资格（PRD 7.2）。"""
    if heat_missing or creativity is None or interest is None:
        return None
    if creativity >= min_creativity and interest >= min_interest and heat_percentile.value <= heat_max:
        return BonusItem(
            name="low_heat_creative",
            value=value,
            reason="低热度创意候选（创意≥4、interest≥0.7、热度分位≤0.25）",
        )
    return None


def bonus_trial_category(
    category: str | None, trial_categories: list[str], value: float = 0.02
) -> BonusItem | None:
    if category and category in trial_categories:
        return BonusItem(
            name="trial_category",
            value=value,
            reason=f"试验分类偏好（{category}）",
        )
    return None


def sort_key(interest: float, event_at, stable_id: str):  # noqa: ANN201
    """排名相同：interest 降序、事件时间降序、稳定 ID 升序（PRD 7.2）。"""
    from datetime import datetime

    ts = event_at.timestamp() if isinstance(event_at, datetime) else 0.0
    return (-interest, -ts, stable_id)
