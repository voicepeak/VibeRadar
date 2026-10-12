"""运行状态检查（PRD M9/10.6）。

CLI `status` 用于定位任一天的失败阶段、源水位、配额、成本、截至时间和发送账本。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.settings import Settings
from viberadar.store.models import (
    DeliveryAttemptRow,
    EditionRow,
    Source,
)

from .cost import cost_report


def status_report(
    session: Session, settings: Settings, edition_date: date | None = None
) -> dict:
    sources = []
    for source in session.scalars(select(Source).order_by(Source.key)):
        sources.append(
            {
                "key": source.key,
                "enabled": source.enabled,
                "last_success_at": source.last_success_at.isoformat()
                if source.last_success_at
                else None,
                "watermark": source.watermark,
                "notes": source.notes,
            }
        )
    editions = []
    if edition_date is not None:
        from viberadar.store import repo_editions

        edition = repo_editions.get_edition_by_date(
            session, edition_date, settings.pipeline.timezone
        )
        if edition is not None:
            deliveries = [
                {
                    "channel": d.channel,
                    "destination": d.destination,
                    "dispatch_version": d.dispatch_version,
                    "status": d.status,
                    "attempts": d.attempt_count,
                    "error": d.last_error,
                }
                for d in repo_editions.list_deliveries(session, edition.id)
            ]
            from viberadar.store import repo_pipeline

            stage_rows = []
            if edition.run_id:
                stage_rows = [
                    {
                        "stage": s.stage,
                        "status": s.status,
                        "input": s.input_count,
                        "output": s.output_count,
                        "duration_ms": s.duration_ms,
                        "detail": (s.detail or {}).get("errors"),
                    }
                    for s in repo_pipeline.list_stages(session, edition.run_id)
                ]
            editions.append(
                {
                    "date": edition.date.isoformat(),
                    "status": edition.status,
                    "content_status": edition.content_status,
                    "revision": edition.revision,
                    "published_at": edition.published_at.isoformat()
                    if edition.published_at
                    else None,
                    "deliveries": deliveries,
                    "stages": stage_rows,
                }
            )
    open_issues = session.execute(
        select(DeliveryAttemptRow).where(
            DeliveryAttemptRow.status.in_(["retryable_failed", "unknown"])
        )
    ).all()
    return {
        "sources": sources,
        "editions": editions,
        "delivery_alerts": [
            {"id": row[0].id, "status": row[0].status, "error": row[0].last_error}
            for row in open_issues
        ],
        "cost": cost_report(session, settings),
        "timezone": settings.pipeline.timezone,
        "schedule": settings.pipeline.schedule.model_dump(),
    }


def open_edition_summary(session: Session, settings: Settings) -> dict:
    draft = session.scalar(
        select(EditionRow)
        .where(EditionRow.status.in_(["draft", "reviewing", "ready"]))
        .order_by(EditionRow.date.desc())
        .limit(1)
    )
    if draft is None:
        return {}
    return {
        "date": draft.date.isoformat(),
        "status": draft.status,
        "content_status": draft.content_status,
    }
