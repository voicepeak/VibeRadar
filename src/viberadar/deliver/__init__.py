"""分发子包（PRD M6）：发送账本、飞书、RSS。"""

from .feishu import DeliveryResult, build_card, send_feishu
from .rss import build_rss
from .runner import (
    dispatch_edition,
    dispatch_feishu,
    record_web_delivery,
    retry_failed_deliveries,
)

__all__ = [
    "DeliveryResult",
    "build_card",
    "build_rss",
    "dispatch_edition",
    "dispatch_feishu",
    "record_web_delivery",
    "retry_failed_deliveries",
    "send_feishu",
]
