"""模板环境与公共渲染工具（含 zh/en 本地化）。"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from viberadar.i18n import (
    DEFAULT_LOCALE,
    JS_KEYS,
    evidence_label,
    normalize_locale,
    product_status_label,
    section_label,
    translate,
)
from viberadar.i18n import mode_label as _mode_label
from viberadar.settings import PROJECT_ROOT

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "web" / "templates"))


def _locale_of(context) -> str:  # noqa: ANN001
    return normalize_locale(context.get("locale") or DEFAULT_LOCALE)


@pass_context
def t(context, key: str, **kwargs) -> str:  # noqa: ANN001
    """界面文案：{{ t('nav.today') }}；可选占位符参数。"""
    return translate(_locale_of(context), key, **kwargs)


@pass_context
def lang_url(context, target: str) -> str:  # noqa: ANN001
    """当前页面切换到目标语言的链接；静态导出时退化为 ?lang=。"""
    target = normalize_locale(target)
    request = context.get("request")
    if request is None:
        return f"?lang={target}"
    from .deps import locale_switch_url

    return locale_switch_url(request, target)


@pass_context
def js_i18n(context) -> dict:  # noqa: ANN001
    locale = _locale_of(context)
    return {key: translate(locale, key) for key in JS_KEYS}


@pass_context
def mode_label(context, mode: str) -> str:  # noqa: ANN001
    return _mode_label(mode, _locale_of(context))


@pass_context
def section_label_filter(context, section: str) -> str:  # noqa: ANN001
    return section_label(section, _locale_of(context))


@pass_context
def evidence_label_filter(context, status: str) -> str:  # noqa: ANN001
    return evidence_label(status, _locale_of(context))


@pass_context
def product_status_label_filter(context, status: str) -> str:  # noqa: ANN001
    return product_status_label(status, _locale_of(context))


def fmt_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    return value.isoformat()


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


templates.env.globals["t"] = t
templates.env.globals["lang_url"] = lang_url
templates.env.globals["js_i18n"] = js_i18n

templates.env.filters["mode_label"] = mode_label
templates.env.filters["section_label"] = section_label_filter
templates.env.filters["evidence_label"] = evidence_label_filter
templates.env.filters["product_status_label"] = product_status_label_filter
templates.env.filters["fmt_date"] = fmt_date
templates.env.filters["fmt_datetime"] = fmt_datetime


def render(request: Request, name: str, context: dict, status_code: int = 200):
    from .deps import ensure_locale_cookie, resolve_locale

    locale = resolve_locale(request)
    ctx = {"request": request, "locale": locale, **context}
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    ensure_locale_cookie(request, response)
    return response
