"""采集子包：适配器、抓取边界与编排（PRD M1）。"""

from .base import Adapter, AdapterError, FetchOutcome
from .fetch import UnsafeURLError, safe_get, validate_url_safety
from .github import GitHubAdapter
from .hn import HNAdapter
from .manual import ManualAdapter
from .producthunt import ProductHuntAdapter
from .runner import ADAPTERS, IngestReport, build_adapter, run_all_sources, run_source

__all__ = [
    "ADAPTERS",
    "Adapter",
    "AdapterError",
    "FetchOutcome",
    "GitHubAdapter",
    "HNAdapter",
    "IngestReport",
    "ManualAdapter",
    "ProductHuntAdapter",
    "UnsafeURLError",
    "build_adapter",
    "run_all_sources",
    "run_source",
    "safe_get",
    "validate_url_safety",
]
