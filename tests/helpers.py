"""测试辅助：确定性 Stub LLM 与离线 HTTP transport。"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from viberadar.llm import LLMGateway, LLMResult

CUTOFF = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc)


class StubLLM(LLMGateway):
    """不发网络请求；按 prompt 名返回确定性结构化结果。"""

    def __init__(self, session_factory, settings) -> None:
        super().__init__(session_factory, settings, provider="mock")
        self.calls: list[str] = []

    def chat_json(self, *, prompt_name: str, system: str, payload: dict, model: str, **kwargs):  # noqa: ANN003
        self.calls.append(prompt_name)
        if prompt_name == "classify":
            categories = [
                "agent-hygiene",
                "coding-agent",
                "mcp-skill",
                "app-builder",
                "creative-toy",
                "editor-extension",
            ]
            results = []
            for idx, item in enumerate(payload.get("items", [])):
                results.append(
                    {
                        "item_id": item["item_id"],
                        "relevant": True,
                        "category": categories[idx % len(categories)],
                        "confidence": 0.92,
                        "spam_score": 0.0,
                        "reason": "stub 分类",
                        "evidence_ids": [],
                        "needs_review": False,
                    }
                )
            return LLMResult(ok=True, model=model, data={"results": results})
        if prompt_name == "interest":
            return LLMResult(
                ok=True,
                model=model,
                data={
                    "dims": {
                        "creativity": 5,
                        "playability": 4,
                        "completeness": 4,
                        "surprise": 4,
                        "niche": 4,
                    }
                },
            )
        if prompt_name == "guide":
            evidence = payload.get("evidence") or []
            eid = evidence[0]["id"] if evidence else None
            claims = []
            if eid:
                claims.append({"text": "费用未确认", "evidence_ids": [eid]})
            return LLMResult(
                ok=True,
                model=model,
                data={
                    "one_liner": "把 agent 会话上下文变成可回滚记录",
                    "why_interesting": "可以对照修改前后的上下文变化。",
                    "mode": "demo",
                    "steps": [{"text": "打开项目页面"}, {"text": "查看演示效果"}],
                    "access": {
                        "pricing": "unknown",
                        "mobile_support": "unknown",
                        "login_required": None,
                        "api_key_required": None,
                        "waitlist": None,
                        "self_host_required": None,
                        "platforms": [],
                        "setup_minutes": None,
                        "checked_at": "2026-10-12T00:00:00Z",
                    },
                    "claims": claims,
                    "verification_status": "unverified",
                },
            )
        return LLMResult(ok=False, model=model, error=f"stub 未实现: {prompt_name}")


def html_transport(body: str | None = None, status: int = 200) -> httpx.MockTransport:
    html = body or (
        "<html><head><title>测试产品</title>"
        "<meta name='description' content='免费试用，需登录，提供公开演示'>"
        "</head><body><h1>Demo</h1><p>免费在线体验。sign in to save. "
        "支持 Windows / macOS / Linux。</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def json_transport(payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def failing_transport(status: int = 500) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "boom"})

    return httpx.MockTransport(handler)


def manual_entries(count: int = 6, prefix: str = "https://example.com/?p=") -> list[dict]:
    entries = []
    for i in range(1, count + 1):
        entries.append(
            {
                "url": f"{prefix}{i}",
                "title": f"Test Agent Tool {i}",
                "description": (
                    "面向编码 agent 的上下文与回滚工具，支持 agent 工作流调试、"
                    "上下文快照、回滚与配额检查，帮助测试编码 agent 是否按预期修改代码。"
                ),
                "published_at": "2026-10-12T05:00:00+08:00",
            }
        )
    return entries


def disable_external_sources(settings) -> None:
    for key in ("hn", "github", "producthunt"):
        if key in settings.sources.sources:
            settings.sources.sources[key].enabled = False
