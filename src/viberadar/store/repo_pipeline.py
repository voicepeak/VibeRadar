"""流水线仓储：运行、阶段、分类结果、候选（PRD M3–M5.5）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar.contracts import Classification, StageResult, new_id, utcnow

from .models import (
    CandidateRow,
    ClassificationRow,
    PipelineRunRow,
    StageRunRow,
)


def create_pipeline_run(
    session: Session,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    cutoff: datetime,
    code_version: str,
    config_hash: str,
    prompt_hash: str | None = None,
    model_ids: list[str] | None = None,
    replay_of: str | None = None,
    dry_run: bool = False,
    edition_id: str | None = None,
) -> PipelineRunRow:
    row = PipelineRunRow(
        run_id=run_id,
        edition_id=edition_id,
        window_start=window_start,
        window_end=window_end,
        cutoff=cutoff,
        status="success",
        code_version=code_version,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        model_ids=model_ids or [],
        started_at=utcnow(),
        replay_of=replay_of,
        dry_run=dry_run,
    )
    session.add(row)
    session.flush()
    return row


def finish_pipeline_run(session: Session, run_id: str, status: str) -> None:
    row = session.get(PipelineRunRow, run_id)
    if row:
        row.status = status
        row.finished_at = utcnow()
        session.flush()


def get_pipeline_run(session: Session, run_id: str) -> PipelineRunRow | None:
    return session.get(PipelineRunRow, run_id)


def record_stage(session: Session, result: StageResult) -> StageRunRow:
    row = StageRunRow(
        id=new_id("stg"),
        run_id=result.run_id,
        stage=result.stage,
        status=result.status.value if hasattr(result.status, "value") else str(result.status),
        input_count=result.input_count,
        output_count=result.output_count,
        error_ids=list(result.error_ids),
        artifact_ids=list(result.artifact_ids),
        duration_ms=result.duration_ms,
        cost_usd=Decimal(result.cost_usd),
        cache_key=result.cache_key,
        detail=dict(result.detail),
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def list_stages(session: Session, run_id: str) -> list[StageRunRow]:
    return list(
        session.scalars(
            select(StageRunRow).where(StageRunRow.run_id == run_id).order_by(StageRunRow.created_at)
        )
    )


def latest_run_for_date(session: Session, edition_date: date) -> PipelineRunRow | None:
    from datetime import time, timedelta, timezone

    from viberadar.settings import get_settings

    tz = get_settings().pipeline.tz
    start = datetime.combine(edition_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return session.scalar(
        select(PipelineRunRow)
        .where(PipelineRunRow.cutoff >= start, PipelineRunRow.cutoff < end)
        .order_by(PipelineRunRow.started_at.desc())
        .limit(1)
    )


def upsert_classification(
    session: Session,
    edition_date: date,
    classification: Classification,
    product_id: str,
    event_id: str | None,
    raw_item_id: str | None,
    source: str = "llm",
) -> ClassificationRow:
    row = session.scalar(
        select(ClassificationRow).where(
            ClassificationRow.edition_date == edition_date,
            ClassificationRow.product_id == product_id,
        )
    )
    now = utcnow()
    if row is None:
        row = ClassificationRow(
            id=new_id("cls"),
            edition_date=edition_date,
            product_id=product_id,
            event_id=event_id,
            raw_item_id=raw_item_id,
            relevant=classification.relevant,
            category=classification.category,
            confidence=classification.confidence,
            spam_score=classification.spam_score,
            reason=classification.reason,
            evidence_ids=list(classification.evidence_ids),
            needs_review=classification.needs_review,
            model_confidence=classification.model_confidence or classification.confidence,
            source=source,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.event_id = event_id or row.event_id
        row.raw_item_id = raw_item_id or row.raw_item_id
        row.relevant = classification.relevant
        row.category = classification.category
        row.confidence = classification.confidence
        row.spam_score = classification.spam_score
        row.reason = classification.reason
        row.evidence_ids = list(classification.evidence_ids)
        row.needs_review = classification.needs_review
        if classification.model_confidence is not None:
            row.model_confidence = classification.model_confidence
        row.source = source
        row.updated_at = now
    session.flush()
    return row


def get_classification(
    session: Session, edition_date: date, product_id: str
) -> ClassificationRow | None:
    return session.scalar(
        select(ClassificationRow).where(
            ClassificationRow.edition_date == edition_date,
            ClassificationRow.product_id == product_id,
        )
    )


def list_classifications(session: Session, edition_date: date) -> list[ClassificationRow]:
    return list(
        session.scalars(
            select(ClassificationRow)
            .where(ClassificationRow.edition_date == edition_date)
            .order_by(ClassificationRow.created_at)
        )
    )


def upsert_candidate(
    session: Session,
    edition_date: date,
    run_id: str,
    product_id: str,
    event_id: str | None,
    auto_selected: bool,
    final_score: float,
    signals: dict,
    content_json: dict | None,
) -> CandidateRow:
    row = session.scalar(
        select(CandidateRow).where(
            CandidateRow.edition_date == edition_date,
            CandidateRow.product_id == product_id,
        )
    )
    now = utcnow()
    if row is None:
        row = CandidateRow(
            id=new_id("cnd"),
            edition_date=edition_date,
            run_id=run_id,
            product_id=product_id,
            event_id=event_id,
            status="candidate",
            auto_selected=auto_selected,
            final_score=final_score,
            signals=signals,
            content_json=content_json,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.run_id = run_id
        row.event_id = event_id or row.event_id
        row.auto_selected = auto_selected
        # 重放默认不覆盖已保存的人工编辑（PRD 5.2）
        if not row.locked and row.curator_status is None:
            row.final_score = final_score
            row.signals = signals
            if content_json is not None:
                row.content_json = content_json
        row.updated_at = now
    session.flush()
    return row


def list_candidates(session: Session, edition_date: date) -> list[CandidateRow]:
    return list(
        session.scalars(
            select(CandidateRow)
            .where(CandidateRow.edition_date == edition_date)
            .order_by(CandidateRow.final_score.desc(), CandidateRow.created_at)
        )
    )


def get_candidate(
    session: Session, edition_date: date, product_id: str
) -> CandidateRow | None:
    return session.scalar(
        select(CandidateRow).where(
            CandidateRow.edition_date == edition_date,
            CandidateRow.product_id == product_id,
        )
    )


def apply_curator_change(
    session: Session,
    candidate: CandidateRow,
    status: str | None = None,
    reason: str | None = None,
    edits: dict | None = None,
    locked: bool | None = None,
    position_hint: int | None = None,
) -> CandidateRow:
    if status is not None:
        candidate.curator_status = status
    if reason is not None:
        candidate.curator_edits = {**(candidate.curator_edits or {}), "reason": reason}
    if edits:
        candidate.curator_edits = {**(candidate.curator_edits or {}), **edits}
    if locked is not None:
        candidate.locked = locked
    if position_hint is not None:
        candidate.position_hint = position_hint
    candidate.updated_at = utcnow()
    session.flush()
    return candidate
