"""分类与垃圾内容判断（PRD M3/附录 D.1）。

规则先排明显纯新闻；轻量模型批量分类；阈值：
≥0.8 可自动候选，0.5≤值<0.8 需人工，<0.5 等待补证据。
置信度是待校准信号，不是真实正确概率。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from viberadar.contracts import Classification, ClassificationBatch
from viberadar.llm import COMMON_SYSTEM, LLMGateway
from viberadar.llm.gateway import BudgetExceeded
from viberadar.settings import Settings, get_settings
from viberadar.store import repo_pipeline

from .entities import ResolvedItem

NEWS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(raises|raised|secures)\s+\$?\d", re.I), "纯融资新闻"),
    (re.compile(r"series\s+[a-e]\b", re.I), "纯融资新闻"),
    (re.compile(r"\b(ipo|acquired by|acquisition)\b", re.I), "公司新闻/收购"),
    (re.compile(r"(funding|valuation)\s+(round|at)\b", re.I), "纯融资新闻"),
    (re.compile(r"^(show hn:\s*)?(ask hn|tell hn)", re.I), "非产品帖"),
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "mcp-skill": ["mcp", "model context protocol", " skill", "server for claude"],
    "coding-agent": [
        "coding agent", "code agent", "agent harness", "multi-agent", "agent orchestrat",
        "autonomous coding", "claude code", "cursor agent", "codex",
    ],
    "agent-hygiene": [
        "context", "sandbox", "memory", "quota", "test", "debug", "review", "lint",
        "rollback", "checkpoint", "token usage",
    ],
    "app-builder": [
        "prompt to app", "website builder", "ui generat", "app builder", "generate a website",
        "landing page", "no-code", "vibe code an app",
    ],
    "editor-extension": [
        "vscode", "vs code", "jetbrains", "ide plugin", "editor extension", "neovim",
        "sublime", "session manager",
    ],
    "infra-model": [
        "router", "inference", "eval", "gateway", "proxy", "model serving", "load balanc",
        "benchmark harness",
    ],
    "creative-toy": [
        "game", "toy", "play", "fun", "canvas", "animation", "interactive art",
        "soundscape", "pixel",
    ],
}


@dataclass
class ClassifyReport:
    classified: int = 0
    auto_eligible: int = 0
    needs_review: int = 0
    excluded: int = 0
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


def rule_classify(title: str, description: str | None, metadata: dict | None) -> Classification | None:
    """确定性规则：明显新闻排除；强关键词分类。无法判定返回 None。"""
    text = f"{title} {description or ''}".lower()
    topics = [str(t).lower() for t in ((metadata or {}).get("topics") or [])]
    for pattern, reason in NEWS_PATTERNS:
        if pattern.search(title):
            return Classification(
                item_id="",
                relevant=False,
                category=None,
                confidence=0.9,
                spam_score=0.0,
                reason=f"规则排除：{reason}",
                evidence_ids=[],
                needs_review=False,
            )

    topic_map = {
        "mcp": "mcp-skill",
        "model-context-protocol": "mcp-skill",
        "ai-agent": "coding-agent",
        "agents": "coding-agent",
        "coding-agent": "coding-agent",
        "developer-tools": "editor-extension",
        "vscode-extension": "editor-extension",
        "llm": None,  # 单独 llm 主题不足以分类
    }
    for topic in topics:
        mapped = topic_map.get(topic)
        if mapped:
            return Classification(
                item_id="",
                relevant=True,
                category=mapped,
                confidence=0.84,
                spam_score=0.0,
                reason=f"规则分类：repo topics 含 {topic}",
                evidence_ids=[],
                needs_review=False,
            )

    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits:
            scores[category] = hits
    if scores:
        best = max(scores, key=lambda k: (scores[k], k))
        if scores[best] >= 2:
            # 仅强匹配直接采用；弱匹配交给模型判定（PRD M3：规则先排明显纯新闻）
            return Classification(
                item_id="",
                relevant=True,
                category=best,
                confidence=0.82,
                spam_score=0.0,
                reason=f"规则分类：关键词匹配 {scores[best]} 处",
                evidence_ids=[],
                needs_review=False,
            )
    return None


def _band(classification: Classification, settings: Settings) -> Classification:
    gates = settings.pipeline.quality_gates
    product_keys = set(settings.taxonomy.product_category_keys)
    needs_review = classification.needs_review
    if classification.relevant is None:
        needs_review = True
    if classification.confidence < gates.min_confidence:
        needs_review = True
    if classification.category is not None and classification.category not in product_keys:
        needs_review = True
    if classification.spam_score >= gates.max_spam_score:
        needs_review = True
    return classification.model_copy(update={"needs_review": needs_review})


def classify_items(
    session: Session,
    resolved: list[ResolvedItem],
    edition_date: date,
    run_id: str,
    settings: Settings | None = None,
    llm: LLMGateway | None = None,
    mock_llm_response: dict | None = None,
) -> ClassifyReport:
    settings = settings or get_settings()
    report = ClassifyReport()
    pending: list[ResolvedItem] = []

    for entry in resolved:
        raw = entry.raw_item
        rule = rule_classify(raw.title, raw.description, raw.metadata_json)
        if rule is not None:
            rule = rule.model_copy(update={"item_id": raw.id})
            if rule.relevant is False:
                report.excluded += 1
            classification = _band(rule, settings)
            repo_pipeline.upsert_classification(
                session,
                edition_date,
                classification,
                product_id=entry.product.id,
                event_id=entry.event.id,
                raw_item_id=raw.id,
                source="rule",
            )
            report.classified += 1
            if _is_auto_eligible(classification, settings):
                report.auto_eligible += 1
            continue
        pending.append(entry)

    if pending and llm is not None:
        llm_results = _llm_classify(session, pending, settings, llm, mock_llm_response, report)
    else:
        llm_results = {}

    for entry in pending:
        raw = entry.raw_item
        result = llm_results.get(raw.id)
        if result is None:
            result = Classification(
                item_id=raw.id,
                relevant=None,
                category=None,
                confidence=0.0,
                spam_score=0.0,
                reason="无法自动判定，等待补证据（未调用或不可用）",
                evidence_ids=[],
                needs_review=True,
            )
        result = _band(result, settings)
        repo_pipeline.upsert_classification(
            session,
            edition_date,
            result,
            product_id=entry.product.id,
            event_id=entry.event.id,
            raw_item_id=raw.id,
            source="llm" if raw.id in llm_results else "rule",
        )
        report.classified += 1
        if result.needs_review:
            report.needs_review += 1
        elif _is_auto_eligible(result, settings):
            report.auto_eligible += 1
    return report


def _is_auto_eligible(classification: Classification, settings: Settings) -> bool:
    gates = settings.pipeline.quality_gates
    return (
        classification.relevant is True
        and not classification.needs_review
        and classification.confidence >= gates.min_confidence
        and classification.spam_score < gates.max_spam_score
        and classification.category in set(settings.taxonomy.product_category_keys)
    )


def _material(raw, limit: int) -> dict:
    return {
        "item_id": raw.id,
        "title": raw.title,
        "description": (raw.description or "")[:limit],
        "evidence": [],
    }


def _llm_classify(
    session: Session,
    pending: list[ResolvedItem],
    settings: Settings,
    llm: LLMGateway,
    mock_llm_response: dict | None,
    report: ClassifyReport,
) -> dict[str, Classification]:
    batch_size = settings.pipeline.limits.classification_batch
    limit = settings.pipeline.limits.classify_material_chars
    payload_rules = {
        "relevant": "AI 辅助软件构建、agent 工作流、vibe-coded 作品、生态工具",
        "exclude": "纯融资/公司新闻、纯模型发布、无产品入口的概念帖、通用聊天应用",
    }
    results: dict[str, Classification] = {}
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        payload = {
            "rules": payload_rules,
            "taxonomy": [
                {"key": c.key, "label": c.label, "description": c.description}
                for c in settings.taxonomy.categories
                if c.key in settings.taxonomy.product_category_keys
            ],
            "items": [_material(e.raw_item, limit) for e in batch],
        }
        response = llm.chat_json(
            prompt_name="classify",
            system=COMMON_SYSTEM,
            payload=payload,
            model="fast",
            run_id=None,
            stage="classify",
            max_output_tokens=2000,
            mock_response=mock_llm_response,
        )
        if not response.ok or response.data is None:
            report.errors.append(f"LLM 分类不可用: {response.error}")
            continue
        try:
            parsed = ClassificationBatch(**response.data)
        except Exception as exc:  # noqa: BLE001 —— schema 失败只影响本批次
            report.errors.append(f"分类 schema 校验失败: {exc}")
            continue
        expected = {e.raw_item.id for e in batch}
        got = {c.item_id for c in parsed.results}
        if got != expected:
            missing = expected - got
            report.errors.append(f"批次缺条: {sorted(missing)}")
        for classification in parsed.results:
            if classification.item_id in expected:
                results[classification.item_id] = classification
        report.cost_usd += float(response.cost_usd)
    return results
