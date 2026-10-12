"""公共类型、ID 策略与枚举（PRD 8.1/8.3）。"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """单调性足够充分的 ULID（48bit 时间戳 + 80bit 随机）。"""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    return f"{prefix}_{new_ulid().lower()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime, field: str = "datetime") -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{field} 必须带时区（PRD 5.1：所有时间带时区）")
    return dt.astimezone(timezone.utc)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditionStatus(str, Enum):
    DRAFT = "draft"
    REVIEWING = "reviewing"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"


class ContentStatus(str, Enum):
    NORMAL = "normal"
    SHORT = "short"
    EMPTY = "empty"
    DEGRADED = "degraded"


class StageStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    RETRYABLE_FAILED = "retryable_failed"
    UNKNOWN = "unknown"
    PERMANENT_FAILED = "permanent_failed"


class VerificationStatus(str, Enum):
    DOC_CHECKED = "doc_checked"
    HUMAN_TESTED = "human_tested"
    PROBE_TESTED = "probe_tested"
    UNVERIFIED = "unverified"
    FAILED = "failed"


class TryMode(str, Enum):
    DIRECT = "direct"
    DEMO = "demo"
    SETUP = "setup"


class Pricing(str, Enum):
    FREE = "free"
    FREEMIUM = "freemium"
    PAID = "paid"
    UNKNOWN = "unknown"


class MobileSupport(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class LaunchKind(str, Enum):
    LAUNCH = "launch"
    REPO_CREATED = "repo_created"
    UPDATE = "update"
    DISCOVERY = "discovery"


class ProductStatus(str, Enum):
    ACTIVE = "active"
    MERGED = "merged"
    BLOCKED = "blocked"
    RETRACTED = "retracted"


class EventType(str, Enum):
    EDITION_VIEW = "edition_view"
    CARD_IMPRESSION = "card_impression"
    OUTBOUND_CLICK = "outbound_click"
    TRIED = "tried"
    INTEREST = "interest"
    BORING = "boring"
    TRY_FAILED = "try_failed"
    SAVE = "save"
    UNSAVE = "unsave"
    REPORT_ISSUE = "report_issue"


class RatingState(str, Enum):
    INTEREST = "interest"
    BORING = "boring"
    TRY_FAILED = "try_failed"


class FailureReason(str, Enum):
    ENTRY_DEAD = "entry_dead"
    LOGIN_REQUIRED = "login_required"
    PAID = "paid"
    MISSING_API_KEY = "missing_api_key"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    STEPS_MISMATCH = "steps_mismatch"
    OTHER = "other"


class IssueType(str, Enum):
    LINK_DEAD = "link_dead"
    PRICING_WRONG = "pricing_wrong"
    PLATFORM_WRONG = "platform_wrong"
    STEPS_WRONG = "steps_wrong"
    OTHER = "other"


class IssueStatus(str, Enum):
    NEW = "new"
    REVIEWING = "reviewing"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class CuratorActionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    REORDER = "reorder"
    UNDO = "undo"
    LOCK = "lock"


class Channel(str, Enum):
    WEB = "web"
    FEISHU = "feishu"
    RSS = "rss"
    MANUAL = "manual"


class CostRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    input_tokens: int = 0
    cache_input_tokens: int = 0
    output_tokens: int = 0
    price_version: str
    cost_usd: Decimal
    retry: bool = False
