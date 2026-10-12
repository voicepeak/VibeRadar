"""全局设置：加载 YAML 配置、校验、版本哈希与运行环境（PRD 9.3）。

原则：
- 版本文件不含 secret；secret 只来自环境变量或部署引用。
- 非法配置阻止新任务（调用方捕获 ValueError 保持旧版本继续服务）。
- 每个运行冻结 config_hash。
"""

from __future__ import annotations

import hashlib
import os
from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROJECT_ROOT = Path(os.environ.get("VIBERADAR_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("VIBERADAR_CONFIG_DIR", PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("VIBERADAR_DATA_DIR", PROJECT_ROOT / "data"))


class ConfigError(ValueError):
    """配置缺失或不合法；不得用于运行新任务。"""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ScheduleConfig(_Model):
    ingest_every_hours: int = 4
    cutoff: str = "07:30"
    review_deadline: str = "08:35"
    publish: str = "09:00"
    failure_check: str = "09:05"


class WindowConfig(_Model):
    main_hours: int = 24
    watermark_overlap_hours: int = 2
    late_discovery_days: int = 7
    republish_cooldown_days: int = 30


class LimitsConfig(_Model):
    candidate_limit: int = 20
    public_target: int = 6
    public_min: int = 5
    public_max: int = 12
    deep_enrich_limit: int = 40
    strong_model_limit: int = 12
    low_heat_explore_quota: int = 10
    classification_batch: int = 15
    classify_material_chars: int = 600


class QualityGatesConfig(_Model):
    """质量门槛配置。mode=soft（宽进）：仅“明确排除/运营性去重”硬拦截，
    其余质量问题降级为候选质量标记；mode=strict：PRD 5.3 全部门槛生效。"""

    mode: str = "soft"
    min_confidence: float = 0.8
    manual_review_confidence: float = 0.5
    max_spam_score: float = 0.5
    min_interest: float = 0.6
    require_evidence: bool = True
    require_reachable: bool = True


class RateLimitsConfig(_Model):
    anonymous_per_minute: int = 30
    ip_per_minute: int = 300


class RetentionConfig(_Model):
    source_metadata_days: int = 90
    evidence_excerpt_max_chars: int = 200
    anonymous_event_days: int = 90
    rate_limit_identity_hours: int = 24
    audit_days: int = 365


class PipelineConfig(_Model):
    schema_version: str = "1"
    timezone: str = "Asia/Shanghai"
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    window: WindowConfig = Field(default_factory=WindowConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    quality_gates: QualityGatesConfig = Field(default_factory=QualityGatesConfig)
    rate_limits: RateLimitsConfig = Field(default_factory=RateLimitsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def cutoff_time(self) -> time:
        return _parse_hhmm(self.schedule.cutoff)

    @property
    def review_deadline_time(self) -> time:
        return _parse_hhmm(self.schedule.review_deadline)

    @property
    def publish_time(self) -> time:
        return _parse_hhmm(self.schedule.publish)

    @property
    def failure_check_time(self) -> time:
        return _parse_hhmm(self.schedule.failure_check)


class CategoryDef(_Model):
    key: str
    label: str
    label_en: str = ""
    description: str = ""


class TaxonomyConfig(_Model):
    schema_version: str = "1"
    categories: list[CategoryDef]
    product_category_keys: list[str]
    max_tags_per_product: int = 3

    def labels(self, locale: str = "zh") -> dict[str, str]:
        from viberadar.i18n import normalize_locale

        if normalize_locale(locale) == "en":
            return {c.key: (c.label_en or c.label) for c in self.categories}
        return {c.key: c.label for c in self.categories}


class RubricConfig(_Model):
    schema_version: str = "1"
    version: str = "rubric-1.0"
    dimensions: dict = Field(default_factory=dict)
    interest_formula: dict = Field(default_factory=dict)
    taste_match: dict = Field(default_factory=dict)
    score_formula: dict = Field(default_factory=dict)
    bonuses: dict = Field(default_factory=dict)
    low_heat_percentile_max: float = 0.25
    novelty: dict = Field(default_factory=dict)
    source_signal: dict = Field(default_factory=dict)
    velocity: dict = Field(default_factory=dict)
    cross_source_min_sources: int = 2
    diversity: dict = Field(default_factory=dict)
    trial_categories: list[str] = Field(default_factory=list)

    @property
    def taste_weights(self) -> dict[str, float]:
        return dict(self.taste_match.get("weights") or {})


class SourceDef(_Model):
    key: str
    label: str
    tier: int = 1
    enabled: bool = True
    adapter: str
    license_note: str = ""
    config: dict = Field(default_factory=dict)


class SourcesConfig(_Model):
    schema_version: str = "1"
    sources: dict[str, SourceDef]

    def enabled(self) -> list[SourceDef]:
        return [s for s in self.sources.values() if s.enabled]


class PricingConfig(_Model):
    schema_version: str = "1"
    checked_at: str = ""
    currency: str = "USD"
    providers: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)

    @property
    def price_version(self) -> str:
        return f"pricing-{self.checked_at}"

    def model_price(self, provider: str, model: str) -> dict:
        try:
            return dict(self.providers[provider]["models"][model])
        except KeyError as exc:
            raise ConfigError(f"未配置模型价格: {provider}/{model}") from exc

    def find_price(self, model: str) -> dict | None:
        """按模型名在所有 provider 中查价；查不到返回 None（成本记 0 并标注未定价）。"""
        for provider in self.providers.values():
            models = provider.get("models") if isinstance(provider, dict) else None
            if isinstance(models, dict) and model in models:
                return dict(models[model])
        return None


class Settings(_Model):
    pipeline: PipelineConfig
    taxonomy: TaxonomyConfig
    rubric: RubricConfig
    sources: SourcesConfig
    pricing: PricingConfig
    config_hash: str
    prompt_hash: str
    config_dir: Path
    data_dir: Path

    @property
    def db_path(self) -> Path:
        override = os.environ.get("VIBERADAR_DB_PATH")
        if override:
            return Path(override)
        return self.data_dir / "viberadar.sqlite3"

    @property
    def public_dir(self) -> Path:
        return self.data_dir / "public"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def llm_provider(self) -> str:
        return os.environ.get("VIBERADAR_LLM_PROVIDER", "auto").lower()

    @property
    def llm_config_path(self) -> Path:
        override = os.environ.get("VIBERADAR_LLM_CONFIG")
        if override:
            return Path(override)
        return self.data_dir / "llm.json"

    @property
    def llm_api_token(self) -> str | None:
        return os.environ.get("VIBERADAR_LLM_API_TOKEN")

    @property
    def deepseek_api_key(self) -> str | None:
        return os.environ.get("DEEPSEEK_API_KEY")

    @property
    def deepseek_base_url(self) -> str:
        return os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    @property
    def feishu_webhook_url(self) -> str | None:
        return os.environ.get("VIBERADAR_FEISHU_WEBHOOK_URL")

    @property
    def admin_token(self) -> str | None:
        return os.environ.get("VIBERADAR_ADMIN_TOKEN")

    @property
    def site_base_url(self) -> str:
        return os.environ.get("VIBERADAR_SITE_URL", "http://127.0.0.1:8000")

    @property
    def secret_key(self) -> str:
        return os.environ.get("VIBERADAR_SECRET_KEY", "dev-only-secret-change-me")

    @property
    def strict_llm(self) -> bool:
        return os.environ.get("VIBERADAR_STRICT_LLM", "0") == "1"


def _parse_hhmm(value: str) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"时间格式非法（HH:MM）: {value}") from exc


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"缺少配置文件: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件必须为映射: {path}")
    return data


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if path.exists():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _hash_dir(path: Path) -> str:
    digest = hashlib.sha256()
    if path.exists():
        for file in sorted(path.glob("*.txt")):
            digest.update(file.name.encode("utf-8"))
            digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


def load_settings(config_dir: Path | None = None, data_dir: Path | None = None) -> Settings:
    cdir = Path(config_dir or CONFIG_DIR)
    ddir = Path(data_dir or DATA_DIR)
    try:
        pipeline = PipelineConfig(**_read_yaml(cdir / "pipeline.yaml"))
        taxonomy = TaxonomyConfig(**_read_yaml(cdir / "taxonomy.yaml"))
        rubric = RubricConfig(**_read_yaml(cdir / "rubric.yaml"))
        sources = SourcesConfig(**_read_yaml(cdir / "sources.yaml"))
        pricing = PricingConfig(**_read_yaml(cdir / "pricing.yaml"))
    except ValidationError as exc:
        raise ConfigError(f"配置校验失败: {exc}") from exc

    product_keys = set(taxonomy.product_category_keys)
    unknown = product_keys - {c.key for c in taxonomy.categories}
    if unknown:
        raise ConfigError(f"taxonomy.product_category_keys 含未定义分类: {sorted(unknown)}")
    for key in rubric.trial_categories:
        if key not in product_keys:
            raise ConfigError(f"rubric.trial_categories 含未定义分类: {key}")
    if not (0 <= pipeline.quality_gates.manual_review_confidence <= pipeline.quality_gates.min_confidence <= 1):
        raise ConfigError("quality_gates 置信度阈值必须满足 0 ≤ 人工 < 自动 ≤ 1")
    if pipeline.limits.public_min > pipeline.limits.public_target > pipeline.limits.public_max:
        raise ConfigError("limits 必须满足 public_min ≤ public_target ≤ public_max")

    return Settings(
        pipeline=pipeline,
        taxonomy=taxonomy,
        rubric=rubric,
        sources=sources,
        pricing=pricing,
        config_hash=_hash_files(
            [
                cdir / "pipeline.yaml",
                cdir / "taxonomy.yaml",
                cdir / "rubric.yaml",
                cdir / "sources.yaml",
                cdir / "pricing.yaml",
            ]
        ),
        prompt_hash=_hash_dir(cdir / "prompts"),
        config_dir=cdir,
        data_dir=ddir,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def taxonomy_keys() -> set[str]:
    return {c.key for c in get_settings().taxonomy.categories}


def read_prompt(name: str) -> str:
    path = get_settings().config_dir / "prompts" / f"{name}.txt"
    if not path.exists():
        raise ConfigError(f"缺少 Prompt 模板: {path}")
    return path.read_text(encoding="utf-8")
