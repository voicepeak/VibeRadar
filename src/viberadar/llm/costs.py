"""成本与 token 估算工具（无数据库依赖，可被其他工具复用）。"""

from __future__ import annotations

import hashlib
from decimal import Decimal


def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 约 1.5 字符/token，其余约 4 字符/token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))


def compute_cost(
    price: dict,
    input_tokens: int,
    cache_input_tokens: int,
    output_tokens: int,
    tier: str = "low",
) -> Decimal:
    if tier == "high":
        in_price = float(price.get("input_high", price.get("input", 0)) or price.get("input", 0))
        out_price = float(
            price.get("output_high", price.get("output", 0)) or price.get("output", 0)
        )
    else:
        in_price = float(price.get("input", 0))
        out_price = float(price.get("output", 0))
    cache_price = float(price.get("cache_input", in_price) or in_price)
    non_cache = max(0, input_tokens - cache_input_tokens)
    cost = (
        non_cache * in_price
        + cache_input_tokens * cache_price
        + output_tokens * out_price
    ) / 1_000_000
    return Decimal(str(round(cost, 8)))


def prompt_digest(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
