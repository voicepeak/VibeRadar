"""单元测试：分位数、公式与多样性（PRD 7.2/7.3）。"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from viberadar.contracts import BonusItem, ScoreSignals, SignalValue
from viberadar.rank import (
    best_across_sources,
    bonus_low_heat_creative,
    compute_breakdown,
    midrank_percentile,
    novelty_value,
    review_pool_limit,
    select_public_items,
    short_edition_items,
    velocity_from_snapshots,
)
from viberadar.rank.diversity import DiversityItem

UTC = timezone.utc


def test_midrank_percentile_exact():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = midrank_percentile(values)
    assert [round(r.value, 4) for r in result] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert all(not r.missing for r in result)


def test_midrank_ties_use_midrank():
    values = [10.0, 10.0, 20.0, 30.0, 40.0]
    result = midrank_percentile(values)
    assert result[0].value == result[1].value  # 并列取中位秩
    # less=0, equal=2 → midrank=1.5 → (1.5-1)/(5-1)=0.125
    assert round(result[0].value, 4) == 0.125
    # 20: less=2, equal=1 → midrank=3 → (3-1)/4=0.5
    assert round(result[2].value, 4) == 0.5


def test_midrank_small_or_constant_falls_back():
    small = midrank_percentile([1.0, 2.0, 3.0])
    assert all(r.value == 0.5 and r.missing for r in small)
    constant = midrank_percentile([5.0] * 6)
    assert all(r.value == 0.5 and r.missing for r in constant)
    missing = midrank_percentile([None] * 6)
    assert all(r.missing for r in missing)


def test_best_across_sources_takes_max():
    best = best_across_sources(
        {
            "hn": SignalValue(value=0.3),
            "github": SignalValue(value=0.9),
            "manual": SignalValue(value=0.5, missing=True),
        }
    )
    assert best.value == 0.9 and not best.missing


def test_novelty_formula_and_unknown_cap():
    cutoff = datetime(2026, 10, 12, 7, 30, tzinfo=UTC)
    fresh = novelty_value(cutoff, cutoff)
    assert fresh.value == 1.0
    old = novelty_value(cutoff - timedelta(hours=18), cutoff)
    assert math.isclose(old.value, math.exp(-1), rel_tol=1e-6)
    unknown = novelty_value(None, cutoff, unknown_time=True)
    assert unknown.value == 0.5 and unknown.missing


def test_velocity_requires_two_snapshots_one_hour_apart():
    now = datetime(2026, 10, 12, tzinfo=UTC)
    single = velocity_from_snapshots([(now, 10.0)])
    assert single is None
    too_close = velocity_from_snapshots([(now, 10.0), (now + timedelta(minutes=30), 20.0)])
    assert too_close is None
    value = velocity_from_snapshots([(now, 10.0), (now + timedelta(hours=2), 30.0)])
    assert math.isclose(value, math.log1p(10.0), rel_tol=1e-9)


def _signals(**over):
    defaults = dict(
        source_signal=SignalValue(value=0.5),
        velocity=SignalValue(value=0.5, missing=True),
        novelty=SignalValue(value=0.5),
        llm_interest=SignalValue(value=0.8),
        taste_match=SignalValue(value=0.6),
        spam_score=SignalValue(value=0.0),
    )
    defaults.update(over)
    return ScoreSignals(**defaults)


def test_score_formula_exact_and_clamped():
    breakdown = compute_breakdown(
        "p1",
        _signals(),
        {
            "source_signal": 0.20,
            "velocity": 0.15,
            "novelty": 0.25,
            "llm_interest": 0.25,
            "taste_match": 0.15,
            "spam_score": -0.30,
        },
        [],
    )
    expected = 0.2 * 0.5 + 0.15 * 0.5 + 0.25 * 0.5 + 0.25 * 0.8 + 0.15 * 0.6
    assert abs(breakdown.base_score - expected) < 1e-9
    assert "velocity" in breakdown.missing_fields


def test_bonus_total_capped_at_008():
    bonuses = [
        BonusItem(name="a", value=0.05, reason="r"),
        BonusItem(name="b", value=0.05, reason="r"),
        BonusItem(name="c", value=0.05, reason="r"),
    ]
    breakdown = compute_breakdown("p1", _signals(), {"source_signal": 1.0}, bonuses, max_total_bonus=0.08)
    total = sum(b.value for b in breakdown.bonuses)
    assert abs(total - 0.08) < 1e-9


def test_low_heat_bonus_requires_valid_heat():
    heat = SignalValue(value=0.1)
    assert bonus_low_heat_creative(5, 0.8, heat, heat_missing=False) is not None
    assert bonus_low_heat_creative(5, 0.8, heat, heat_missing=True) is None
    assert bonus_low_heat_creative(3, 0.8, heat, heat_missing=False) is None
    assert bonus_low_heat_creative(5, 0.5, heat, heat_missing=False) is None
    hot = SignalValue(value=0.9)
    assert bonus_low_heat_creative(5, 0.8, hot, heat_missing=False) is None


def _items(specs):
    return [
        DiversityItem(key=f"p{i}", category=cat, score=score, interest=score)
        for i, (cat, score) in enumerate(specs)
    ]


def test_review_pool_category_cap():
    items = _items([("a", 1.0)] * 15 + [("b", 0.5)] * 10)
    pool = review_pool_limit(items, candidate_limit=20, category_max=8)
    from collections import Counter

    counts = Counter(i.category for i in pool)
    # 两个分类都触顶：8 + 8 = 16；不为了比例删除合格候选之外的东西
    assert len(pool) == 16
    assert counts["a"] == 8 and counts["b"] == 8


def test_public_selection_category_ratio_and_coverage():
    specs = [("a", 1.0), ("a", 0.9), ("a", 0.8), ("a", 0.7), ("b", 0.6), ("c", 0.5), ("d", 0.4)]
    selected = select_public_items(_items(specs), target=6)
    assert len(selected) == 6
    from collections import Counter

    counts = Counter(i.category for i in selected)
    assert counts["a"] <= 3  # ceil(0.4*6)=3
    assert len(counts) >= 3


def test_public_selection_never_deletes_qualified_when_few():
    selected = select_public_items(_items([("a", 1.0), ("b", 0.9), ("c", 0.8)]), target=6)
    assert len(selected) == 3


def test_short_and_empty_edition():
    assert short_edition_items([], 5) == ([], "empty")
    items = _items([("a", 1.0)] * 3)
    assert short_edition_items(items, 5)[1] == "short"
    items = _items([("a", 1.0)] * 5)
    assert short_edition_items(items, 5)[1] == "normal"
