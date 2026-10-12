"""分发：发送账本、unknown 不盲目重发、渠道解耦（PRD M6/5.4/E13–E15）。"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from viberadar.compose import publish_edition
from viberadar.contracts import DeliveryStatus
from viberadar.deliver import dispatch_feishu, record_web_delivery, retry_failed_deliveries
from viberadar.store import repo_editions, repo_products

NOW = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)
WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook"


class CountingTransport(httpx.MockTransport):
    def __init__(self, handler):  # noqa: ANN001
        super().__init__(handler)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return super().handle_request(request)


def _ok_transport() -> CountingTransport:
    return CountingTransport(
        lambda request: httpx.Response(200, json={"code": 0, "msg": "success"})
    )


def _rate_limited_transport() -> CountingTransport:
    return CountingTransport(
        lambda request: httpx.Response(200, json={"code": 9499, "msg": "too many request"})
    )


def _server_error_transport() -> CountingTransport:
    return CountingTransport(lambda request: httpx.Response(503, text="unavailable"))


def _bad_request_transport() -> CountingTransport:
    return CountingTransport(
        lambda request: httpx.Response(400, json={"code": 19001, "msg": "param invalid"})
    )


def _timeout_transport() -> CountingTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    return CountingTransport(handler)


def _published_edition(session):
    edition = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
    product = repo_products.create_product(
        session, "P", None, None, "url:https://example.com/p", NOW
    )
    rows = repo_editions.replace_items(
        session,
        edition,
        edition.revision,
        [{"product_id": product.id, "content": {"name": "P", "source_links": []}}],
    )
    repo_editions.transition(session, edition, "reviewing")
    repo_editions.transition(session, edition, "ready")
    publish_edition(session, edition, now=NOW)
    return edition, rows[0]


def test_web_delivery_recorded_independently(session, monkeypatch, temp_settings):
    edition, _ = _published_edition(session)
    record_web_delivery(session, edition.id, success=True)
    deliveries = repo_editions.list_deliveries(session, edition.id)
    web = [d for d in deliveries if d.channel == "web"][0]
    assert web.status == DeliveryStatus.SENT.value


def test_feishu_success_and_no_resend(session, monkeypatch, temp_settings):
    monkeypatch.setenv("VIBERADAR_FEISHU_WEBHOOK_URL", WEBHOOK)
    edition, _ = _published_edition(session)
    transport = _ok_transport()
    result = dispatch_feishu(session, edition, settings=temp_settings, transport=transport)
    assert result.status == DeliveryStatus.SENT.value
    assert len(transport.requests) == 1
    # 同一 dispatch_version 不重复发送
    result2 = dispatch_feishu(session, edition, settings=temp_settings, transport=transport)
    assert result2.status == DeliveryStatus.SENT.value
    assert len(transport.requests) == 1


def test_feishu_timeout_marks_unknown_and_blocks_blind_retry(session, monkeypatch, temp_settings):
    monkeypatch.setenv("VIBERADAR_FEISHU_WEBHOOK_URL", WEBHOOK)
    edition, _ = _published_edition(session)
    transport = _timeout_transport()
    result = dispatch_feishu(session, edition, settings=temp_settings, transport=transport)
    assert result.status == DeliveryStatus.UNKNOWN.value
    result2 = dispatch_feishu(session, edition, settings=temp_settings, transport=_ok_transport())
    assert result2.status == DeliveryStatus.UNKNOWN.value  # 需人工核对，不盲目重发


def test_feishu_retryable_then_retry_succeeds(session, monkeypatch, temp_settings):
    monkeypatch.setenv("VIBERADAR_FEISHU_WEBHOOK_URL", WEBHOOK)
    edition, _ = _published_edition(session)
    result = dispatch_feishu(session, edition, settings=temp_settings, transport=_server_error_transport())
    assert result.status == DeliveryStatus.RETRYABLE_FAILED.value
    retried = retry_failed_deliveries(session, settings=temp_settings, transport=_ok_transport())
    assert retried and retried[0][1].status == DeliveryStatus.SENT.value


def test_feishu_permanent_failure_not_retried(session, monkeypatch, temp_settings):
    monkeypatch.setenv("VIBERADAR_FEISHU_WEBHOOK_URL", WEBHOOK)
    edition, _ = _published_edition(session)
    result = dispatch_feishu(session, edition, settings=temp_settings, transport=_bad_request_transport())
    assert result.status == DeliveryStatus.PERMANENT_FAILED.value
    retried = retry_failed_deliveries(session, settings=temp_settings, transport=_ok_transport())
    assert retried == []


def test_missing_webhook_is_not_success(session, monkeypatch, temp_settings):
    monkeypatch.delenv("VIBERADAR_FEISHU_WEBHOOK_URL", raising=False)
    edition, _ = _published_edition(session)
    result = dispatch_feishu(session, edition, settings=temp_settings)
    assert result.status == DeliveryStatus.PERMANENT_FAILED.value
    assert "webhook" in (result.error or "")


def test_rate_limited_is_retryable(session, monkeypatch, temp_settings):
    monkeypatch.setenv("VIBERADAR_FEISHU_WEBHOOK_URL", WEBHOOK)
    edition, _ = _published_edition(session)
    result = dispatch_feishu(session, edition, settings=temp_settings, transport=_rate_limited_transport())
    assert result.status == DeliveryStatus.RETRYABLE_FAILED.value
