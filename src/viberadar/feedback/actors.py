"""匿名身份与限流（PRD M7/10.4/10.5）。

- 第一方随机标识；服务端带密钥派生 actor_hash，不保存原始 ID 到公开处。
- 禁用 cookie 时仍可阅读与提交单次反馈，统计标为身份不可用。
- 限流：匿名标识 30 次/分钟，IP 300 次/分钟（可调）；短期标识 24 小时到期。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

COOKIE_NAME = "vr_aid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def new_anonymous_id() -> str:
    return secrets.token_hex(16)


def derive_actor_hash(secret_key: str, anonymous_id: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"), anonymous_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


class RateLimiter:
    """单实例内存滑动窗口；多 worker 时需外部存储（P0 单实例）。"""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup > 3600:
                self._cleanup(now, window_seconds)
            bucket = self._buckets[key]
            while bucket and bucket[0] < now - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def _cleanup(self, now: float, window_seconds: float) -> None:
        stale = [
            key
            for key, bucket in self._buckets.items()
            if not bucket or bucket[-1] < now - window_seconds
        ]
        for key in stale:
            self._buckets.pop(key, None)
        self._last_cleanup = now


limiter = RateLimiter()
