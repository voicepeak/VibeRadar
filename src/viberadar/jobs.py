"""调度任务（PRD 5.1/5.4/10.1）。

全天每 4 小时增量采集；07:30 补采并冻结截点并跑管线；08:35 冻结编辑版本；
09:00 公开并分发；09:05 失败告警与有界重试。
APScheduler 仅运行在单个 worker 中。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session, sessionmaker

from viberadar.compose import freeze_edition, publish_edition
from viberadar.compose.render import export_site
from viberadar.deliver import dispatch_feishu, record_web_delivery, retry_failed_deliveries
from viberadar.ingest import run_all_sources
from viberadar.llm import LLMGateway, make_gateway
from viberadar.pipeline import run_pipeline
from viberadar.settings import Settings
from viberadar.store import repo_editions, repo_ops, repo_pipeline

logger = logging.getLogger("viberadar.jobs")


def _default_llm(session: Session, settings: Settings) -> LLMGateway | None:
    """调度任务按当前配置构建 Gateway；离线 mock 不注入（避免无意义报错）。"""
    factory = sessionmaker(bind=session.get_bind(), future=True, expire_on_commit=False)
    gateway = make_gateway(factory, settings)
    if gateway is not None and gateway.provider == "mock":
        return None
    return gateway


def _local_now(settings: Settings) -> datetime:
    return datetime.now(timezone.utc).astimezone(settings.pipeline.tz)


def job_ingest_cycle(
    session: Session, settings: Settings, now: datetime | None = None
) -> dict:
    """增量采集（水位向前重叠 2 小时去重）。"""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(
        hours=settings.pipeline.schedule.ingest_every_hours
        + settings.pipeline.window.watermark_overlap_hours
    )
    reports = run_all_sources(session, since=since, until=now, settings=settings)
    failures = [r for r in reports if r.status.value == "failed"]
    if failures:
        logger.warning(
            "采集失败源: %s", ", ".join(r.source_key for r in failures)
        )
    return {"sources": [(r.source_key, r.status.value) for r in reports]}


def job_cutoff_pipeline(
    session: Session,
    settings: Settings,
    edition_date: date,
    llm: LLMGateway | None = None,
    now: datetime | None = None,
) -> dict:
    if llm is None:
        llm = _default_llm(session, settings)
    report = run_pipeline(
        session,
        edition_date,
        settings=settings,
        now=now,
        llm=llm,
        owner="scheduler",
    )
    return {
        "run_id": report.run_id,
        "candidates": report.candidate_count,
        "auto_selected": report.auto_selected,
    }


def job_freeze(
    session: Session,
    settings: Settings,
    edition_date: date,
    llm: LLMGateway | None = None,
) -> dict:
    if llm is None:
        llm = _default_llm(session, settings)
    edition = freeze_edition(
        session,
        edition_date,
        settings=settings,
        run_id=(
            repo_editions.get_edition_by_date(
                session, edition_date, settings.pipeline.timezone
            ).run_id
            if repo_editions.get_edition_by_date(
                session, edition_date, settings.pipeline.timezone
            )
            else None
        ),
        operator="scheduler",
        llm=llm,
    )
    return {"edition_id": edition.id, "status": edition.status}


def job_publish(
    session: Session, settings: Settings, edition_date: date, dry_run: bool = False
) -> dict:
    edition = repo_editions.get_edition_by_date(
        session, edition_date, settings.pipeline.timezone
    )
    if edition is None:
        return {"error": f"{edition_date} 未生成期号（工程失败要单独告警）"}
    publish_edition(session, edition)
    export_error = None
    try:
        export_site(session, settings)
        record_web_delivery(session, edition.id, success=True)
    except Exception as exc:  # noqa: BLE001
        export_error = str(exc)
        record_web_delivery(session, edition.id, success=False, error=export_error)
    results = []
    if not dry_run:
        result = dispatch_feishu(session, edition, settings=settings)
        results.append(
            {
                "channel": "feishu",
                "status": result.status if result else "unknown",
                "error": result.error if result else None,
            }
        )
    return {
        "edition_id": edition.id,
        "published": True,
        "export_error": export_error,
        "deliveries": results,
    }


def job_failure_check(session: Session, settings: Settings, dry_run: bool = False) -> dict:
    """09:05：重试明确失败渠道；unknown 只告警不盲目重发。"""
    retried = retry_failed_deliveries(session, settings=settings, dry_run=dry_run)
    unknown = repo_editions.list_deliveries_by_status(session, "unknown")
    for attempt in unknown:
        logger.warning(
            "投递送达未知（需人工核对）: edition=%s channel=%s attempt=%s",
            attempt.edition_id,
            attempt.channel,
            attempt.id,
        )
    alerts = len([r for _, r in retried if r and r.status != "sent"])
    return {"retried": len(retried), "still_failing": alerts, "unknown": len(unknown)}


def run_due_jobs(
    session_factory: sessionmaker[Session],
    settings: Settings,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[str]:
    """检查并执行到期任务（供 Windows 任务计划或 `scheduler --once` 使用）。

    每项任务：先查"今日已运行"标记，再按当前期号状态决定是否执行；
    任务失败单独记录，不阻塞后续任务；成功后才写运行标记。
    """
    now_utc = now or datetime.now(timezone.utc)
    local = now_utc.astimezone(settings.pipeline.tz)
    edition_date = local.date()
    executed: list[str] = []
    schedule = settings.pipeline

    def _ran(job: str) -> bool:
        return repo_ops.has_lock(session, f"job:{job}:{edition_date.isoformat()}", now_utc)

    def _mark(job: str) -> None:
        repo_ops.acquire_lock(
            session,
            f"job:{job}:{edition_date.isoformat()}",
            owner="scheduler",
            lease_seconds=86400,
            now=now_utc,
        )

    def _attempt(job: str, fn) -> None:  # noqa: ANN001
        if _ran(job):
            return
        try:
            with session.begin_nested():
                fn()
            _mark(job)
            executed.append(job)
        except Exception as exc:  # noqa: BLE001 —— 单项失败不阻塞后续任务
            logger.exception("调度任务失败: %s", job)
            executed.append(f"{job}:ERROR:{exc}")

    with session_factory() as session:
        slot = (local.hour // schedule.schedule.ingest_every_hours) * schedule.schedule.ingest_every_hours
        _attempt(
            f"ingest_{slot}",
            lambda: job_ingest_cycle(session, settings, now=now_utc),
        )

        cutoff_at = datetime.combine(
            edition_date, schedule.cutoff_time, tzinfo=schedule.tz
        )
        existing_run = repo_pipeline.latest_run_for_date(session, edition_date)
        if local >= cutoff_at and existing_run is None:
            _attempt("cutoff", lambda: job_cutoff_pipeline(session, settings, edition_date))
        elif local >= cutoff_at:
            _mark("cutoff")

        review_at = datetime.combine(
            edition_date, schedule.review_deadline_time, tzinfo=schedule.tz
        )
        edition = repo_editions.get_edition_by_date(
            session, edition_date, settings.pipeline.timezone
        )
        if local >= review_at and edition is None:
            _attempt("freeze", lambda: job_freeze(session, settings, edition_date))
        elif local >= review_at:
            _mark("freeze")

        publish_at = datetime.combine(edition_date, schedule.publish_time, tzinfo=schedule.tz)
        edition = repo_editions.get_edition_by_date(
            session, edition_date, settings.pipeline.timezone
        )
        if local >= publish_at and edition is not None and edition.status == "ready":
            _attempt(
                "publish",
                lambda: job_publish(session, settings, edition_date, dry_run=dry_run),
            )
        elif local >= publish_at:
            _mark("publish")

        failure_at = datetime.combine(
            edition_date, schedule.failure_check_time, tzinfo=schedule.tz
        )
        if local >= failure_at:
            _attempt(
                "failure",
                lambda: job_failure_check(session, settings, dry_run=dry_run),
            )
        session.commit()
    return executed


def build_scheduler(settings: Settings, session_factory: sessionmaker[Session]):
    """APScheduler 入口：单 worker 内运行。"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone=settings.pipeline.tz)
    schedule = settings.pipeline

    def _safe(fn):  # noqa: ANN001, ANN202
        def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003
            with session_factory() as session:
                try:
                    fn(session, settings, *args, **kwargs)
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
                    logger.exception("任务失败: %s", fn.__name__)

        return wrapper

    scheduler.add_job(
        _safe(job_ingest_cycle),
        "interval",
        hours=schedule.schedule.ingest_every_hours,
        id="ingest_cycle",
    )
    scheduler.add_job(
        _safe(lambda s, st, **kw: job_cutoff_pipeline(s, st, datetime.now(st.pipeline.tz).date())),
        "cron",
        hour=schedule.cutoff_time.hour,
        minute=schedule.cutoff_time.minute,
        id="cutoff_pipeline",
    )
    scheduler.add_job(
        _safe(lambda s, st, **kw: job_freeze(s, st, datetime.now(st.pipeline.tz).date())),
        "cron",
        hour=schedule.review_deadline_time.hour,
        minute=schedule.review_deadline_time.minute,
        id="freeze",
    )
    scheduler.add_job(
        _safe(lambda s, st, **kw: job_publish(s, st, datetime.now(st.pipeline.tz).date())),
        "cron",
        hour=schedule.publish_time.hour,
        minute=schedule.publish_time.minute,
        id="publish",
    )
    scheduler.add_job(
        _safe(lambda s, st, **kw: job_failure_check(s, st)),
        "cron",
        hour=schedule.failure_check_time.hour,
        minute=schedule.failure_check_time.minute,
        id="failure_check",
    )
    return scheduler
