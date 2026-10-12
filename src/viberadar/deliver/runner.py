"""投递编排与账本（PRD M6/5.4）。

- 每个投递用 (edition_id, channel, destination, dispatch_version) 唯一键。
- 网页公开与飞书投递解耦：网页成功、飞书失败时只补飞书。
- 重放默认 dry_run 不发送。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from viberadar.compose.edition import build_edition_view
from viberadar.contracts import DeliveryStatus, utcnow
from viberadar.settings import Settings, get_settings
from viberadar.store import repo_editions

from .feishu import DeliveryResult, build_card, send_feishu


def record_web_delivery(session: Session, edition_id: str, success: bool, error: str | None = None) -> None:
    """网页归档生成结果写入账本（web 渠道）。"""
    attempt, _ = repo_editions.get_or_create_delivery(
        session, edition_id, channel="web", destination="site"
    )
    status = DeliveryStatus.SENT.value if success else DeliveryStatus.RETRYABLE_FAILED.value
    repo_editions.update_delivery_status(session, attempt, status, error=error)


def dispatch_feishu(
    session: Session,
    edition,  # EditionRow
    settings: Settings | None = None,
    transport=None,
    dispatch_version: int | None = None,
    dry_run: bool = False,
) -> DeliveryResult | None:
    settings = settings or get_settings()
    webhook = settings.feishu_webhook_url
    existing = [
        d for d in repo_editions.list_deliveries(session, edition.id) if d.channel == "feishu"
    ]
    if dispatch_version is None:
        # 默认复用当前最新版本；再次通知需显式增加 dispatch_version（PRD 5.4）
        dispatch_version = max((d.dispatch_version for d in existing), default=0) or 1
    if dry_run:
        return None
    attempt, _ = repo_editions.get_or_create_delivery(
        session,
        edition.id,
        channel="feishu",
        destination="webhook",
        dispatch_version=dispatch_version,
    )
    if attempt.status == DeliveryStatus.SENT.value:
        return DeliveryResult(status=DeliveryStatus.SENT.value, external_message_id=attempt.external_message_id)
    if attempt.status == DeliveryStatus.UNKNOWN.value:
        # unknown 先核对，不盲目自动重发（PRD 5.4/E15）
        return DeliveryResult(
            status=DeliveryStatus.UNKNOWN.value,
            error="上次发送超时且送达未知，需人工核对后再决定",
        )

    view = build_edition_view(session, edition)
    payload = build_card(view, settings)
    if not webhook:
        repo_editions.update_delivery_status(
            session,
            attempt,
            DeliveryStatus.PERMANENT_FAILED.value,
            error="缺少 VIBERADAR_FEISHU_WEBHOOK_URL；仅生成本地预览，不算推送成功",
        )
        return DeliveryResult(
            status=DeliveryStatus.PERMANENT_FAILED.value,
            error="未配置 webhook（凭据缺失只能生成预览）",
        )

    result = send_feishu(webhook, payload, transport=transport)
    repo_editions.update_delivery_status(
        session,
        attempt,
        result.status,
        external_message_id=result.external_message_id,
        error=result.error,
        http_status=result.http_status,
    )
    return result


def dispatch_edition(
    session: Session,
    edition,
    settings: Settings | None = None,
    channels: tuple[str, ...] = ("feishu",),
    transport=None,
    dry_run: bool = False,
    dispatch_version: int | None = None,
) -> list[DeliveryResult | None]:
    settings = settings or get_settings()
    results: list[DeliveryResult | None] = []
    for channel in channels:
        if channel == "feishu":
            results.append(
                dispatch_feishu(
                    session,
                    edition,
                    settings=settings,
                    transport=transport,
                    dispatch_version=dispatch_version,
                    dry_run=dry_run,
                )
            )
        else:
            raise ValueError(f"未知渠道: {channel}（不通过 UI 模糊按钮执行大范围重建）")
    return results


def retry_failed_deliveries(
    session: Session, settings: Settings | None = None, transport=None, dry_run: bool = False
) -> list[tuple[str, DeliveryResult | None]]:
    """有界重试：仅处理明确失败（retryable_failed）的投递。"""
    settings = settings or get_settings()
    out: list[tuple[str, DeliveryResult | None]] = []
    for attempt in repo_editions.list_deliveries_by_status(
        session, DeliveryStatus.RETRYABLE_FAILED.value
    ):
        edition = repo_editions.get_edition(session, attempt.edition_id)
        if edition is None:
            continue
        if attempt.channel == "feishu":
            result = dispatch_feishu(
                session,
                edition,
                settings=settings,
                transport=transport,
                dispatch_version=attempt.dispatch_version,
                dry_run=dry_run,
            )
            out.append((attempt.id, result))
    return out
