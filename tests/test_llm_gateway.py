"""Gateway：配置档案解析、缓存、成本账本、预算与升级兼容。"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from viberadar.llm import (
    LLMConfig,
    LLMGateway,
    LLMPrice,
    LLMProfile,
    LLMResult,
    load_store,
    make_gateway,
)
from viberadar.settings import ConfigError
from viberadar.store import repo_ops


def _transport(content: str = '{"results": []}', seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
        )

    return httpx.MockTransport(handler)


def _save_profile(settings, price: bool = True) -> None:
    store = load_store(settings)
    profile = LLMProfile(
        name="work",
        protocol="openai",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        model_fast="m-fast",
        model_strong="m-strong",
        price=LLMPrice(input=1.0, output=2.0) if price else None,
    )
    store.save(
        LLMConfig(
            active="work",
            profiles={"work": profile},
            stages={"classify": "fast", "interest": "fast", "enrich": "strong"},
        )
    )


def test_gateway_config_provider_calls_model_and_records_cost(
    session_factory, temp_settings
):
    _save_profile(temp_settings)
    seen: list[httpx.Request] = []
    gateway = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport(seen=seen)
    )
    assert gateway.provider == "profile:work"
    result = gateway.chat_json(
        prompt_name="classify",
        system="s",
        payload={"items": []},
        model="fast",
        stage="classify",
    )
    assert result.ok and result.data == {"results": []}
    assert result.model == "m-fast"
    assert result.priced is True
    assert str(seen[0].url) == "https://llm.test/v1/chat/completions"

    with session_factory() as session:
        assert repo_ops.daily_cost(session) > Decimal("0")


def test_gateway_cache_hit_avoids_second_call(session_factory, temp_settings):
    _save_profile(temp_settings)
    seen: list[httpx.Request] = []
    gateway = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport(seen=seen)
    )
    first = gateway.chat_json(
        prompt_name="classify", system="s", payload={"items": []}, model="fast"
    )
    second = gateway.chat_json(
        prompt_name="classify", system="s", payload={"items": []}, model="fast"
    )
    assert first.ok and second.ok
    assert second.cached is True
    assert len(seen) == 1


def test_gateway_cache_invalidates_when_model_changes(session_factory, temp_settings):
    _save_profile(temp_settings)
    seen: list[httpx.Request] = []
    gateway = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport(seen=seen)
    )
    first = gateway.chat_json(
        prompt_name="classify", system="s", payload={"items": []}, model="fast"
    )
    assert first.model == "m-fast" and len(seen) == 1
    store = load_store(temp_settings)
    config = store.load()
    config.profiles["work"] = config.profiles["work"].model_copy(
        update={"model_fast": "m-fast-2"}
    )
    store.save(config)
    second = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport(seen=seen)
    ).chat_json(prompt_name="classify", system="s", payload={"items": []}, model="fast")
    assert second.cached is False and second.model == "m-fast-2"
    assert len(seen) == 2


def test_gateway_budget_hard_cap_returns_not_ok(session_factory, temp_settings):
    _save_profile(temp_settings)
    with session_factory() as session:
        repo_ops.record_cost(
            session,
            model="m-fast",
            input_tokens=1,
            cache_input_tokens=0,
            output_tokens=1,
            price_version="test",
            cost_usd=Decimal("999"),
        )
        session.commit()
    gateway = LLMGateway(session_factory, temp_settings, provider="config", transport=_transport())
    result = gateway.chat_json(
        prompt_name="classify", system="s", payload={"fresh": True}, model="fast"
    )
    assert not result.ok and "上限" in (result.error or "")


def test_gateway_auto_uses_file_then_mock(session_factory, temp_settings, monkeypatch):
    monkeypatch.delenv("VIBERADAR_LLM_PROVIDER", raising=False)
    auto = make_gateway(session_factory, temp_settings, choice="auto")
    assert auto is not None and auto.provider == "mock"
    _save_profile(temp_settings)
    auto = make_gateway(session_factory, temp_settings, choice="auto")
    assert auto is not None and auto.provider == "profile:work"


def test_gateway_none_disabled_and_unknown_profile(session_factory, temp_settings):
    assert make_gateway(session_factory, temp_settings, choice="none") is None
    disabled = LLMGateway(session_factory, temp_settings, provider="none")
    result = disabled.chat_json(prompt_name="x", system="", payload={}, model="fast")
    assert not result.ok
    with pytest.raises(ConfigError):
        LLMGateway(session_factory, temp_settings, provider="config")
    with pytest.raises(ConfigError):
        LLMGateway(session_factory, temp_settings, provider="ghost")


def test_gateway_mock_requires_mock_response(session_factory, temp_settings):
    gateway = LLMGateway(session_factory, temp_settings, provider="mock")
    missing = gateway.chat_json(prompt_name="x", system="", payload={}, model="fast")
    assert not missing.ok and "mock_response" in (missing.error or "")
    ok = gateway.chat_json(
        prompt_name="x", system="", payload={"p": 1}, model="fast", mock_response={"a": 1}
    )
    assert ok.ok and ok.data == {"a": 1}
    assert isinstance(ok, LLMResult)


def test_gateway_raw_chat_json_and_text(session_factory, temp_settings):
    _save_profile(temp_settings)
    gateway = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport('{"answer": 42}')
    )
    result = gateway.raw_chat(system="s", user="u", model="fast", json_mode=True)
    assert result.ok and result.data == {"answer": 42}
    text_gateway = LLMGateway(
        session_factory,
        temp_settings,
        provider="config",
        transport=_transport("纯文本回复"),
    )
    text_result = text_gateway.raw_chat(system="s", user="u2", model="fast", json_mode=False)
    assert text_result.ok and text_result.text == "纯文本回复"


def test_gateway_stage_mapping_overrides_tier(session_factory, temp_settings):
    store = load_store(temp_settings)
    store.save(
        LLMConfig(
            active="work",
            profiles={
                "work": LLMProfile(
                    name="work",
                    protocol="openai",
                    base_url="https://llm.test/v1",
                    api_key="sk-test",
                    model_fast="m-fast",
                    model_strong="m-strong",
                )
            },
            stages={"enrich": "fast"},
        )
    )
    gateway = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport()
    )
    overridden = gateway.chat_json(
        prompt_name="guide", system="s", payload={"x": 1}, model="strong", stage="enrich"
    )
    assert overridden.model == "m-fast"
    untouched = gateway.chat_json(
        prompt_name="classify", system="s", payload={"x": 1}, model="strong", stage="classify"
    )
    assert untouched.model == "m-strong"


def test_gateway_unpriced_model_records_zero_cost(session_factory, temp_settings):
    store = load_store(temp_settings)
    store.save(
        LLMConfig(
            active="work",
            profiles={
                "work": LLMProfile(
                    name="work",
                    protocol="openai",
                    base_url="https://llm.test/v1",
                    api_key="sk-test",
                    model_fast="unknown-model",
                    model_strong="unknown-model",
                )
            },
        )
    )
    gateway = LLMGateway(
        session_factory, temp_settings, provider="config", transport=_transport('{"ok": 1}')
    )
    result = gateway.chat_json(
        prompt_name="classify", system="s", payload={"x": 1}, model="fast"
    )
    assert result.ok and result.priced is False
    with session_factory() as session:
        assert repo_ops.daily_cost(session) == Decimal("0")
