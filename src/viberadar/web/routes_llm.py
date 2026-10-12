"""LLM 配置窗口与服务端点。

- /admin/llm：设置页；直接填写 API Key，无需令牌/环境变量（公网由部署边界/Caddy 屏蔽）。
- /api/admin/llm/*：配置读写与连通性测试（同上，默认开放）。
- /api/llm/chat、/api/llm/status：供其他工具调用（本机直连免令牌；远程需 VIBERADAR_LLM_API_TOKEN）。

密钥只写 data/llm.json；接口响应一律掩码。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from viberadar.feedback import limiter
from viberadar.llm import (
    PRESETS,
    LLMClient,
    LLMConfig,
    LLMPrice,
    LLMProfile,
    make_gateway,
)
from viberadar.llm.config import PROTOCOLS
from viberadar.llm.gateway import BudgetExceeded
from viberadar.store import repo_ops

from .deps import client_ip, get_session
from .templating import render

router = APIRouter()

KEEP_KEY = "__KEEP__"


class LLMProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = ""
    protocol: str = "openai"
    base_url: str = ""
    api_key: str | None = None
    model_fast: str = ""
    model_strong: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 1600
    json_mode: bool = True
    price: LLMPrice | None = None


class LLMConfigUpdate(BaseModel):
    active: str | None = None
    stages: dict[str, str] | None = None
    profiles: list[LLMProfileInput] | None = None


class LLMChatRequest(BaseModel):
    system: str = ""
    user: str = Field(min_length=1, max_length=200_000)
    json_mode: bool = True
    model: str = "fast"
    max_output_tokens: int | None = Field(default=None, ge=1, le=8000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    profile: str | None = None


def _error(request: Request, code: str, message: str, status: int, retryable: bool = False):
    return JSONResponse(
        {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", ""),
            "retryable": retryable,
        },
        status_code=status,
    )


def _configured_token(request: Request) -> str:
    settings = request.app.state.settings
    return settings.llm_api_token or settings.admin_token or ""


def _trusted_local(request: Request) -> bool:
    """本机直连（回环地址且未经代理）访问服务端点免令牌；远程需 VIBERADAR_LLM_API_TOKEN。"""
    if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        return False
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _token_ok(request: Request) -> bool:
    expected = _configured_token(request)
    if not expected:
        return False
    header = request.headers.get("x-admin-token", "")
    if not header:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            header = auth[7:].strip()
    return bool(header) and secrets.compare_digest(header, expected)


def _require_service_token(request: Request):
    """仅 /api/llm/* 服务端点使用；设置页与管理 API 无需令牌（靠部署边界保护）。"""
    if _trusted_local(request):
        return None
    if not _configured_token(request):
        return _error(request, "llm_auth_required", "远程调用需要 VIBERADAR_LLM_API_TOKEN（本机直连免）", 503)
    if not _token_ok(request):
        return _error(request, "unauthorized", "令牌无效", 401)
    return None


# ---------- 管理页 ----------


@router.get("/admin", include_in_schema=False)
@router.get("/settings", include_in_schema=False)
def admin_redirect():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/admin/llm", status_code=302)


@router.get("/admin/llm", include_in_schema=False)
def admin_llm_page(request: Request):
    return render(
        request,
        "admin_llm.html",
        {
            "settings": request.app.state.settings,
            "presets": PRESETS,
        },
    )


# ---------- 管理 API ----------


@router.get("/api/admin/llm/config")
def get_llm_config(request: Request):
    settings = request.app.state.settings
    from viberadar.llm import load_store

    store = load_store(settings)
    config = store.load_optional()
    return {
        "path": str(store.path),
        "exists": config is not None,
        "active": config.active if config else "",
        "stages": config.stages if config else {},
        "profiles": [
            config.profiles[name].public_dict() for name in config.unique_names()
        ]
        if config
        else [],
        "presets": PRESETS,
        "env_provider": settings.llm_provider,
    }


@router.put("/api/admin/llm/config")
async def put_llm_config(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    from viberadar.llm import load_store

    try:
        raw = await request.json()
        update = LLMConfigUpdate(**raw)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(request, "invalid_payload", f"配置载荷非法: {exc}", 400)

    store = load_store(settings)
    try:
        config = store.load_optional() or LLMConfig()
    except Exception as exc:  # noqa: BLE001
        return _error(request, "invalid_config", str(exc), 400)

    if update.profiles is not None:
        for item in update.profiles:
            if item.protocol not in PROTOCOLS:
                return _error(request, "invalid_config", f"协议非法: {item.protocol}", 400)
            existing = config.profiles.get(item.name)
            key = item.api_key
            if key in (None, "", KEEP_KEY):
                key = existing.api_key if existing else ""
            payload = item.model_dump()
            payload["api_key"] = key
            if not payload["base_url"] and existing is not None:
                payload["base_url"] = existing.base_url
            try:
                profile = LLMProfile(**payload)
            except Exception as exc:  # noqa: BLE001
                return _error(request, "invalid_config", f"档案 {item.name} 校验失败: {exc}", 400)
            config.profiles[item.name] = profile

    if update.stages is not None:
        for stage, tier in update.stages.items():
            if tier not in ("fast", "strong"):
                return _error(request, "invalid_config", f"阶段档位非法: {stage}={tier}", 400)
        config.stages = {**config.stages, **update.stages}

    if update.active is not None:
        if update.active and update.active not in config.profiles:
            return _error(request, "invalid_config", f"active 档案不存在: {update.active}", 400)
        config.active = update.active
    if not config.active and config.profiles:
        config.active = next(iter(config.unique_names()))

    try:
        store.save(config)
    except Exception as exc:  # noqa: BLE001
        return _error(request, "save_failed", f"保存失败: {exc}", 500)

    repo_ops.record_audit(
        session,
        actor="admin",
        action="llm_config_update",
        target=str(store.path),
        detail={
            "profiles": config.unique_names(),
            "active": config.active,
            "stages": config.stages,
        },
    )
    return {
        "ok": True,
        "active": config.active,
        "stages": config.stages,
        "profiles": [
            config.profiles[name].public_dict() for name in config.unique_names()
        ],
    }


@router.delete("/api/admin/llm/profiles/{name}")
def delete_llm_profile(name: str, request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    from viberadar.llm import load_store

    store = load_store(settings)
    config = store.load_optional()
    if config is None or name not in config.profiles:
        return _error(request, "not_found", f"档案不存在: {name}", 404)
    config.profiles.pop(name)
    if config.active == name:
        config.active = next(iter(config.profiles), "")
    store.save(config)
    repo_ops.record_audit(
        session, actor="admin", action="llm_profile_delete", target=name
    )
    return {"ok": True, "active": config.active}


@router.post("/api/admin/llm/test")
async def test_llm_profile(request: Request):
    settings = request.app.state.settings
    from viberadar.llm import load_store

    name = None
    try:
        raw = await request.json()
        if isinstance(raw, dict):
            name = raw.get("profile") or None
    except (ValueError, TypeError):
        name = None
    store = load_store(settings)
    config = store.load_optional()
    if config is None or not config.profiles:
        return _error(request, "not_configured", "尚无档案，先保存配置", 400)
    try:
        profile = config.get_profile(name) if name else config.active_profile()
    except Exception as exc:  # noqa: BLE001
        return _error(request, "not_found", str(exc), 404)
    if not profile.api_key:
        return _error(request, "missing_key", f"档案 {profile.name} 未设置 API key", 400)

    transport = getattr(request.app.state, "llm_transport", None)
    client = LLMClient(profile, transport=transport)
    result = client.chat_text(
        system="你是连通性探针，只回复一行结果。",
        user="请只回复：OK",
        model=profile.model_fast or profile.model_strong,
        max_output_tokens=16,
    )
    status = 200 if result.ok else 502
    return JSONResponse(
        {
            "ok": result.ok,
            "profile": profile.name,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "text": (result.text or "")[:120],
            "error": result.error,
        },
        status_code=status,
    )


# ---------- 服务端点（其他工具） ----------


def _service_gateway(request: Request, profile_name: str | None):
    settings = request.app.state.settings
    transport = getattr(request.app.state, "llm_transport", None)
    return make_gateway(
        request.app.state.session_factory,
        settings,
        choice=profile_name or None,
        transport=transport,
    )


@router.post("/api/llm/chat")
async def llm_chat(request: Request):
    denied = _require_service_token(request)
    if denied is not None:
        return denied
    settings = request.app.state.settings
    if not limiter.allow(f"llm-ip:{client_ip(request)}", settings.pipeline.rate_limits.ip_per_minute):
        return _error(request, "rate_limited", "请求过于频繁", 429, retryable=True)
    try:
        payload = LLMChatRequest(**(await request.json()))
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(request, "invalid_payload", f"载荷非法: {exc}", 400)

    try:
        gateway = _service_gateway(request, payload.profile)
    except Exception as exc:  # noqa: BLE001
        return _error(request, "llm_not_configured", str(exc), 503)
    if gateway is None or gateway.provider == "none":
        return _error(request, "llm_disabled", "LLM 已禁用", 503)
    if gateway.provider == "mock":
        return _error(
            request,
            "llm_not_configured",
            "当前为离线 mock：请先在 /admin/llm 或 CLI 配置档案",
            503,
        )
    result = gateway.raw_chat(
        system=payload.system,
        user=payload.user,
        model=payload.model,
        json_mode=payload.json_mode,
        max_output_tokens=payload.max_output_tokens,
        temperature=payload.temperature,
        stage="api",
    )
    if not result.ok:
        if "上限" in (result.error or ""):
            return _error(request, "budget_exceeded", result.error or "预算已达上限", 429)
        return _error(request, "llm_error", result.error or "模型调用失败", 502, retryable=True)
    _, price_version = gateway._price_for(result.model)  # noqa: SLF001
    return {
        "ok": True,
        "data": result.data,
        "text": result.text,
        "model": result.model,
        "cached": result.cached,
        "usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        "cost_usd": float(result.cost_usd),
        "priced": result.priced,
        "price_version": price_version,
        "latency_ms": result.latency_ms,
    }


@router.get("/api/llm/status")
def llm_status(request: Request):
    denied = _require_service_token(request)
    if denied is not None:
        return denied
    settings = request.app.state.settings
    from viberadar.llm import load_store

    store = load_store(settings)
    config = store.load_optional()
    budget = None
    try:
        gateway = _service_gateway(request, None)
        if gateway is not None:
            budget = gateway.budget_snapshot()
    except Exception:  # noqa: BLE001
        budget = None
    return {
        "configured": bool(config and config.profiles),
        "active": config.active if config else "",
        "profiles": [
            {
                "name": name,
                "label": config.profiles[name].label,
                "protocol": config.profiles[name].protocol,
                "model_fast": config.profiles[name].model_fast,
                "model_strong": config.profiles[name].model_strong,
                "api_key_masked": config.profiles[name].masked_key(),
            }
            for name in (config.unique_names() if config else [])
        ],
        "stages": config.stages if config else {},
        "budget": budget,
        "config_path": str(store.path),
    }
