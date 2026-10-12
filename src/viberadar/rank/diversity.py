"""多样性与质量选择（PRD 7.3）：贪心选择，不因比例删除合格候选。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class DiversityItem:
    key: str                 # product_id（同产品一期最多 1 条）
    category: str
    score: float
    interest: float = 0.0
    topic_key: str | None = None
    locked: bool = False
    extra: dict = field(default_factory=dict)


def review_pool_limit(
    items: list[DiversityItem], candidate_limit: int, category_max: int = 8
) -> list[DiversityItem]:
    """复核池：Top20 上限，同一主分类最多 8 条；候选不足时不删合格候选。"""
    ordered = sorted(items, key=lambda i: (-i.score, i.key))
    selected: list[DiversityItem] = []
    counts: dict[str, int] = {}
    for item in ordered:
        if len(selected) >= candidate_limit:
            break
        if counts.get(item.category, 0) >= category_max:
            continue
        selected.append(item)
        counts[item.category] = counts.get(item.category, 0) + 1
    return selected


def category_cap(n: int, ratio: float = 0.4) -> int:
    return max(1, math.ceil(ratio * n))


def select_public_items(
    items: list[DiversityItem],
    target: int,
    ratio: float = 0.4,
    min_categories: int = 3,
    same_topic_max: int = 2,
    ratio_min_count: int = 5,
) -> list[DiversityItem]:
    """公开列表：单分类 ≤ ceil(0.4×N)；N≥6 且类别足够时优先 ≥3 类；同主题 ≤2。

    短刊（N < ratio_min_count，默认 5）不应用比例上限，避免合格候选被再次削减。
    顺序按分数降序输出；locked（人工锁定）项先占位。
    """
    if not items:
        return []

    pool = sorted(items, key=lambda i: (-i.score, i.key))
    n = min(target, len(pool))
    cap = category_cap(n, ratio) if n >= ratio_min_count else n

    selected: list[DiversityItem] = []
    cat_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    chosen_keys: set[str] = set()

    def fits(item: DiversityItem) -> bool:
        if item.key in chosen_keys:
            return False
        if cat_counts.get(item.category, 0) >= cap:
            return False
        if item.topic_key and topic_counts.get(item.topic_key, 0) >= same_topic_max:
            return False
        return True

    def take(item: DiversityItem) -> None:
        selected.append(item)
        chosen_keys.add(item.key)
        cat_counts[item.category] = cat_counts.get(item.category, 0) + 1
        if item.topic_key:
            topic_counts[item.topic_key] = topic_counts.get(item.topic_key, 0) + 1

    # 人工锁定项先占位
    for item in pool:
        if item.locked and fits(item) and len(selected) < n:
            take(item)

    # 第一遍：在 N≥6 时保证覆盖至少 3 类
    if n >= 6:
        seen = set(cat_counts.keys())
        for item in pool:
            if len(seen) >= min_categories or len(selected) >= n:
                break
            if item.category not in seen and fits(item):
                take(item)
                seen.add(item.category)

    # 第二遍：按分数填充
    for item in pool:
        if len(selected) >= n:
            break
        if fits(item):
            take(item)

    return selected


def short_edition_items(items: list[DiversityItem], public_min: int) -> tuple[list[DiversityItem], str]:
    """返回 (条目, content_status)：少于 public_min 为短刊，0 条为空刊。"""
    if not items:
        return [], "empty"
    if len(items) < public_min:
        return items, "short"
    return items, "normal"
