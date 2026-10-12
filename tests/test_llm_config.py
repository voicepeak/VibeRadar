"""LLM 配置层：档案存取、预设、阶段映射与 provider 解析。"""

from __future__ import annotations

import json

import pytest

from viberadar.llm import (
    LLMConfig,
    LLMPrice,
    LLMProfile,
    load_store,
    preset_profile,
)
from viberadar.settings import ConfigError


def _profile(**overrides) -> LLMProfile:
    payload = {
        "name": "work",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-1234567890abcdef",
        "model_fast": "deepseek-chat",
        "model_strong": "deepseek-reasoner",
    }
    payload.update(overrides)
    return LLMProfile(**payload)


def test_store_roundtrip_and_masking(temp_settings):
    store = load_store(temp_settings)
    assert store.load_optional() is None
    config = LLMConfig(
        active="work",
        profiles={"work": _profile(price=LLMPrice(input=0.5, output=1.5))},
        stages={"classify": "fast", "enrich": "strong"},
    )
    store.save(config)

    loaded = store.load()
    profile = loaded.active_profile()
    assert profile.model_fast == "deepseek-chat"
    assert loaded.stage_model("enrich") == "deepseek-reasoner"
    assert loaded.stage_model("classify") == "deepseek-chat"
    assert profile.masked_key() == "sk-1…cdef"

    public = profile.public_dict()
    assert public["api_key"] == ""
    assert public["api_key_set"] is True
    assert public["priced"] is True
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["profiles"]["work"]["api_key"] == "sk-1234567890abcdef"


def test_store_rejects_invalid_config(temp_settings):
    store = load_store(temp_settings)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "active": "missing",
                "profiles": {"work": _profile().model_dump()},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        store.load()


def test_profile_validates_base_url():
    with pytest.raises(Exception):
        _profile(base_url="ftp://example.com")


def test_presets_cover_protocols():
    deepseek = preset_profile("deepseek")
    assert deepseek.protocol == "openai"
    assert deepseek.model_fast
    anthropic = preset_profile("anthropic")
    assert anthropic.protocol == "anthropic"
    assert anthropic.model_strong
    with pytest.raises(ConfigError):
        preset_profile("nope")


def test_stage_mapping_defaults():
    profile = _profile()
    config = LLMConfig(active="work", profiles={"work": profile})
    assert config.stage_tier("classify") == "fast"
    assert profile.model_for("fast") == "deepseek-chat"
    assert profile.model_for("explicit-model") == "explicit-model"
