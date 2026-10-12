"""采集适配器契约（PRD M1）。

每 adapter 返回条目、分页水位、完成状态、错误、请求次数与配额。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from viberadar.contracts import RawItem, StageStatus


@dataclass
class FetchOutcome:
    items: list[RawItem] = field(default_factory=list)
    watermark: dict = field(default_factory=dict)
    complete: bool = True
    errors: list[str] = field(default_factory=list)
    request_count: int = 0
    quota_note: str | None = None
    coverage_note: str | None = None

    @property
    def status(self) -> StageStatus:
        if self.errors and not self.items:
            return StageStatus.FAILED
        if self.errors or not self.complete:
            return StageStatus.PARTIAL
        return StageStatus.SUCCESS


class AdapterError(RuntimeError):
    pass


class Adapter:
    source_key: str = "base"
    adapter_name: str = "base"

    def __init__(self, config: dict | None = None, transport=None) -> None:
        self.config = config or {}
        self.transport = transport

    def fetch(self, since: datetime, until: datetime, watermark: dict) -> FetchOutcome:
        raise NotImplementedError
