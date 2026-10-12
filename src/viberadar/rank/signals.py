"""排序信号：分位数、速度、新鲜度（PRD 7.2）。纯函数，便于测试。"""

from __future__ import annotations

import math
from datetime import datetime

from viberadar.contracts import SignalValue


def midrank_percentile(
    raw: list[float | None],
    min_samples: int = 5,
    missing_default: float = 0.5,
) -> list[SignalValue]:
    """按同一截点的固定集计算热度中位并列秩分位数。

    n≥5 且非全相同时为 (midrank-1)/(n-1)；否则 0.5 并标记缺失。
    缺失值记 missing_default（默认 0.5）。
    """
    present = [v for v in raw if v is not None]
    n = len(present)
    if n < min_samples or len(set(present)) <= 1:
        return [
            SignalValue(
                value=missing_default,
                missing=True,
                reason="样本不足或全相同（PRD 7.2）",
            )
            for _ in raw
        ]

    sorted_vals = sorted(present)
    result: list[SignalValue] = []
    for value in raw:
        if value is None:
            result.append(
                SignalValue(value=missing_default, missing=True, reason="缺热度")
            )
            continue
        less = sum(1 for v in sorted_vals if v < value)
        equal = sum(1 for v in sorted_vals if v == value)
        midrank = less + (equal + 1) / 2
        percentile = (midrank - 1) / (n - 1)
        result.append(
            SignalValue(value=max(0.0, min(1.0, percentile)), missing=False)
        )
    return result


def best_across_sources(per_source: dict[str, SignalValue]) -> SignalValue:
    """多个来源取最高值，不求和（PRD 7.2）。缺失不视为有效但保留信息。"""
    if not per_source:
        return SignalValue(value=0.5, missing=True, reason="无来源信号")
    valid = [v for v in per_source.values() if not v.missing]
    if not valid:
        first = next(iter(per_source.values()))
        return SignalValue(value=first.value, missing=True, reason=first.reason)
    best = max(valid, key=lambda v: v.value)
    return SignalValue(value=best.value, missing=False)


def velocity_from_snapshots(
    snapshots: list[tuple[datetime, float]],
    min_hours: float = 1.0,
) -> float | None:
    """log1p(max(0,delta)/hours)；至少两次快照且相隔 ≥1 小时（PRD 7.2）。"""
    if len(snapshots) < 2:
        return None
    ordered = sorted(snapshots, key=lambda s: s[0])
    first_at, first_val = ordered[0]
    last_at, last_val = ordered[-1]
    hours = (last_at - first_at).total_seconds() / 3600.0
    if hours < min_hours:
        return None
    delta = max(0.0, float(last_val) - float(first_val))
    return math.log1p(delta / hours)


def novelty_value(
    event_at: datetime | None,
    cutoff: datetime,
    unknown_time: bool = False,
    half_life_hours: float = 18.0,
    unknown_cap: float = 0.5,
) -> SignalValue:
    """novelty = exp(-hours_since_event/18)，相对本期截点；未知时间上限 0.5。"""
    if event_at is None:
        value = math.exp(0.0) if not unknown_time else unknown_cap
        return SignalValue(
            value=min(unknown_cap, value),
            missing=True,
            reason="发布时间未确认（PRD 4.3）",
        )
    hours = (cutoff - event_at).total_seconds() / 3600.0
    if hours < 0:
        # 未来时间异常进入复核
        return SignalValue(value=1.0, missing=False, reason="事件时间在未来，需复核")
    value = math.exp(-hours / half_life_hours)
    if unknown_time:
        value = min(value, unknown_cap)
    return SignalValue(value=max(0.0, min(1.0, value)), missing=False)
