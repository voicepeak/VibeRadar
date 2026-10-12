"""LLM 配置窗口与服务端点：鉴权、读写掩码、连通性与 /api/llm/chat。"""

from __future__ import annotations

import json

import httpx


def _enable(monkeypatch, token: str = "sekret-token") -> None:
    monkeypatch.setenv("VIBERADAR_ADMIN_TOKEN", token)
    monkeypatch.setenv("VIBERADAR_LLM_PROVIDER", "auto")


def _save_profile(temp_settings, key: str = "sk-secret-123456") -> None:
    from viberadar.llm import LLMConfig, LLMPrice, LLMProfile, load_store

    store = load_store(temp_settings)
    store.save(
        LLMConfig(
            active="work",
            profiles={
                "work": LLMProfile(
                    name="work",
                    protocol="openai",
                    base_url="https://llm.test/v1",
                    api_key=key,
                    model_fast="m-fast",
                    model_strong="m-strong",
                    price=LLMPrice(input=1.0, output=2.0),
                )
            },
            stages={"classify": "fast", "enrich": "strong"},
        )
    )


def _openai_transport(seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"answer": 42}, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    return httpx.MockTransport(handler)


HEADERS = {"X-Admin-Token": "sekret-token"}


def test_trusted_local_detection():
    from types import SimpleNamespace

    from viberadar.web.routes_llm import _trusted_local

    def fake(host, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})

    assert _trusted_local(fake("127.0.0.1"))
    assert _trusted_local(fake("::1"))
    assert not _trusted_local(fake("192.168.1.10"))
    assert not _trusted_local(fake("127.0.0.1", {"x-forwarded-for": "1.2.3.4"}))


def test_admin_config_fully_open_no_token(client):
    """设置页与管理 API 不依赖任何令牌/环境变量，直接可用。"""
    resp = client.get("/api/admin/llm/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profiles"] == [] and body["presets"]

    saved = client.put(
        "/api/admin/llm/config",
        json={
            "profiles": [
                {
                    "name": "quick",
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-quick-123456",
                    "model_fast": "deepseek-chat",
                    "model_strong": "deepseek-reasoner",
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["profiles"][0]["api_key_masked"]


def test_chat_endpoint_allows_trusted_local_without_token(app, client, monkeypatch, temp_settings):
    monkeypatch.setenv("VIBERADAR_LLM_PROVIDER", "auto")
    monkeypatch.setattr("viberadar.web.routes_llm._trusted_local", lambda request: True)
    _save_profile(temp_settings)
    app.state.llm_transport = _openai_transport()
    resp = client.post("/api/llm/chat", json={"user": "hi", "json_mode": True})
    assert resp.status_code == 200 and resp.json()["ok"] is True


def test_settings_link_in_live_nav(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/admin/llm"' in home.text
    assert "设置" in home.text


def test_admin_page_renders(client, monkeypatch):
    _enable(monkeypatch)
    resp = client.get("/admin/llm")
    assert resp.status_code == 200
    assert "LLM 配置窗口" in resp.text
    assert "noindex" in resp.text
    assert "管理令牌" not in resp.text


def test_admin_config_roundtrip_masks_and_keeps_key(client, monkeypatch, temp_settings):
    _enable(monkeypatch)
    payload = {
        "active": "work",
        "stages": {"classify": "fast", "enrich": "strong"},
        "profiles": [
            {
                "name": "work",
                "label": "测试",
                "protocol": "openai",
                "base_url": "https://llm.test/v1",
                "api_key": "sk-secret-123456",
                "model_fast": "m-fast",
                "model_strong": "m-strong",
            }
        ],
    }
    saved = client.put("/api/admin/llm/config", headers=HEADERS, json=payload)
    assert saved.status_code == 200, saved.text
    listed = client.get("/api/admin/llm/config", headers=HEADERS).json()
    profile = listed["profiles"][0]
    assert profile["api_key"] == "" and profile["api_key_set"] is True
    assert profile["api_key_masked"].startswith("sk-s")
    assert "sk-secret-123456" not in json.dumps(listed)
    stored = json.loads((temp_settings.data_dir / "llm.json").read_text(encoding="utf-8"))
    assert stored["profiles"]["work"]["api_key"] == "sk-secret-123456"

    # 留空/占位符表示保持原密钥
    payload["profiles"][0]["api_key"] = "__KEEP__"
    payload["profiles"][0]["model_fast"] = "m-fast-2"
    again = client.put("/api/admin/llm/config", headers=HEADERS, json=payload)
    assert again.status_code == 200
    stored = json.loads((temp_settings.data_dir / "llm.json").read_text(encoding="utf-8"))
    assert stored["profiles"]["work"]["api_key"] == "sk-secret-123456"
    assert stored["profiles"]["work"]["model_fast"] == "m-fast-2"


def test_admin_test_connection_uses_transport(app, client, monkeypatch, temp_settings):
    _enable(monkeypatch)
    _save_profile(temp_settings)
    app.state.llm_transport = _openai_transport()
    resp = client.post(
        "/api/admin/llm/test", headers=HEADERS, json={"profile": "work"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["profile"] == "work" and body["model"] == "m-fast"


def test_chat_endpoint_serves_other_tools(app, client, monkeypatch, temp_settings):
    _enable(monkeypatch)
    _save_profile(temp_settings)
    seen: list[httpx.Request] = []
    app.state.llm_transport = _openai_transport(seen)

    unauth = client.post("/api/llm/chat", json={"user": "hi"})
    assert unauth.status_code == 401

    resp = client.post(
        "/api/llm/chat",
        headers=HEADERS,
        json={"system": "s", "user": "hi", "json_mode": True, "model": "fast"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["data"] == {"answer": 42}
    assert body["model"] == "m-fast"
    assert body["usage"]["input_tokens"] == 10
    assert body["cost_usd"] > 0 and body["priced"] is True

    cached = client.post(
        "/api/llm/chat",
        headers=HEADERS,
        json={"system": "s", "user": "hi", "json_mode": True, "model": "fast"},
    ).json()
    assert cached["cached"] is True
    assert len(seen) == 1


def test_chat_endpoint_rejects_invalid_payload(client, monkeypatch, temp_settings):
    _enable(monkeypatch)
    _save_profile(temp_settings)
    resp = client.post("/api/llm/chat", headers=HEADERS, json={"system": "s"})
    assert resp.status_code == 400


def test_chat_endpoint_reports_unconfigured(client, monkeypatch):
    _enable(monkeypatch)
    resp = client.post(
        "/api/llm/chat", headers=HEADERS, json={"user": "hi"}
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "llm_not_configured"


def test_llm_status_masks_keys(client, monkeypatch, temp_settings):
    _enable(monkeypatch)
    _save_profile(temp_settings)
    body = client.get("/api/llm/status", headers=HEADERS).json()
    assert body["configured"] is True and body["active"] == "work"
    listed = body["profiles"][0]
    assert listed["api_key_masked"] and "sk-secret" not in json.dumps(body)
    assert body["stages"]["enrich"] == "strong"
    assert body["budget"]["daily_cap_usd"] > 0


def test_admin_delete_profile(client, monkeypatch, temp_settings):
    _enable(monkeypatch)
    _save_profile(temp_settings)
    resp = client.delete("/api/admin/llm/profiles/work", headers=HEADERS)
    assert resp.status_code == 200
    assert client.get("/api/admin/llm/config", headers=HEADERS).json()["profiles"] == []
