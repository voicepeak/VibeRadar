"""Versioned, curator-verified recipes; never inferred at launch time."""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .common import ensure_aware


class TryTier(str, Enum):
    INSTANT = "instant"
    CONTAINER = "container"
    COMPOSE = "compose"
    GUIDED = "guided"
    BLOCKED = "blocked"


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str = Field(min_length=1)
    at: datetime
    device: str = Field(min_length=1)
    _aware = field_validator("at")(lambda v: ensure_aware(v, "verified.at"))


class TrialService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,30}$")
    image: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9./:@_-]+$")
    run: list[str] = Field(min_length=1, max_length=8)
    env: dict[str, str] = Field(default_factory=dict)


class TryRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,159}$")
    version: str = Field(min_length=1, max_length=64)
    tier: TryTier
    repo: str | None = None
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    image: str = "python:3.13-slim"
    image_archive_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    install: list[str] = Field(default_factory=list, max_length=12)
    run: list[str] = Field(default_factory=list, max_length=8)
    port: int = Field(default=3000, ge=1, le=65535)
    healthcheck: str = "/"
    llm_env: dict[str, str] = Field(default_factory=dict)
    package_hosts: list[str] = Field(default_factory=list, max_length=30)
    services: list[TrialService] = Field(default_factory=list, max_length=4)
    timeout_minutes: int = Field(default=8, ge=1, le=20)
    ttl_minutes: int = Field(default=120, ge=1, le=120)
    budget_usd: float = Field(default=.1, gt=0, le=2)
    license_checked: str = Field(default="", max_length=128)
    verified: Verification | None = None
    guide: list[str] = Field(default_factory=list)
    guide_en: list[str] = Field(default_factory=list)
    entry_url: str | None = None

    @field_validator("image")
    @classmethod
    def image_safe(cls, value):
        import re
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:@_-]+", value):
            raise ValueError("Invalid image")
        return value

    @field_validator("package_hosts")
    @classmethod
    def hosts_safe(cls, hosts):
        import re
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", h) or "." not in h for h in hosts):
            raise ValueError("Exact public package hostnames required")
        return hosts

    @field_validator("llm_env")
    @classmethod
    def virtual_only(cls, values):
        import re
        for key, value in values.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or value not in ("{{proxy_base}}", "{{virtual_key}}"):
                raise ValueError("LLM env accepts only proxy_base / virtual_key placeholders")
        return values

    @model_validator(mode="after")
    def executable(self):
        from urllib.parse import urlparse
        import re
        if self.image_archive_sha256 and not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image):
            raise ValueError("Local image archives require an immutable image content ID")
        if self.entry_url:
            from viberadar.ingest.fetch import validate_url_safety
            validate_url_safety(self.entry_url)
        if self.repo:
            parsed = urlparse(self.repo)
            if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment or
                not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", parsed.path)):
                raise ValueError("A plain GitHub repository URL is required")
        if self.repo and (not self.repo.startswith("https://github.com/") or not self.revision):
            raise ValueError("Source recipes require a GitHub repository pinned to a full commit SHA")
        if self.tier in (TryTier.CONTAINER, TryTier.COMPOSE) and not self.run:
            raise ValueError("Executable recipe requires run commands")
        if self.tier == TryTier.COMPOSE and not self.services:
            raise ValueError("Compose recipe requires declared services")
        if not self.healthcheck.startswith("/"):
            u = urlparse(self.healthcheck)
            if u.hostname != "127.0.0.1" or u.port != self.port or u.scheme != "http":
                raise ValueError("Healthcheck must target the declared loopback port")
        return self


class TrialSession(BaseModel):
    id: str
    status: str
    started_at: datetime
    ready_at: datetime | None = None
    expires_at: datetime
    url: str | None = None
    error: str | None = None
