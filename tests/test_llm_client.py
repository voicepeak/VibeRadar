"""通用 LLM 客户端：OpenAI 兼容、Anthropic、JSON 提取、重试与成本。"""

from __future__ import annotations

from decimal import Decimal

import httpx
import json

from viberadar.llm import LLMClient, LLMPrice, LLMProfile, extract_json_object


def _profile(**overrides) -> LLMProfile:
    payload = {
        "name": "t",
        "protocol": "openai",
        "base_url": "https://llm.test/v1",
        "api_key": "sk-test",
        "model_fast": "m-fast",
        "model_strong": "m-strong",
    }
    payload.update(overrides)
    return LLMProfile(**payload)


def _openai_transport(content: str, usage: dict | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": usage or {}},
        )

    return httpx.MockTransport(handler)


def test_openai_json_mode_parses_fenced_content():
    content = '```json\n{"results": [{"item_id": "i1"}]}\n```'
    client = LLMClient(_profile(), transport=_openai_transport(content, {"prompt_tokens": 10, "completion_tokens": 5}))
    result = client.chat_json(system="s", user="u", model="m-fast")
    assert result.ok and result.data == {"results": [{"item_id": "i1"}]}
    assert result.input_tokens == 10 and result.output_tokens == 5
    assert result.priced is False and result.cost_usd == Decimal("0")


def test_official_flash_short_json_disables_thinking_and_requests_json():
    seen = []
    client = LLMClient(_profile(base_url="https://api.deepseek.com"),
                       transport=_openai_transport('{"dims": {}}', seen=seen))
    assert client.chat_json(system="", user="score this", model="deepseek-flash", max_output_tokens=800).ok
    body = json.loads(seen[0].content)
    assert body["thinking"] == {"type": "disabled"}
    assert "JSON" in body["messages"][0]["content"]
    other_seen = []
    other = LLMClient(_profile(), transport=_openai_transport('{"ok":true}', seen=other_seen))
    other.chat_json(system="", user="u", model="deepseek-flash")
    assert "thinking" not in json.loads(other_seen[0].content)


def test_empty_truncated_model_output_is_not_success():
    client = LLMClient(_profile(), transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
              "usage": {"prompt_tokens": 12, "completion_tokens": 16}})))
    result = client.chat_text(system="s", user="u", model="m-fast", max_output_tokens=16)
    assert not result.ok and result.output_tokens == 16 and "token" in result.error


def test_openai_url_join_when_base_lacks_suffix():
    seen: list[httpx.Request] = []
    client = LLMClient(
        _profile(base_url="https://llm.test"), transport=_openai_transport('{"a":1}', seen=seen)
    )
    client.chat_json(system="s", user="u", model="m-fast")
    assert str(seen[0].url) == "https://llm.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-test"


def test_anthropic_protocol_and_headers():
    seen: list[httpx.Request] = []
    content = "说明文字 {\"b\": 2} 结尾"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": content}],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        )

    client = LLMClient(
        _profile(
            protocol="anthropic",
            base_url="https://api.anthropic.test",
            model_fast="claude-haiku",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = client.chat_json(system="s", user="u", model="claude-haiku")
    assert result.ok and result.data == {"b": 2}
    request = seen[0]
    assert str(request.url) == "https://api.anthropic.test/v1/messages"
    assert request.headers["x-api-key"] == "sk-test"
    assert request.headers["anthropic-version"] == "2023-06-01"


def test_retry_on_retryable_status_then_success():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\":true}"}}]})

    client = LLMClient(_profile(), transport=httpx.MockTransport(handler))
    result = client.chat_json(system="s", user="u", model="m-fast")
    assert result.ok and calls["count"] == 2


def test_no_retry_on_client_error():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, text="unauthorized")

    client = LLMClient(_profile(), transport=httpx.MockTransport(handler))
    result = client.chat_json(system="s", user="u", model="m-fast")
    assert not result.ok and calls["count"] == 1
    assert "401" in (result.error or "")


def test_missing_key_fails_fast():
    client = LLMClient(_profile(api_key=""), transport=_openai_transport('{"a":1}'))
    result = client.chat_json(system="s", user="u", model="m-fast")
    assert not result.ok and "API key" in (result.error or "")


def test_cost_from_profile_price():
    transport = _openai_transport(
        '{"a":1}', {"prompt_tokens": 1000, "completion_tokens": 2000}
    )
    client = LLMClient(
        _profile(price=LLMPrice(input=1.0, output=2.0)), transport=transport
    )
    result = client.chat_json(system="s", user="u", model="m-fast")
    assert result.priced is True
    assert result.cost_usd == Decimal("0.005")


def test_extract_json_object_rejects_garbage():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    try:
        extract_json_object("不是 JSON")
        raise AssertionError("应当抛出异常")
    except ValueError:
        pass
