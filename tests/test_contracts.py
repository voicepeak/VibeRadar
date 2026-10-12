"""契约测试（PRD 8.1/11.3）：JSON 解析、ID 对应、缺失值与时间。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from viberadar.contracts import (
    AccessInfo,
    CardCopyEn,
    Claim,
    Classification,
    ClassificationBatch,
    Enrichment,
    FeedbackStatePayload,
    InterestDims,
    MobileSupport,
    Pricing,
    RawItem,
    TryMode,
    TryStep,
    VerificationStatus,
    ensure_aware,
)


def test_raw_item_requires_timezone():
    with pytest.raises(ValidationError):
        RawItem(
            source_key="manual",
            external_id="1",
            source_url="https://example.com",
            title="t",
            fetched_at=datetime(2026, 10, 12, 0, 0),  # naive
        )


def test_raw_item_utc_normalized():
    item = RawItem(
        source_key="manual",
        external_id="1",
        source_url="https://example.com",
        title="t",
        fetched_at=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc),
    )
    assert item.fetched_at.tzinfo is not None


def test_ensure_aware_rejects_naive():
    with pytest.raises(ValueError):
        ensure_aware(datetime(2026, 1, 1))


def test_classification_confidence_bounds():
    with pytest.raises(ValidationError):
        Classification(item_id="x", confidence=1.2, spam_score=0.0, reason="r")
    with pytest.raises(ValidationError):
        Classification(item_id="x", confidence=0.5, spam_score=-0.1, reason="r")


def test_classification_category_must_be_taxonomy(temp_settings):
    with pytest.raises(ValidationError):
        Classification(
            item_id="x", confidence=0.9, spam_score=0.0, reason="r", category="not-a-category"
        )
    ok = Classification(
        item_id="x", confidence=0.9, spam_score=0.0, reason="r", category="coding-agent"
    )
    assert ok.category == "coding-agent"


def test_classification_batch_item_ids_unique():
    with pytest.raises(ValidationError):
        ClassificationBatch(
            results=[
                Classification(item_id="a", confidence=0.9, spam_score=0.0, reason="r"),
                Classification(item_id="a", confidence=0.9, spam_score=0.0, reason="r"),
            ]
        )


def test_access_display_text_combinations():
    checked = datetime(2026, 10, 12, tzinfo=timezone.utc)
    access = AccessInfo(
        pricing=Pricing.PAID,
        mobile_support=MobileSupport.UNSUPPORTED,
        login_required=True,
        api_key_required=True,
        platforms=["Windows"],
        checked_at=checked,
    )
    text = access.display_text()
    assert "收费" in text and "需登录" in text and "需要API key" in text and "Windows" in text
    unknown = AccessInfo(checked_at=checked)
    assert unknown.pricing == Pricing.UNKNOWN
    assert unknown.login_required is None


def test_enrichment_one_liner_length():
    access = AccessInfo(checked_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        Enrichment(
            one_liner="x" * 41,
            why_interesting="w",
            mode=TryMode.SETUP,
            steps=[],
            access=access,
        )


def test_enrichment_bilingual_copy_and_user_story():
    access = AccessInfo(checked_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    enrichment = Enrichment(
        one_liner="把会话上下文变成可回滚记录",
        why_interesting="可以对照修改前后的变化。",
        user_story="你是排查异常的开发者：回滚一次会话定位问题。",
        en=CardCopyEn(
            one_liner="Turns a session context into a rollbackable record.",
            why_interesting="Diff context changes.",
            user_story="You debug an agent: roll back one session to locate the issue.",
            steps=["Open", "Try"],
        ),
        mode=TryMode.DEMO,
        steps=[],
        access=access,
    )
    assert enrichment.en is not None and enrichment.user_story
    with pytest.raises(ValidationError):
        CardCopyEn(one_liner="x" * 121)


def test_enrichment_time_promise_requires_verified_direct():
    access = AccessInfo(checked_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        Enrichment(
            one_liner="ok",
            why_interesting="w",
            mode=TryMode.DEMO,
            steps=[],
            access=access,
            time_promise="约 30 秒开始体验",
        )
    with pytest.raises(ValidationError):
        Enrichment(
            one_liner="ok",
            why_interesting="w",
            mode=TryMode.DIRECT,
            steps=[],
            access=access,
            verification_status=VerificationStatus.DOC_CHECKED,
            time_promise="约 30 秒开始体验",
        )
    ok = Enrichment(
        one_liner="ok",
        why_interesting="w",
        mode=TryMode.DIRECT,
        steps=[],
        access=access,
        verification_status=VerificationStatus.HUMAN_TESTED,
        time_promise="约 30 秒开始体验",
    )
    assert ok.time_promise is not None


def test_enrichment_claim_evidence_check():
    access = AccessInfo(checked_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    enrichment = Enrichment(
        one_liner="ok",
        why_interesting="w",
        mode=TryMode.SETUP,
        steps=[TryStep(text="打开仓库")],
        access=access,
        claims=[
            Claim(text="免费", evidence_ids=["evd_missing"]),
            Claim(text="无证明", evidence_ids=[]),
        ],
    )
    errors = enrichment.claim_evidence_errors({"evd_present"})
    assert len(errors) == 2


def test_interest_dims_unknown():
    dims = InterestDims(creativity=5, playability=None, completeness=3, surprise=3, niche=3)
    assert dims.llm_interest() is None
    full = InterestDims(creativity=5, playability=5, completeness=5, surprise=5, niche=5)
    assert full.llm_interest() == 1.0
    taste = full.taste_match({"creativity": 0.35, "surprise": 0.25, "niche": 0.25, "playability": 0.15})
    assert taste == 1.0


def test_feedback_payload_explicit_null_tracking():
    payload = FeedbackStatePayload(rating_state=None)
    assert "rating_state" in payload.model_fields_set
    assert "tried" not in payload.model_fields_set
