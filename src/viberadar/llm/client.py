"""通用 LLM 客户端：OpenAI 兼容与 Anthropic 协议、JSON 解析、重试与用量统计。

不依赖数据库；可被其他工具直接 import 复用。缓存/预算/账本在 Gateway 层。
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse
from dataclasses import dataclass
from decimal import Decimal

import httpx

from .config import LLMPrice, LLMProfile
from .costs import compute_cost, estimate_tokens

RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)
ANTHROPIC_VERSION = "2023-06-01"
JSON_INSTRUCTION = "只输出一个合法 JSON 对象，不要输出解释或 Markdown 代码围栏。"


@dataclass
class CompletionResult:
    ok: bool
    text: str | None = None
    data: dict | None = None
    error: str | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    priced: bool = False
    latency_ms: int = 0
    retries: int = 0
    retryable: bool = False


def extract_json_object(text: str) -> dict:
    """从模型输出中提取 JSON 对象：容忍代码围栏与前后说明文字。"""
    if not text:
        raise ValueError("空响应")
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fence:
        stripped = fence.group(1).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("响应不是 JSON 对象")
    return parsed


class LLMClient:
    def __init__(
        self,
        profile: LLMProfile,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.profile = profile
        self._transport = transport

    # ---------- 主入口 ----------

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool | None = None,
    ) -> CompletionResult:
        profile = self.profile
        if not profile.api_key:
            return CompletionResult(ok=False, error="缺少 API key", model=model)
        json_mode = profile.json_mode if json_mode is None else json_mode
        temperature = profile.temperature if temperature is None else temperature
        max_tokens = max_output_tokens or profile.max_output_tokens
        started = time.monotonic()
        result = CompletionResult(ok=False, error="未执行", model=model)
        for attempt in range(2):
            if profile.protocol == "anthropic":
                result = self._anthropic_call(
                    system=system,
                    user=user,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                )
            else:
                result = self._openai_call(
                    system=system,
                    user=user,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                )
            result.retries = attempt
            if result.ok:
                result.latency_ms = int((time.monotonic() - started) * 1000)
                result.cost_usd, result.priced = self._cost(result)
                return result
            if not result.retryable or attempt == 1:
                break
            time.sleep(0.4)
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result

    def chat_json(self, *, system: str, user: str, model: str, **kwargs) -> CompletionResult:
        return self.complete(json_mode=True, **kwargs, system=system, user=user, model=model)

    def chat_text(self, *, system: str, user: str, model: str, **kwargs) -> CompletionResult:
        return self.complete(json_mode=False, **kwargs, system=system, user=user, model=model)

    # ---------- 协议实现 ----------

    def _openai_call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> CompletionResult:
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": f"{system}\n{JSON_INSTRUCTION}" if json_mode else system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # The current official Flash model defaults to high thinking effort. Small
        # structured tasks otherwise spend their entire output budget on reasoning.
        # Keep provider-specific parameters away from other OpenAI-compatible services.
        if (urlparse(self.profile.base_url).hostname == "api.deepseek.com" and
            model == "deepseek-flash" and (json_mode or max_tokens <= 64)):
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {self.profile.api_key}"}
        result = self._post(self._url("/v1/chat/completions", "/chat/completions"), payload, headers)
        if not result.ok:
            return CompletionResult(
                ok=False, error=result.error, model=model, retryable=result.retryable
            )
        data = result.data or {}
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            return CompletionResult(ok=False, error=f"响应结构异常: {exc}", model=model)
        usage = data.get("usage") or {}
        in_tokens = int(usage.get("prompt_tokens") or estimate_tokens(system + user))
        out_tokens = int(usage.get("completion_tokens") or estimate_tokens(content or ""))
        if data["choices"][0].get("finish_reason") == "length" or not content:
            return CompletionResult(ok=False, error="模型输出为空或被 token 限制截断", model=model,
                                    input_tokens=in_tokens, output_tokens=out_tokens)
        parsed: dict | None = None
        if json_mode:
            try:
                parsed = extract_json_object(content or "")
            except (json.JSONDecodeError, ValueError) as exc:
                return CompletionResult(
                    ok=False,
                    error=f"JSON 解析失败: {exc}",
                    model=model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                )
        return CompletionResult(
            ok=True,
            text=content,
            data=parsed,
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )

    def _anthropic_call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> CompletionResult:
        system_text = f"{system}\n{JSON_INSTRUCTION}" if json_mode else system
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_text,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.profile.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        result = self._post(self._url("/v1/messages", "/messages"), payload, headers)
        if not result.ok:
            return CompletionResult(
                ok=False, error=result.error, model=model, retryable=result.retryable
            )
        data = result.data or {}
        blocks = data.get("content") or []
        text = "".join(
            block.get("text") or ""
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        in_tokens = int(usage.get("input_tokens") or estimate_tokens(system + user))
        out_tokens = int(usage.get("output_tokens") or estimate_tokens(text))
        parsed: dict | None = None
        if json_mode:
            try:
                parsed = extract_json_object(text)
            except (json.JSONDecodeError, ValueError) as exc:
                return CompletionResult(
                    ok=False,
                    error=f"JSON 解析失败: {exc}",
                    model=model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                )
        return CompletionResult(
            ok=True,
            text=text,
            data=parsed,
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )

    # ---------- 传输与成本 ----------

    def _url(self, path_with_v1: str, path_bare: str) -> str:
        base = self.profile.base_url.rstrip("/")
        if base.endswith("/v1") or base.endswith("/v4"):
            return base + path_bare
        return base + path_with_v1

    def _post(self, url: str, payload: dict, headers: dict) -> CompletionResult:
        request_headers = {"content-type": "application/json", **headers}
        try:
            with httpx.Client(
                timeout=self.profile.timeout_seconds,
                transport=self._transport,
                headers=request_headers,
            ) as client:
                response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            return CompletionResult(
                ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True
            )
        if response.status_code in RETRY_STATUS:
            return CompletionResult(
                ok=False,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
                retryable=True,
            )
        if response.status_code != 200:
            return CompletionResult(
                ok=False, error=f"HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            return CompletionResult(ok=False, error=f"响应不是 JSON: {exc}")
        if not isinstance(data, dict):
            return CompletionResult(ok=False, error="响应不是 JSON 对象")
        return CompletionResult(ok=True, data=data)

    def _cost(self, result: CompletionResult) -> tuple[Decimal, bool]:
        price: LLMPrice | None = self.profile.price
        if price is not None and price.configured:
            return (
                compute_cost(price.model_dump(), result.input_tokens, 0, result.output_tokens),
                True,
            )
        return Decimal("0"), False
