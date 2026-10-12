"""管线子包（PRD 第 6 章 M1–M5.5）。"""

from .classify import ClassifyReport, classify_items, rule_classify
from .enrich import EnrichOutcome, enrich_product, fetch_page, infer_access
from .entities import ResolvedItem, ResolveReport, resolve_entities, was_published_recently
from .normalize import IdentityHints, extract_identity, strip_tracking
from .runner import PipelineReport, compute_cutoff, run_pipeline

__all__ = [
    "ClassifyReport",
    "EnrichOutcome",
    "IdentityHints",
    "PipelineReport",
    "ResolveReport",
    "ResolvedItem",
    "classify_items",
    "compute_cutoff",
    "enrich_product",
    "extract_identity",
    "fetch_page",
    "infer_access",
    "resolve_entities",
    "rule_classify",
    "run_pipeline",
    "strip_tracking",
    "was_published_recently",
]
