"""RSS 生成（PRD 4.6 P1；永久 GUID，重试不生成重复条目）。"""

from __future__ import annotations

from email.utils import format_datetime
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.settings import Settings
from viberadar.store.models import EditionRow


def build_rss(session: Session, settings: Settings, limit: int = 30) -> str:
    base = settings.site_base_url.rstrip("/")
    editions = list(
        session.scalars(
            select(EditionRow)
            .where(EditionRow.status == "published")
            .order_by(EditionRow.date.desc())
            .limit(limit)
        )
    )
    items_xml = []
    for edition in editions:
        date_str = edition.date.isoformat()
        link = f"{base}/d/{date_str}"
        pub = format_datetime(edition.published_at) if edition.published_at else ""
        title = f"Vibe Radar {date_str} · {edition.intro or '今日精选'}"
        guid = f"urn:viberadar:edition:{edition.id}"
        items_xml.append(
            "<item>"
            f"<title>{escape(title)}</title>"
            f"<link>{escape(link)}</link>"
            f"<guid isPermaLink=\"false\">{escape(guid)}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<description>{escape(edition.intro or '')}</description>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel>"
        "<title>Vibe Radar</title>"
        f"<link>{escape(base)}</link>"
        "<description>每日中文 AI builder 新品精选</description>"
        f'<atom:link href="{escape(base)}/rss.xml" rel="self" type="application/rss+xml"/>'
        + "".join(items_xml)
        + "</channel></rss>"
    )
