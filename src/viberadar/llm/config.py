"""LLM 配置层：多 provider 档案、阶段映射、内置预设与本地配置文件（data/llm.json）。

- 密钥只落本地文件（gitignore 的 data/ 目录）或环境变量，不进数据库与日志。
- 档案 protocol 支持 openai（OpenAI 兼容 /chat/completions）与 anthropic（/v1/messages）。
- stages 把管线阶段（classify/interest/enrich/compose）映射到 fast/strong 两档模型。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from viberadar.settings import ConfigError, Settings

PROTOCOLS = ("openai", "anthropic")
TIERS = ("fast", "strong")
STAGES = ("classify", "interest", "enrich", "compose", "api", "default")
DEFAULT_TIER_MODELS = {"fast": "deepseek-flash", "strong": "deepseek-pro"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LLMPrice(_Model):
    """美元 / 百万 token；留空表示未定价（成本记 0 并在状态中标出）。"""

    input: float = 0.0
    output: float = 0.0
    cache_input: float | None = None
    input_high: float | None = None
    output_high: float | None = None

    @property
    def configured(self) -> bool:
        return self.input > 0 or self.output > 0


class LLMProfile(_Model):
    name: str
    label: str = ""
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: str = ""
    model_fast: str = ""
    model_strong: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 1600
    json_mode: bool = True
    timeout_seconds: float = 60.0
    price: LLMPrice | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url_http(cls, value: str) -> str:
        value = (value or "").strip().rstrip("/")
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return value

    def model_for(self, tier_or_model: str) -> str:
        if tier_or_model in TIERS:
            explicit = self.model_fast if tier_or_model == "fast" else self.model_strong
            return explicit or DEFAULT_TIER_MODELS[tier_or_model]
        return tier_or_model

    def masked_key(self) -> str:
        key = self.api_key or ""
        if not key:
            return ""
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}…{key[-4:]}"

    def public_dict(self) -> dict:
        data = self.model_dump(mode="json")
        data["api_key"] = ""
        data["api_key_masked"] = self.masked_key()
        data["api_key_set"] = bool(self.api_key)
        data["priced"] = bool(self.price and self.price.configured)
        return data


class LLMConfig(_Model):
    version: int = 1
    active: str = ""
    profiles: dict[str, LLMProfile] = {}
    stages: dict[str, str] = {}

    def active_profile(self) -> LLMProfile:
        if not self.profiles:
            raise ConfigError("尚未配置任何 LLM 档案（data/llm.json 为空）")
        name = self.active or next(iter(self.profiles))
        profile = self.profiles.get(name)
        if profile is None:
            raise ConfigError(f"active 档案不存在: {name}")
        return profile

    def get_profile(self, name: str) -> LLMProfile:
        profile = self.profiles.get(name)
        if profile is None:
            raise ConfigError(f"LLM 档案不存在: {name}")
        return profile

    def stage_tier(self, stage: str) -> str:
        value = (self.stages or {}).get(stage) or self.stages.get("default") or "fast"
        return value if value in TIERS else "fast"

    def stage_model(self, stage: str, profile: LLMProfile | None = None) -> str:
        return (profile or self.active_profile()).model_for(self.stage_tier(stage))

    def unique_names(self) -> list[str]:
        return sorted(self.profiles.keys())


class LLMConfigStore:
    """data/llm.json 读写：原子写入、权限收紧、缺失时返回 None。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> LLMConfig:
        config = self.load_optional()
        if config is None:
            raise ConfigError(f"缺少 LLM 配置文件: {self.path}")
        return config

    def load_optional(self) -> LLMConfig | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"LLM 配置文件 JSON 非法: {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"LLM 配置文件必须为对象: {self.path}")
        try:
            config = LLMConfig(**raw)
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"LLM 配置校验失败: {exc}") from exc
        if config.profiles and config.active and config.active not in config.profiles:
            raise ConfigError(f"LLM 配置 active 档案不存在: {config.active}")
        return config

    def save(self, config: LLMConfig) -> None:
        payload = config.model_dump(mode="json", exclude_none=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".llm-", suffix=".json.tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


PRESETS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com",
        "model_fast": "deepseek-chat",
        "model_strong": "deepseek-reasoner",
        "docs": "https://api-docs.deepseek.com/",
    },
    "openai": {
        "label": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_fast": "gpt-4o-mini",
        "model_strong": "gpt-4o",
        "docs": "https://platform.openai.com/docs/api-reference",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model_fast": "claude-3-5-haiku-latest",
        "model_strong": "claude-sonnet-4-5",
        "docs": "https://docs.anthropic.com/en/api/messages",
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "model_fast": "moonshot-v1-8k",
        "model_strong": "kimi-k2-0905-preview",
        "docs": "https://platform.moonshot.cn/docs",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model_fast": "glm-4-flash",
        "model_strong": "glm-4-plus",
        "docs": "https://open.bigmodel.cn/dev/api",
    },
    "ollama": {
        "label": "Ollama 本地",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_fast": "qwen2.5:7b",
        "model_strong": "qwen2.5:14b",
        "docs": "https://github.com/ollama/ollama/blob/main/docs/openai.md",
    },
}


def preset_profile(preset: str, name: str | None = None) -> LLMProfile:
    data = PRESETS.get(preset)
    if data is None:
        raise ConfigError(f"未知预设: {preset}（可选 {sorted(PRESETS)}）")
    payload = {k: v for k, v in data.items() if k != "docs"}
    payload["name"] = name or preset
    return LLMProfile(**payload)


def legacy_deepseek_profile(settings: Settings) -> LLMProfile:
    """旧式环境变量路径：VIBERADAR_LLM_PROVIDER=deepseek + DEEPSEEK_API_KEY。"""

    return LLMProfile(
        name="deepseek-env",
        label="DeepSeek（环境变量）",
        protocol="openai",
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=settings.deepseek_api_key or "",
    )


def load_store(settings: Settings) -> LLMConfigStore:
    return LLMConfigStore(settings.llm_config_path)
