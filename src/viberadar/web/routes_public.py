"""公共页面路由（PRD 4.6/M8/9.1）。"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from viberadar.contracts import (
    EditionStatus,
    EventPayload,
    EventType,
    new_id,
)
from viberadar.deliver import build_rss
from viberadar.feedback import ingest_event
from viberadar.store import repo_editions, repo_products
from viberadar.store.models import EditionItemRow, EditionRow

from .deps import ensure_cookie, get_session, resolve_actor
from .templating import render

router = APIRouter()

BOT_UA_PATTERN = re.compile(
    r"(bot|crawler|spider|preview|facebookexternalhit|twitterbot|slackbot|telegrambot|"
    r"discordbot|feishu|dingtalk|wechat|wxwork|curl|wget|python-requests)",
    re.I,
)


def _latest_published(session: Session) -> EditionRow | None:
    return session.scalar(
        select(EditionRow)
        .where(EditionRow.status == EditionStatus.PUBLISHED.value)
        .order_by(EditionRow.date.desc())
        .limit(1)
    )


def _recent_editions(
    session: Session,
    exclude_id: str | None = None,
    limit: int = 7,
    before_date: date | None = None,
) -> list[dict]:
    """最近期号入口（PRD 4.6）：日期、条数、内容状态与简介。

    before_date 用于日期页"往期回顾"，只展示更早的期号。
    """
    stmt = (
        select(EditionRow)
        .where(EditionRow.status == EditionStatus.PUBLISHED.value)
        .order_by(EditionRow.date.desc())
        .limit(limit + 1)
    )
    if before_date is not None:
        stmt = stmt.where(EditionRow.date < before_date)
    rows = list(session.scalars(stmt))
    result: list[dict] = []
    for edition in rows:
        if exclude_id and edition.id == exclude_id:
            continue
        revision = repo_editions.latest_revision(session, edition.id) or edition.revision
        count = session.scalar(
            select(func.count())
            .select_from(EditionItemRow)
            .where(
                EditionItemRow.edition_id == edition.id,
                EditionItemRow.revision == revision,
            )
        )
        result.append(
            {
                "date": edition.date,
                "count": int(count or 0),
                "content_status": edition.content_status,
                "intro": edition.intro or "",
            }
        )
        if len(result) >= limit:
            break
    return result


def _get_date_param(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    today = date.today()
    edition = _latest_published(session)
    if edition is None:
        response = render(
            request,
            "index.html",
            {
                "settings": settings,
                "edition": None,
                "today": today,
                "intro_only": True,
                "recent": [],
            },
        )
    else:
        view = _build_view(session, edition)
        response = render(
            request,
            "index.html",
            {
                "settings": settings,
                "edition": view,
                "today": today,
                "is_today": edition.date == today,
                "intro_only": False,
                "highlights": _highlights(view),
                "sections": _group_sections(view),
                "recent": _recent_editions(session, exclude_id=edition.id, limit=7),
            },
        )
    actor = resolve_actor(request, settings, create=True)
    ensure_cookie(request, response, settings, actor)
    return response


@router.get("/d/{date_str}", response_class=HTMLResponse)
def edition_page(date_str: str, request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    target = _get_date_param(date_str)
    if target is None:
        return render(request, "not_found.html", {"settings": settings, "message": "日期不存在"}, status_code=404)
    edition = repo_editions.get_edition_by_date(session, target, settings.pipeline.timezone)
    if edition is None or edition.status != EditionStatus.PUBLISHED.value:
        return render(
            request,
            "not_found.html",
            {"settings": settings, "message": "该日期没有已公开的期号"},
            status_code=404,
        )
    view = _build_view(session, edition)
    response = render(
        request,
        "edition.html",
        {
            "settings": settings,
            "edition": view,
            "highlights": _highlights(view),
            "sections": _group_sections(view),
            "recent": _recent_editions(session, exclude_id=edition.id, limit=5, before_date=target),
        },
    )
    actor = resolve_actor(request, settings, create=True)
    ensure_cookie(request, response, settings, actor)
    return response


@router.get("/p/{slug}")
def product_page(slug: str, request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    redirect = repo_products.get_slug_redirect(session, slug)
    if redirect is not None:
        product = repo_products.get_product(session, redirect.product_id)
        if product is not None:
            return RedirectResponse(f"/p/{product.slug}", status_code=301)
    product = repo_products.get_product_by_slug(session, slug)
    if product is None:
        return render(
            request, "not_found.html", {"settings": settings, "message": "产品不存在"}, status_code=404
        )
    history_rows = session.execute(
        select(EditionItemRow, EditionRow)
        .join(EditionRow, EditionRow.id == EditionItemRow.edition_id)
        .where(
            EditionItemRow.product_id == product.id,
            EditionRow.status == EditionStatus.PUBLISHED.value,
        )
        .order_by(EditionRow.date.desc())
    ).all()
    history = []
    for item_row, edition_row in history_rows:
        content = dict(item_row.content_json or {})
        history.append(
            {
                "date": edition_row.date,
                "edition_id": edition_row.id,
                "item_id": item_row.id,
                "one_liner": content.get("one_liner", ""),
                "position": item_row.position,
                "revision": item_row.revision,
            }
        )
    return render(
        request,
        "product.html",
        {
            "settings": settings,
            "product": product,
            "history": history,
            "category_label": settings.taxonomy.labels().get(
                product.category or "", product.category or ""
            ),
        },
    )


@router.get("/saved", response_class=HTMLResponse)
def saved_page(request: Request):
    settings = request.app.state.settings
    response = render(request, "saved.html", {"settings": settings})
    actor = resolve_actor(request, settings, create=True)
    ensure_cookie(request, response, settings, actor)
    return response


@router.get("/feedback/{edition_item_id}", response_class=HTMLResponse)
def feedback_page(
    edition_item_id: str, request: Request, session: Session = Depends(get_session)
):
    """GET 仅显示操作；POST（由页面确认后）才写反馈。"""
    settings = request.app.state.settings
    item = repo_editions.get_item(session, edition_item_id)
    if item is None:
        return render(
            request, "not_found.html", {"settings": settings, "message": "条目不存在"}, status_code=404
        )
    edition = repo_editions.get_edition(session, item.edition_id)
    content = dict(item.content_json or {})
    response = render(
        request,
        "feedback.html",
        {
            "settings": settings,
            "item": {
                "id": item.id,
                "name": content.get("name", ""),
                "one_liner": content.get("one_liner", ""),
                "category_label": content.get("category_label", ""),
                "access_text": content.get("access_text", ""),
            },
            "edition": {"date": edition.date if edition else None},
        },
    )
    actor = resolve_actor(request, settings, create=True)
    ensure_cookie(request, response, settings, actor)
    return response


@router.get("/go/{link_id}")
def go_redirect(link_id: str, request: Request, session: Session = Depends(get_session)):
    """受控 302 跳转；跳转优先可用，事件写入失败不能阻止正常跳转。"""
    link = repo_editions.get_outbound_link(session, link_id)
    if link is None:
        return render(
            request, "not_found.html", {"settings": request.app.state.settings, "message": "链接不存在"}, status_code=404
        )
    settings = request.app.state.settings
    user_agent = request.headers.get("user-agent", "")
    if not BOT_UA_PATTERN.search(user_agent):
        try:
            actor = resolve_actor(request, settings, create=False)
            edition_item = repo_editions.get_item(session, link.edition_item_id) if link.edition_item_id else None
            payload = EventPayload(
                client_event_id=new_id("clk"),
                event_type=EventType.OUTBOUND_CLICK,
                edition_id=edition_item.edition_id if edition_item else None,
                edition_item_id=link.edition_item_id,
                ref="web",
            )
            ingest_event(session, payload, actor.actor_hash, actor.identity_available)
            session.commit()
        except Exception:  # noqa: BLE001 —— 统计失败不影响跳转
            session.rollback()
    return RedirectResponse(link.target_url, status_code=302)


@router.get("/rss.xml")
def rss(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    xml = build_rss(session, settings)
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/sitemap.xml")
def sitemap(request: Request, session: Session = Depends(get_session)):
    """仅已公开期号与有内容的产品页可索引。"""
    settings = request.app.state.settings
    base = settings.site_base_url.rstrip("/")
    urls = [f"{base}/"]
    editions = session.scalars(
        select(EditionRow.date).where(EditionRow.status == EditionStatus.PUBLISHED.value)
    ).all()
    for edition_date in editions:
        urls.append(f"{base}/d/{edition_date.isoformat()}")
    # 只有被公开期号收录过的产品页才可索引
    from viberadar.store.models import ProductRow

    slugs = session.scalars(
        select(ProductRow.slug)
        .join(EditionItemRow, EditionItemRow.product_id == ProductRow.id)
        .join(EditionRow, EditionRow.id == EditionItemRow.edition_id)
        .where(EditionRow.status == EditionStatus.PUBLISHED.value, ProductRow.status == "active")
        .distinct()
    ).all()
    for slug in slugs:
        urls.append(f"{base}/p/{slug}")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>"
    )
    return Response(content=body, media_type="application/xml; charset=utf-8")


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    settings = request.app.state.settings
    base = settings.site_base_url.rstrip("/")
    body = (
        "User-agent: *\n"
        "Disallow: /saved\n"
        "Disallow: /feedback/\n"
        "Disallow: /go/\n"
        "Disallow: /api/\n"
        "Disallow: /health/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@router.get("/health/live")
def health_live():
    return {"status": "alive"}


@router.get("/health/ready")
def health_ready(request: Request, session: Session = Depends(get_session)):
    try:
        session.execute(select(1))
        settings = request.app.state.settings  # 配置加载成功
        del settings
        return {"status": "ready", "database": "ok"}
    except Exception:  # noqa: BLE001
        return Response(
            content='{"status":"unavailable"}',
            media_type="application/json",
            status_code=503,
        )


def _build_view(session: Session, edition: EditionRow):
    from viberadar.compose import build_edition_view

    return build_edition_view(session, edition)


def _highlights(view) -> list:
    """重点推荐 1–2 条优先展示（PRD 4.6）。"""
    return [item for item in view.items if item.highlight]


def _group_sections(view):
    """其余条目按“工具与工作流 / 创意与实验”分组；空分组隐藏，重点条目不重复出现。"""
    rest = [item for item in view.items if not item.highlight]
    tools = [item for item in rest if item.section != "creative"]
    creative = [item for item in rest if item.section == "creative"]
    sections = []
    if tools:
        sections.append(("工具与工作流", tools))
    if creative:
        sections.append(("创意与实验", creative))
    return sections
