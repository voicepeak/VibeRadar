"""飞书 webhook 投递（PRD M6/附录 B.4）。

P0 使用自定义 webhook 群推送；交互通过跳转网页。
发送超时可能已经送达：状态置 unknown，不盲目重发。
凭据缺失只能生成预览，不能把本地生成称为推送成功。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from viberadar.contracts import ContentStatus, DeliveryStatus
from viberadar.settings import Settings


@dataclass
class DeliveryResult:
    status: str
    http_status: int | None = None
    error: str | None = None
    external_message_id: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status == DeliveryStatus.SENT.value


def build_card(edition_view, settings: Settings) -> dict:
    """webhook 卡片：精选摘要 + 网页链接；反馈链接打开反馈页。"""
    base = settings.site_base_url.rstrip("/")
    date_str = edition_view.date.isoformat()
    count = len(edition_view.items)
    if edition_view.content_status == ContentStatus.EMPTY:
        title = f"Vibe Radar · {date_str} 暂无推荐"
    else:
        title = f"Vibe Radar · {date_str} 精选 {count} 条"

    elements: list[dict] = []
    for item in edition_view.items[:4]:
        lines = [
            f"**{item.position}. {item.name}** · {item.category_label}",
            item.one_liner,
            f"门槛：{item.access_text}",
        ]
        mode_text = {
            "direct": "直接试玩",
            "demo": "看演示",
            "setup": "安装体验",
        }.get(item.mode, item.mode)
        lines.append(f"体验方式：{mode_text}")
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        )
    if count > 4:
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": f"… 另有 {count - 4} 条"}}
        )

    actions = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "打开今日日报"},
            "url": f"{base}/d/{date_str}",
            "type": "primary",
        }
    ]
    if edition_view.items:
        first = edition_view.items[0]
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "玩过了 / 反馈"},
                "url": f"{base}/feedback/{first.id}",
                "type": "default",
            }
        )
    elements.append({"tag": "action", "actions": actions})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "链接打开网页后才记录反馈，预览机器人不会产生反馈。",
                }
            ],
        }
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        },
    }


def send_feishu(
    webhook_url: str,
    payload: dict,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 15.0,
) -> DeliveryResult:
    try:
        client = httpx.Client(timeout=timeout, transport=transport)
        with client:
            response = client.post(webhook_url, json=payload)
    except httpx.TimeoutException:
        # 超时可能已经送达 → unknown，核对后决定（PRD 5.4/E15）
        return DeliveryResult(status=DeliveryStatus.UNKNOWN.value, error="发送超时，是否送达未知")
    except httpx.HTTPError as exc:
        return DeliveryResult(
            status=DeliveryStatus.RETRYABLE_FAILED.value, error=f"网络错误: {exc}"
        )

    if response.status_code == 429:
        return DeliveryResult(
            status=DeliveryStatus.RETRYABLE_FAILED.value,
            http_status=429,
            error="限流，按恢复时间退避",
        )
    if response.status_code >= 500:
        return DeliveryResult(
            status=DeliveryStatus.RETRYABLE_FAILED.value,
            http_status=response.status_code,
            error=f"服务端错误 {response.status_code}",
        )
    if response.status_code >= 400:
        return DeliveryResult(
            status=DeliveryStatus.PERMANENT_FAILED.value,
            http_status=response.status_code,
            error=f"请求被拒绝 {response.status_code}",
        )
    try:
        data = response.json()
    except ValueError:
        return DeliveryResult(
            status=DeliveryStatus.UNKNOWN.value,
            http_status=response.status_code,
            error="响应非 JSON，送达状态未知",
        )
    code = data.get("code")
    if code in (0, None) and (data.get("StatusCode") in (0, None)):
        return DeliveryResult(
            status=DeliveryStatus.SENT.value,
            http_status=response.status_code,
            external_message_id=str(data.get("msg_id") or data.get("request_id") or ""),
        )
    if code in (9499, 11232):
        return DeliveryResult(
            status=DeliveryStatus.RETRYABLE_FAILED.value,
            http_status=response.status_code,
            error=f"飞书限流 code={code}",
        )
    return DeliveryResult(
        status=DeliveryStatus.PERMANENT_FAILED.value,
        http_status=response.status_code,
        error=f"飞书返回 code={code} msg={data.get('msg')}",
    )
