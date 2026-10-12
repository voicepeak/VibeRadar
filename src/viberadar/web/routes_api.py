"""公共 JSON API（PRD 9.1/12.2）。"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.compose import build_edition_view
from viberadar.contracts import (
    ContentIssueCreate,
    EditionStatus,
    EventPayload,
    FeedbackStatePayload,
)
from viberadar.feedback import (
    create_issue,
    edition_stats,
    get_feedback_state_view,
    ingest_event,
    limiter,
    set_feedback_state,
)
from viberadar.store import repo_editions, repo_products
from viberadar.store.models import EditionItemRow, EditionRow

from .deps import (
    client_ip,
    ensure_cookie,
    get_session,
    resolve_actor,
    resolve_locale,
    same_origin,
)

router = APIRouter(prefix="/api/v1")


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


def _check_rate(request: Request, settings, actor) -> JSONResponse | None:
    ip = client_ip(request)
    if not limiter.allow(f"ip:{ip}", settings.pipeline.rate_limits.ip_per_minute):
        return _error(request, "rate_limited", "请求过于频繁", 429, retryable=True)
    if actor.actor_hash:
        if not limiter.allow(
            f"actor:{actor.actor_hash}", settings.pipeline.rate_limits.anonymous_per_minute
        ):
            return _error(request, "rate_limited", "请求过于频繁", 429, retryable=True)
    return None


@router.get("/editions/{date_str}")
def get_edition(
    date_str: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        return _error(request, "invalid_date", "日期格式非法", 404)
    edition = repo_editions.get_edition_by_date(session, target, settings.pipeline.timezone)
    if edition is None or edition.status != EditionStatus.PUBLISHED.value:
        return _error(request, "not_found", "期号不存在或未公开", 404)
    view = build_edition_view(session, edition, resolve_locale(request))
    payload = view.model_dump(mode="json")
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    result = JSONResponse(json.loads(body))
    result.headers["ETag"] = etag
    result.headers["Cache-Control"] = "public, max-age=300"
    # 内容随 ?lang= 或语言 cookie 变化；共享缓存需按 Cookie/语言键隔离。
    result.headers["Vary"] = "Cookie, Accept-Language"
    return result


@router.get("/products/{slug}")
def get_product(slug: str, request: Request, session: Session = Depends(get_session)):
    redirect = repo_products.get_slug_redirect(session, slug)
    if redirect is not None:
        product = repo_products.get_product(session, redirect.product_id)
        if product is not None:
            return {"merged_into": product.slug, "status": "merged"}
    product = repo_products.get_product_by_slug(session, slug)
    if product is None:
        return _error(request, "not_found", "产品不存在", 404)
    history = session.execute(
        select(EditionItemRow.edition_id, EditionRow.date)
        .join(EditionRow, EditionRow.id == EditionItemRow.edition_id)
        .where(
            EditionItemRow.product_id == product.id,
            EditionRow.status == EditionStatus.PUBLISHED.value,
        )
        .order_by(EditionRow.date.desc())
    ).all()
    # 不暴露运营备注、原始日志与凭据
    return {
        "id": product.id,
        "slug": product.slug,
        "name": product.name,
        "summary": product.summary,
        "category": product.category,
        "status": product.status,
        "canonical_url": product.canonical_url,
        "first_seen_at": product.first_seen_at.isoformat() if product.first_seen_at else None,
        "history": [{"edition_id": row[0], "date": row[1].isoformat()} for row in history],
        "retraction_note": product.retraction_note,
    }


@router.post("/events")
async def post_event(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    if not same_origin(request):
        return _error(request, "forbidden", "同源校验失败", 403)
    actor = resolve_actor(request, settings, create=False)
    limited = _check_rate(request, settings, actor)
    if limited is not None:
        return limited
    try:
        raw = await request.json()
        payload = EventPayload(**raw)
    except (ValidationError, ValueError, TypeError):
        return _error(request, "invalid_payload", "事件载荷非法", 400)
    event_id, created = ingest_event(
        session, payload, actor.actor_hash, actor.identity_available
    )
    response = JSONResponse({"ok": True, "event_id": event_id, "deduped": not created})
    if not actor.identity_available:
        actor = resolve_actor(request, settings, create=True)
    ensure_cookie(request, response, settings, actor)
    return response


@router.get("/feedback/{edition_item_id}")
def get_feedback(
    edition_item_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    item = repo_editions.get_item(session, edition_item_id)
    if item is None:
        return _error(request, "not_found", "条目不存在", 404)
    actor = resolve_actor(request, settings, create=False)
    view = get_feedback_state_view(session, actor.actor_hash, edition_item_id)
    response = JSONResponse(view.model_dump(mode="json"))
    response.headers["Cache-Control"] = "private, no-store"
    if actor.anonymous_id:
        ensure_cookie(request, response, settings, actor)
    return response


@router.put("/feedback/{edition_item_id}")
async def put_feedback(
    edition_item_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    if not same_origin(request):
        return _error(request, "forbidden", "同源校验失败", 403)
    actor = resolve_actor(request, settings, create=True)
    limited = _check_rate(request, settings, actor)
    if limited is not None:
        return limited
    item = repo_editions.get_item(session, edition_item_id)
    if item is None:
        return _error(request, "not_found", "条目不存在", 404)
    try:
        raw = await request.json()
        payload = FeedbackStatePayload(**raw)
    except (ValidationError, ValueError, TypeError):
        return _error(request, "invalid_payload", "反馈载荷非法", 400)
    if "tried" not in payload.model_fields_set and "rating_state" not in payload.model_fields_set:
        return _error(request, "invalid_payload", "至少提交试玩状态或评价", 400)
    assert actor.actor_hash is not None
    view = set_feedback_state(session, actor.actor_hash, edition_item_id, payload)
    response = JSONResponse(view.model_dump(mode="json"))
    response.headers["Cache-Control"] = "private, no-store"
    ensure_cookie(request, response, settings, actor)
    return response


@router.post("/issues")
async def post_issue(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    if not same_origin(request):
        return _error(request, "forbidden", "同源校验失败", 403)
    actor = resolve_actor(request, settings, create=False)
    limited = _check_rate(request, settings, actor)
    if limited is not None:
        return limited
    try:
        raw = await request.json()
        payload = ContentIssueCreate(**raw)
    except (ValidationError, ValueError, TypeError):
        return _error(request, "invalid_payload", "报错载荷非法", 400)
    item = repo_editions.get_item(session, payload.edition_item_id)
    if item is None:
        return _error(request, "not_found", "条目不存在", 404)
    view = create_issue(session, payload)
    return JSONResponse(view.model_dump(mode="json"), status_code=201)


@router.get("/editions/{date_str}/stats")
def get_stats(
    date_str: str, request: Request, session: Session = Depends(get_session)
):
    """运营统计只读接口（不含 actor）。"""
    settings = request.app.state.settings
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        return _error(request, "invalid_date", "日期格式非法", 404)
    edition = repo_editions.get_edition_by_date(session, target, settings.pipeline.timezone)
    if edition is None:
        return _error(request, "not_found", "期号不存在", 404)
    return edition_stats(session, edition.id)
