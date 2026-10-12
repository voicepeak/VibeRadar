"""模板环境与公共渲染工具。"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates

from viberadar.settings import PROJECT_ROOT

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "web" / "templates"))


def mode_label(mode: str) -> str:
    return {
        "direct": "直接试玩",
        "demo": "看演示",
        "setup": "安装体验",
    }.get(mode, mode)


def fmt_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M")
    return value.isoformat()


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def product_status_label(status: str) -> str:
    return {
        "active": "有效",
        "merged": "已合并",
        "blocked": "已阻止",
        "retracted": "已撤回",
    }.get(status, status)


def content_status_label(status: str) -> str:
    return {
        "normal": "常规",
        "short": "短刊",
        "empty": "暂无推荐",
        "degraded": "部分信源缺失",
    }.get(status, status)


def section_label(section: str) -> str:
    return {
        "tool": "工具与工作流",
        "creative": "创意与实验",
    }.get(section, section)


templates.env.filters["mode_label"] = mode_label
templates.env.filters["fmt_date"] = fmt_date
templates.env.filters["fmt_datetime"] = fmt_datetime
templates.env.filters["section_label"] = section_label
templates.env.filters["product_status_label"] = product_status_label
templates.env.filters["content_status_label"] = content_status_label


def render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {"request": request, **context}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
