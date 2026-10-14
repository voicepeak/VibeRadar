"""静态归档导出（PRD M8）：Jinja2 导出一期快照与站点资源。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.contracts import EditionStatus
from viberadar.deliver import build_rss
from viberadar.settings import Settings
from viberadar.store.models import EditionRow

from .edition import (
    build_edition_view,
    group_sections,
    highlights,
    latest_published,
    recent_editions,
)


def _env():
    from viberadar.web.templating import templates

    return templates.env


def export_edition(session: Session, edition: EditionRow, settings: Settings) -> Path:
    out_dir = settings.public_dir / "d"
    out_dir.mkdir(parents=True, exist_ok=True)
    view = build_edition_view(session, edition)
    html = _env().get_template("edition.html").render(
        settings=settings,
        edition=view,
        highlights=highlights(view),
        sections=group_sections(view),
        recent=recent_editions(session, exclude_id=edition.id, limit=5, before_date=view.date),
        request=None,
        locale="zh",
    )
    path = out_dir / f"{edition.date.isoformat()}.html"
    path.write_text(html, encoding="utf-8")
    return path


def export_index(session: Session, settings: Settings) -> Path:
    from viberadar.pipeline.feed import feed_view
    out_dir = settings.public_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    html = _env().get_template("index.html").render(
        settings=settings, feed=feed_view(session, settings),
        recent=recent_editions(session, limit=7), request=None, locale="zh")
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def export_rss(session: Session, settings: Settings) -> Path:
    out_dir = settings.public_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rss.xml"
    path.write_text(build_rss(session, settings), encoding="utf-8")
    return path


def export_site(session: Session, settings: Settings) -> list[Path]:
    paths = []
    editions = session.scalars(
        select(EditionRow).where(EditionRow.status == EditionStatus.PUBLISHED.value)
    ).all()
    for edition in editions:
        paths.append(export_edition(session, edition, settings))
    paths.append(export_index(session, settings))
    paths.append(export_rss(session, settings))
    return paths
