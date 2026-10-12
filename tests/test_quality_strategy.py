"""宽进策略：soft 模式质量问题不阻塞入选（仅标记）；只有明确排除与运营性去重硬拦截。

strict 模式保留 PRD 5.3 全部硬门槛，可用配置回退。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from viberadar.contracts import InterestDims
from viberadar.pipeline.runner import _passes_gates

CUTOFF = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc)


def _entry():
    return SimpleNamespace(
        product=SimpleNamespace(id="prd_x", slug="x"),
        raw_item=SimpleNamespace(id="raw_x", description="", title="t"),
        event=SimpleNamespace(id="evt_x", status="active"),
        exclusion_reason=None,
    )


def _dims(low: bool = True) -> InterestDims:
    if low:
        return InterestDims(creativity=3, playability=2, completeness=3, surprise=3, niche=3)
    return InterestDims(creativity=4, playability=4, completeness=4, surprise=4, niche=4)


def _unjudged():
    return SimpleNamespace(relevant=None, confidence=0.0, spam_score=0.0, category=None)


def test_soft_mode_keeps_unjudged_item_eligible_with_flags(session, temp_settings):
    assert temp_settings.pipeline.quality_gates.mode == "soft"
    auto, reasons = _passes_gates(
        session, _entry(), _unjudged(), _dims(), None, temp_settings, CUTOFF
    )
    assert auto is True
    assert any("相关性未确认" in r for r in reasons)
    assert any("分类置信度" in r for r in reasons)
    assert any("无富化结果" in r for r in reasons)
    assert any("兴趣分" in r for r in reasons)


def test_strict_mode_blocks_unjudged_item(session, temp_settings):
    temp_settings.pipeline.quality_gates.mode = "strict"
    auto, reasons = _passes_gates(
        session, _entry(), _unjudged(), _dims(), None, temp_settings, CUTOFF
    )
    assert auto is False
    assert any("相关性未确认" in r for r in reasons)


def test_soft_mode_still_blocks_explicit_exclusion(session, temp_settings):
    classification = SimpleNamespace(
        relevant=False, confidence=0.9, spam_score=0.0, category="coding-agent"
    )
    auto, reasons = _passes_gates(
        session, _entry(), classification, _dims(low=False), None, temp_settings, CUTOFF
    )
    assert auto is False
    assert any("明确不相关" in r for r in reasons)


def test_soft_mode_still_blocks_high_spam(session, temp_settings):
    classification = SimpleNamespace(
        relevant=True, confidence=0.9, spam_score=0.8, category="coding-agent"
    )
    auto, reasons = _passes_gates(
        session, _entry(), classification, _dims(low=False), None, temp_settings, CUTOFF
    )
    assert auto is False
    assert any("spam" in r for r in reasons)
