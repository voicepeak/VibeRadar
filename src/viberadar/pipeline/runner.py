"""管线编排（PRD 第 5–7 章）：采集 → 归一化 → 分类 → 富化 → 排序 → 候选池。

每阶段落库；重放沿用原窗口；dry_run 不外发；按日期互斥锁防双调度。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from viberadar import __version__
from viberadar.contracts import (
    InterestDims,
    ScoreSignals,
    SignalValue,
    StageResult,
    StageStatus,
    new_id,
    utcnow,
)
from viberadar.ingest import run_all_sources
from viberadar.llm import LLMGateway
from viberadar.rank import (
    best_across_sources,
    bonus_cross_source,
    bonus_low_heat_creative,
    bonus_trial_category,
    compute_breakdown,
    midrank_percentile,
    novelty_value,
    review_pool_limit,
    select_public_items,
    velocity_from_snapshots,
)
from viberadar.rank.diversity import DiversityItem
from viberadar.settings import Settings, get_settings
from viberadar.store import repo_ingest, repo_ops, repo_pipeline, repo_products
from viberadar.store.models import (
    ClassificationRow,
    EnrichmentRow,
    ProductSourceRow,
    RawItemRow,
)

from .classify import classify_items
from .enrich import EnrichOutcome, enrich_product
from .entities import (
    ResolvedItem,
    has_update_event,
    resolve_entities,
    was_published_recently,
)


@dataclass
class CandidateData:
    resolved: ResolvedItem
    classification: ClassificationRow | None
    score: object
    content: dict
    auto_eligible: bool
    reasons: list[str] = field(default_factory=list)
    interest: float | None = None
    dims: InterestDims | None = None
    enrichment: EnrichOutcome | None = None


@dataclass
class PipelineReport:
    run_id: str
    edition_date: date
    window_start: datetime
    window_end: datetime
    stages: dict[str, StageResult] = field(default_factory=dict)
    candidate_count: int = 0
    auto_selected: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def compute_cutoff(
    settings: Settings, edition_date: date, now: datetime | None = None
) -> datetime:
    """截点 T = 当日 07:30（Asia/Shanghai）；手动提前运行时用 now 兜底。"""
    now = now or utcnow()
    local_cutoff = datetime.combine(
        edition_date, settings.pipeline.cutoff_time, tzinfo=settings.pipeline.tz
    )
    cutoff = local_cutoff.astimezone(timezone.utc)
    if now < cutoff:
        return now
    return cutoff


def run_pipeline(
    session: Session,
    edition_date: date,
    settings: Settings | None = None,
    cutoff_at: datetime | None = None,
    now: datetime | None = None,
    llm: LLMGateway | None = None,
    transports: dict | None = None,
    manual_entries: list[dict] | None = None,
    skip_ingest: bool = False,
    dry_run: bool = False,
    replay_of: str | None = None,
    mock_classify_response: dict | None = None,
    mock_guide_response: dict | None = None,
    mock_interest_response: dict | None = None,
    owner: str = "pipeline",
) -> PipelineReport:
    settings = settings or get_settings()
    now = now or utcnow()
    cutoff = cutoff_at or compute_cutoff(settings, edition_date, now)
    window_start = cutoff - timedelta(hours=settings.pipeline.window.main_hours)
    lock_key = f"pipeline:{edition_date.isoformat()}"
    if not repo_ops.acquire_lock(session, lock_key, owner=owner, lease_seconds=1800, now=now):
        raise RuntimeError(f"流水线互斥锁被占用：{lock_key}（防止双调度生成两期）")
    try:
        return _run(
            session,
            edition_date,
            settings,
            cutoff,
            window_start,
            now=now,
            llm=llm,
            transports=transports,
            manual_entries=manual_entries,
            skip_ingest=skip_ingest,
            dry_run=dry_run,
            replay_of=replay_of,
            mock_classify_response=mock_classify_response,
            mock_guide_response=mock_guide_response,
            mock_interest_response=mock_interest_response,
        )
    finally:
        repo_ops.release_lock(session, lock_key, owner)


def _stage(
    session: Session,
    run_id: str,
    name: str,
    status: StageStatus,
    started: float,
    input_count: int = 0,
    output_count: int = 0,
    errors: list[str] | None = None,
    detail: dict | None = None,
    artifact_ids: list[str] | None = None,
    cost_usd: float = 0.0,
) -> StageResult:
    result = StageResult(
        run_id=run_id,
        stage=name,
        status=status,
        input_count=input_count,
        output_count=output_count,
        error_ids=[],
        artifact_ids=artifact_ids or [],
        duration_ms=int((time.monotonic() - started) * 1000),
        cost_usd=Decimal(str(round(cost_usd, 6))),
        detail=detail or {},
    )
    if errors:
        result.detail = {**result.detail, "errors": errors[:20]}
    repo_pipeline.record_stage(session, result)
    return result


def _run(
    session: Session,
    edition_date: date,
    settings: Settings,
    cutoff: datetime,
    window_start: datetime,
    *,
    now: datetime,
    llm: LLMGateway | None,
    transports: dict | None,
    manual_entries: list[dict] | None,
    skip_ingest: bool,
    dry_run: bool,
    replay_of: str | None,
    mock_classify_response: dict | None,
    mock_guide_response: dict | None,
    mock_interest_response: dict | None,
) -> PipelineReport:
    run_id = new_id("run")
    repo_pipeline.create_pipeline_run(
        session,
        run_id=run_id,
        window_start=window_start,
        window_end=cutoff,
        cutoff=cutoff,
        code_version=__version__,
        config_hash=settings.config_hash,
        prompt_hash=settings.prompt_hash,
        model_ids=[llm.provider] if llm else [],
        replay_of=replay_of,
        dry_run=dry_run,
    )
    report = PipelineReport(
        run_id=run_id,
        edition_date=edition_date,
        window_start=window_start,
        window_end=cutoff,
        dry_run=dry_run,
    )

    # ---- M1 采集 ----
    started = time.monotonic()
    if skip_ingest:
        report.stages["ingest"] = _stage(
            session,
            run_id,
            "ingest",
            StageStatus.SKIPPED,
            started,
            detail={"note": "复用既有原始条目（重放）"},
        )
    else:
        since = window_start - timedelta(
            hours=settings.pipeline.window.watermark_overlap_hours
        )
        ingest_reports = run_all_sources(
            session,
            since=since,
            until=cutoff,
            settings=settings,
            transports=transports,
            manual_entries=manual_entries,
        )
        per_source = {
            r.source_key: {
                "status": r.status.value,
                "fetched": r.raw_fetched,
                "created": r.created,
                "updated": r.updated,
                "errors": r.errors,
                "requests": r.request_count,
                "coverage": r.coverage_note,
            }
            for r in ingest_reports
        }
        failed = [r for r in ingest_reports if r.status == StageStatus.FAILED]
        partial = [r for r in ingest_reports if r.status == StageStatus.PARTIAL]
        status = (
            StageStatus.FAILED
            if failed and len(failed) == len(ingest_reports)
            else (StageStatus.PARTIAL if (failed or partial) else StageStatus.SUCCESS)
        )
        report.stages["ingest"] = _stage(
            session,
            run_id,
            "ingest",
            status,
            started,
            output_count=sum(r.raw_fetched for r in ingest_reports),
            detail={"sources": per_source},
        )

    # ---- M2 归一化与实体解析 ----
    started = time.monotonic()
    lookback_start = cutoff - timedelta(days=settings.pipeline.window.late_discovery_days)
    raw_items = repo_ingest.list_raw_items_between(session, lookback_start, cutoff)
    linked_ids = {
        row[0] for row in session.execute(select(ProductSourceRow.raw_item_id)).all()
    }
    selected_raw: list[RawItemRow] = []
    for raw in raw_items:
        is_new_this_window = (raw.first_fetched_at or lookback_start) >= window_start
        if is_new_this_window or raw.id not in linked_ids:
            selected_raw.append(raw)
    resolve_report = resolve_entities(
        session, selected_raw, settings, window_start, cutoff, now=now
    )
    report.stages["normalize"] = _stage(
        session,
        run_id,
        "normalize",
        StageStatus.SUCCESS,
        started,
        input_count=len(selected_raw),
        output_count=len(resolve_report.resolved),
        errors=resolve_report.errors,
        detail={
            "created_products": resolve_report.created_products,
            "created_events": resolve_report.created_events,
            "pending_relations": resolve_report.pending_relations,
        },
    )

    # ---- M3 分类 ----
    started = time.monotonic()
    classify_report = classify_items(
        session,
        resolve_report.resolved,
        edition_date,
        run_id,
        settings=settings,
        llm=llm,
        mock_llm_response=mock_classify_response,
    )
    report.stages["classify"] = _stage(
        session,
        run_id,
        "classify",
        StageStatus.PARTIAL if classify_report.errors else StageStatus.SUCCESS,
        started,
        input_count=len(resolve_report.resolved),
        output_count=classify_report.classified,
        errors=classify_report.errors,
        detail={
            "auto_eligible": classify_report.auto_eligible,
            "needs_review": classify_report.needs_review,
            "excluded": classify_report.excluded,
        },
        cost_usd=classify_report.cost_usd,
    )

    # ---- 排序：粗筛（预算内，不代表最终兴趣排序） ----
    scored_entries = _preliminary_scores(session, resolve_report.resolved, edition_date)

    # ---- M4 富化（深度处理 ≤40；强模型 ≤12） ----
    started = time.monotonic()
    deep_limit = settings.pipeline.limits.deep_enrich_limit
    strong_limit = settings.pipeline.limits.strong_model_limit
    enrich_map: dict[str, EnrichOutcome] = {}
    enrich_errors: list[str] = []
    for idx, entry in enumerate(scored_entries[:deep_limit]):
        outcome = enrich_product(
            session,
            entry.product,
            entry.raw_item,
            settings=settings,
            transport=(transports or {}).get("__enrich__"),
            llm=llm,
            use_strong_model=idx < strong_limit,
            mock_guide_response=mock_guide_response,
        )
        enrich_map[entry.product.id] = outcome
        if outcome.error:
            enrich_errors.append(f"{entry.product.slug}: {outcome.error}")
        if outcome.enrichment is not None:
            _store_enrichment(session, entry.product, entry.event, outcome)
    report.stages["enrich"] = _stage(
        session,
        run_id,
        "enrich",
        StageStatus.PARTIAL if enrich_errors else StageStatus.SUCCESS,
        started,
        input_count=min(len(scored_entries), deep_limit),
        output_count=len(enrich_map),
        errors=enrich_errors,
    )

    # ---- M5 排序（信号 + 兴趣维度 + 7.2 公式） ----
    started = time.monotonic()
    source_maps = _build_source_maps(session, scored_entries, settings, cutoff, window_start)
    candidates: list[CandidateData] = []
    for entry in scored_entries:
        outcome = enrich_map.get(entry.product.id)
        dims = _assess_interest(
            session,
            entry,
            outcome,
            settings=settings,
            llm=llm,
            mock_interest_response=mock_interest_response,
        )
        interest = dims.llm_interest() if dims else None
        classification = repo_pipeline.get_classification(
            session, edition_date, entry.product.id
        )
        score = _final_score(entry, settings, dims, cutoff, source_maps, classification)
        content = _card_content(session, entry, outcome, settings, dims, classification)
        auto, reasons = _passes_gates(
            session, entry, classification, dims, outcome, settings, cutoff
        )
        content["quality_flags"] = reasons
        candidates.append(
            CandidateData(
                resolved=entry,
                classification=classification,
                score=score,
                content=content,
                auto_eligible=auto,
                reasons=reasons,
                interest=interest,
                dims=dims,
                enrichment=outcome,
            )
        )
    candidates.sort(
        key=lambda c: (-c.score.final_score, -(c.interest or 0), c.resolved.product.id)
    )
    report.stages["rank"] = _stage(
        session,
        run_id,
        "rank",
        StageStatus.SUCCESS,
        started,
        input_count=len(scored_entries),
        output_count=len(candidates),
    )

    # ---- M5.5 复核池（≤20；同分类 ≤8） ----
    started = time.monotonic()
    pool_items = [
        DiversityItem(
            key=c.resolved.product.id,
            category=c.content.get("category", "unknown") or "unknown",
            score=c.score.final_score,
            interest=c.interest or 0.0,
            locked=False,
            extra={"candidate": c},
        )
        for c in candidates
        if c.resolved.exclusion_reason is None
        and c.resolved.event.status == "active"
    ]
    pool = review_pool_limit(
        pool_items,
        settings.pipeline.limits.candidate_limit,
        category_max=int(settings.rubric.diversity.get("review_pool_category_max", 8)),
    )
    auto_candidates = [c for c in candidates if c.auto_eligible]
    selected_auto = select_public_items(
        [
            DiversityItem(
                key=c.resolved.product.id,
                category=c.content.get("category", "unknown") or "unknown",
                score=c.score.final_score,
                interest=c.interest or 0.0,
            )
            for c in auto_candidates
        ],
        target=settings.pipeline.limits.public_target,
        ratio=float(settings.rubric.diversity.get("public_single_category_ratio", 0.4)),
        min_categories=int(settings.rubric.diversity.get("public_min_categories", 3)),
        same_topic_max=int(settings.rubric.diversity.get("same_topic_max", 2)),
        ratio_min_count=settings.pipeline.limits.public_min,
    )
    auto_selected_ids = {item.key for item in selected_auto}

    for item in pool:
        candidate: CandidateData = item.extra["candidate"]
        repo_pipeline.upsert_candidate(
            session,
            edition_date=edition_date,
            run_id=run_id,
            product_id=candidate.resolved.product.id,
            event_id=candidate.resolved.event.id,
            auto_selected=candidate.resolved.product.id in auto_selected_ids,
            final_score=candidate.score.final_score,
            signals=candidate.score.model_dump(mode="json"),
            content_json=candidate.content,
        )
    report.candidate_count = len(pool)
    report.auto_selected = len(auto_selected_ids)
    report.stages["select"] = _stage(
        session,
        run_id,
        "select",
        StageStatus.SUCCESS,
        started,
        input_count=len(candidates),
        output_count=len(pool),
        detail={"auto_selected": len(auto_selected_ids)},
    )

    repo_pipeline.finish_pipeline_run(
        session, run_id, status="success" if report.auto_selected > 0 else "partial"
    )
    return report


# ---------- 评分与门控 ----------


def _preliminary_scores(
    session: Session, resolved: list[ResolvedItem], edition_date: date
) -> list[ResolvedItem]:
    """粗筛排序：仅用于预算内的富化名额分配，不是最终兴趣排序。"""

    def key(entry: ResolvedItem):
        classification = repo_pipeline.get_classification(
            session, edition_date, entry.product.id
        )
        confidence = float(classification.confidence) if classification else 0.0
        event_ts = entry.event.event_at.timestamp() if entry.event.event_at else 0.0
        return (-confidence, -event_ts)

    return sorted(resolved, key=key)


def _raws_first_fetched_between(
    session: Session, source_key: str, start: datetime, end: datetime
) -> list[RawItemRow]:
    return list(
        session.scalars(
            select(RawItemRow).where(
                RawItemRow.source_key == source_key,
                RawItemRow.first_fetched_at >= start,
                RawItemRow.first_fetched_at < end,
            )
        )
    )


def _latest_heat(session: Session, raw: RawItemRow) -> float | None:
    snapshots = repo_ingest.latest_metrics(session, raw.id, limit=1)
    if snapshots:
        metrics = snapshots[0].metrics
        for field in ("points", "stars", "votes"):
            if metrics.get(field) is not None:
                return float(metrics[field])
    return None


def _build_source_maps(
    session: Session,
    entries: list[ResolvedItem],
    settings: Settings,
    cutoff: datetime,
    window_start: datetime,
) -> dict[str, dict[str, dict[str, SignalValue]]]:
    """按每源固定集合（窗口内首次抓取）计算热度/速度分位数。

    返回 {source_key: {"heat": {product_id: SignalValue},
                        "velocity": {product_id: SignalValue}}}
    """
    rubric = settings.rubric
    source_keys = {e.raw_item.source_key for e in entries}
    maps: dict[str, dict[str, dict[str, SignalValue]]] = {}
    for source_key in source_keys:
        raws = _raws_first_fetched_between(session, source_key, window_start, cutoff)
        if not raws:
            raws = [e.raw_item for e in entries if e.raw_item.source_key == source_key]
        product_by_raw = _raw_products(session, [r.id for r in raws])
        heat_values = [_latest_heat(session, r) for r in raws]
        heat_percentiles = midrank_percentile(
            heat_values,
            min_samples=int(rubric.source_signal.get("min_samples", 5)),
            missing_default=float(rubric.source_signal.get("missing_default", 0.5)),
        )
        heat_by_product: dict[str, SignalValue] = {}
        for raw, percentile in zip(raws, heat_percentiles):
            product_id = product_by_raw.get(raw.id)
            if not product_id:
                continue
            current = heat_by_product.get(product_id)
            if current is None or (
                not percentile.missing
                and (current.missing or percentile.value > current.value)
            ):
                heat_by_product[product_id] = percentile

        velocity_raw: dict[str, SignalValue] = {}
        seen_products: set[str] = set()
        for raw in raws:
            product_id = product_by_raw.get(raw.id)
            if not product_id or product_id in seen_products:
                continue
            seen_products.add(product_id)
            snapshots = repo_ingest.latest_metrics(session, raw.id, limit=6)
            points: list[tuple[datetime, float]] = []
            for snap in snapshots:
                for field in ("points", "stars", "votes"):
                    if snap.metrics.get(field) is not None:
                        points.append((snap.captured_at, float(snap.metrics[field])))
                        break
            velocity = velocity_from_snapshots(
                points, min_hours=float(rubric.velocity.get("min_hours", 1))
            )
            if velocity is None:
                velocity_raw[product_id] = SignalValue(
                    value=float(rubric.velocity.get("missing_default", 0.5)),
                    missing=True,
                    reason="速度样本不足（<2 快照或 <1 小时）",
                )
            else:
                velocity_raw[product_id] = SignalValue(value=velocity)

        velocity_by_product = _percentile_within(velocity_raw, rubric.velocity)
        maps[source_key] = {"heat": heat_by_product, "velocity": velocity_by_product}
    return maps


def _percentile_within(
    values: dict[str, SignalValue], config: dict
) -> dict[str, SignalValue]:
    if not values:
        return {}
    min_samples = int(config.get("min_samples", 5))
    missing_default = float(config.get("missing_default", 0.5))
    present = [v.value for v in values.values() if not v.missing]
    keys = list(values.keys())
    if len(present) < min_samples or len(set(present)) <= 1:
        return {
            key: SignalValue(
                value=missing_default,
                missing=True,
                reason="速度样本不足或全相同",
            )
            for key in keys
        }
    percentiles = midrank_percentile(
        [v.value if not v.missing else None for v in values.values()],
        min_samples=min_samples,
        missing_default=missing_default,
    )
    return dict(zip(keys, percentiles))


def _final_score(
    entry: ResolvedItem,
    settings: Settings,
    dims: InterestDims | None,
    cutoff: datetime,
    source_maps: dict[str, dict[str, dict[str, SignalValue]]],
    classification: ClassificationRow | None = None,
):
    rubric = settings.rubric
    source_map = source_maps.get(entry.raw_item.source_key, {"heat": {}, "velocity": {}})
    heat_by_product = source_map.get("heat", {})
    velocity_by_product = source_map.get("velocity", {})
    per_source_heat: dict[str, SignalValue] = {}
    per_source_velocity: dict[str, SignalValue] = {}
    if entry.product.id in heat_by_product:
        per_source_heat[entry.raw_item.source_key] = heat_by_product[entry.product.id]
    if entry.product.id in velocity_by_product:
        per_source_velocity[entry.raw_item.source_key] = velocity_by_product[entry.product.id]
    source_signal = best_across_sources(per_source_heat)
    velocity_signal = best_across_sources(per_source_velocity)
    novelty = novelty_value(
        entry.event.event_at,
        cutoff,
        unknown_time=entry.event.time_unconfirmed,
        half_life_hours=float(rubric.novelty.get("half_life_hours", 18)),
        unknown_cap=float(rubric.novelty.get("unknown_time_cap", 0.5)),
    )
    classification = classification or _latest_classification(entry)
    spam = float(classification.spam_score) if classification else 0.0
    interest = dims.llm_interest() if dims else None
    taste = dims.taste_match(rubric.taste_weights) if dims else None
    signals = ScoreSignals(
        source_signal=source_signal,
        velocity=velocity_signal,
        novelty=novelty,
        llm_interest=SignalValue(
            value=interest if interest is not None else 0.0,
            missing=interest is None,
            reason=None if interest is not None else "兴趣维度未知",
        ),
        taste_match=SignalValue(
            value=taste if taste is not None else 0.0,
            missing=taste is None,
            reason=None if taste is not None else "品味维度未知",
        ),
        spam_score=SignalValue(value=spam),
    )

    bonuses = []
    source_count = len(_distinct_sources(entry))
    b = bonus_cross_source(source_count, min_sources=rubric.cross_source_min_sources)
    if b:
        bonuses.append(b)
    creativity = dims.creativity if dims else None
    heat_for_bonus = per_source_heat.get(entry.raw_item.source_key, source_signal)
    b = bonus_low_heat_creative(
        creativity,
        interest,
        heat_for_bonus,
        heat_missing=heat_for_bonus.missing,
        heat_max=rubric.low_heat_percentile_max,
    )
    if b:
        bonuses.append(b)
    b = bonus_trial_category(
        classification.category if classification else None, rubric.trial_categories
    )
    if b:
        bonuses.append(b)

    weights = {
        "source_signal": rubric.score_formula.get("source_signal", 0.20),
        "velocity": rubric.score_formula.get("velocity", 0.15),
        "novelty": rubric.score_formula.get("novelty", 0.25),
        "llm_interest": rubric.score_formula.get("llm_interest", 0.25),
        "taste_match": rubric.score_formula.get("taste_match", 0.15),
        "spam_score": rubric.score_formula.get("spam_score", -0.30),
    }
    return compute_breakdown(
        entry.product.id,
        signals,
        weights,
        bonuses,
        max_total_bonus=float(rubric.bonuses.get("max_total", 0.08)),
    )


def _raw_products(session: Session, raw_ids: list[str]) -> dict[str, str]:
    if not raw_ids:
        return {}
    rows = session.execute(
        select(ProductSourceRow.raw_item_id, ProductSourceRow.product_id).where(
            ProductSourceRow.raw_item_id.in_(raw_ids)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _distinct_sources(entry: ResolvedItem) -> set[str]:
    metadata = entry.raw_item.metadata_json or {}
    sources = {"manual"} if metadata.get("manual") else set()
    sources.add(entry.raw_item.source_key)
    extra = metadata.get("also_seen_sources")
    if isinstance(extra, list):
        sources.update(str(s) for s in extra)
    return sources


def _latest_classification(entry: ResolvedItem) -> ClassificationRow | None:
    classification = getattr(entry, "classification", None)
    if classification is not None:
        return classification
    return None


def _heuristic_dims(entry: ResolvedItem, outcome: EnrichOutcome | None) -> InterestDims:
    """离线兜底维度：基于已抓取事实的确定性估计（真实部署使用模型评估）。"""
    description = entry.raw_item.description or ""
    page_reachable = bool(outcome and outcome.page and outcome.page.reachable)
    demo_like = (
        page_reachable
        and outcome is not None
        and outcome.page is not None
        and not outcome.page.login_wall
    )
    text_len = len(description)
    return InterestDims(
        creativity=4 if text_len > 120 else 3,
        playability=4 if demo_like else 2,
        completeness=4 if page_reachable else 3,
        surprise=3,
        niche=3,
    )


def _assess_interest(
    session: Session,
    entry: ResolvedItem,
    outcome: EnrichOutcome | None,
    settings: Settings,
    llm: LLMGateway | None,
    mock_interest_response: dict | None,
) -> InterestDims | None:
    if llm is not None:
        response = llm.chat_json(
            prompt_name="interest",
            system="",
            payload={"item_id": entry.raw_item.id},
            model="deepseek-flash",
            stage="interest",
            max_output_tokens=800,
            mock_response=mock_interest_response,
        )
        if response.ok and response.data:
            try:
                dims = InterestDims(**(response.data.get("dims") or response.data))
                if dims.llm_interest() is not None:
                    return dims
            except Exception:  # noqa: BLE001
                pass
    if not settings.strict_llm:
        return _heuristic_dims(entry, outcome)
    return None


def _store_enrichment(session: Session, product, event, outcome: EnrichOutcome) -> None:
    enrichment = outcome.enrichment
    if enrichment is None:
        return
    existing = session.scalar(
        select(EnrichmentRow).where(
            EnrichmentRow.product_id == product.id,
            EnrichmentRow.event_id == event.id,
        )
    )
    payload = {
        "access_json": enrichment.access.model_dump(mode="json"),
        "try_guide_json": {
            "one_liner": enrichment.one_liner,
            "why_interesting": enrichment.why_interesting,
            "user_story": enrichment.user_story,
            "mode": enrichment.mode.value,
            "steps": [s.text for s in enrichment.steps],
            "time_promise": enrichment.time_promise,
            "en": enrichment.en.model_dump(mode="json") if enrichment.en else None,
        },
        "verification": enrichment.verification_status.value,
        "claim_refs": [c.model_dump() for c in enrichment.claims],
        "prompt_version": enrichment.prompt_version,
        "model_version": enrichment.model_version,
    }
    if existing is not None:
        for key, value in payload.items():
            setattr(existing, key, value)
        session.flush()
        return
    session.add(
        EnrichmentRow(
            id=new_id("enr"),
            product_id=product.id,
            event_id=event.id,
            created_at=utcnow(),
            **payload,
        )
    )
    session.flush()


def _card_content(
    session: Session,
    entry: ResolvedItem,
    outcome: EnrichOutcome | None,
    settings: Settings,
    dims: InterestDims | None,
    classification: ClassificationRow | None = None,
) -> dict:
    from viberadar.contracts import MobileSupport

    enrichment = outcome.enrichment if outcome else None
    category = (classification.category if classification else None) or entry.product.category or "unknown"
    label = settings.taxonomy.labels().get(category, category)
    sources = [{"label": "来源", "url": entry.raw_item.source_url}]
    if entry.raw_item.target_url:
        sources.append({"label": "项目/官网", "url": entry.raw_item.target_url})

    if enrichment is not None:
        mobile_note = None
        if enrichment.access.mobile_support == MobileSupport.UNSUPPORTED:
            mobile_note = "需桌面设备"
        elif enrichment.access.mobile_support == MobileSupport.UNKNOWN:
            mobile_note = "手机体验未确认"
        card = {
            "name": entry.product.name,
            "category": category,
            "category_label": label,
            "one_liner": enrichment.one_liner,
            "why_interesting": enrichment.why_interesting,
            "user_story": enrichment.user_story,
            "mode": enrichment.mode.value,
            "steps": [s.text for s in enrichment.steps],
            "access_text": enrichment.access.display_text(),
            "access_json": enrichment.access.model_dump(mode="json"),
            "time_promise": enrichment.time_promise,
            "mobile_note": mobile_note,
            "mobile_note_key": (
                "mobile.desktop_required"
                if enrichment.access.mobile_support == MobileSupport.UNSUPPORTED
                else "mobile.unknown"
            ),
            "evidence_status": enrichment.verification_status.value,
            "checked_at": enrichment.access.checked_at.isoformat(),
            "source_links": sources,
            "evidence_ids": outcome.evidence_ids if outcome else [],
        }
        if enrichment.en is not None:
            card["i18n"] = {"en": enrichment.en.model_dump(mode="json")}
        if enrichment.verification_status.value == "failed":
            card["access_text"] = "入口未核查通过"
    else:
        card = {
            "name": entry.product.name,
            "category": category,
            "category_label": label,
            "one_liner": (entry.raw_item.description or entry.product.name)[:40],
            "why_interesting": (entry.raw_item.description or "等待补充证据")[:160],
            "user_story": "",
            "mode": "setup",
            "steps": [],
            "access_text": "收费未确认",
            "time_promise": None,
            "mobile_note": "手机体验未确认",
            "mobile_note_key": "mobile.unknown",
            "evidence_status": "unverified",
            "checked_at": utcnow().isoformat(),
            "source_links": sources,
            "evidence_ids": [],
        }
    if dims is not None:
        card["dims"] = dims.model_dump()
    return card


def _passes_gates(
    session: Session,
    entry: ResolvedItem,
    classification: ClassificationRow | None,
    dims: InterestDims | None,
    outcome: EnrichOutcome | None,
    settings: Settings,
    cutoff: datetime,
) -> tuple[bool, list[str]]:
    """入选判定（宽进策略，quality_gates.mode=soft 为默认）。

    soft：只有“明确排除”（规则判定不相关、spam 超线）与运营性去重
    （stale 事件、未决实体关系、30 天重复刊登）硬拦截；分类未知、置信度、
    兴趣、富化、可达性等质量问题降级为质量标记（quality_flags），
    不阻塞入选，供后续分类模块与人工复核使用。
    strict：保持 PRD 5.3 全部硬门槛（原行为）。
    """
    gates = settings.pipeline.quality_gates
    strict = (getattr(gates, "mode", "soft") or "soft").lower() == "strict"
    blocked: list[str] = []   # 硬拦截：明确排除 + 运营性去重
    flags: list[str] = []     # 质量标记：soft 下不拦截

    def mark(message: str) -> None:
        (blocked if strict else flags).append(message)

    product_keys = set(settings.taxonomy.product_category_keys)

    if classification is None:
        mark("无分类结果")
    else:
        if classification.relevant is False:
            blocked.append("明确不相关（规则排除）")
        elif classification.relevant is not True:
            mark("相关性未确认")
        if float(classification.confidence) < gates.min_confidence:
            mark(f"分类置信度 <{gates.min_confidence}")
        if float(classification.spam_score) >= gates.max_spam_score:
            blocked.append("spam 分数过高")
        if classification.category not in product_keys:
            if strict or classification.category is not None or classification.relevant is True:
                mark("分类不在产品分类内")

    interest = dims.llm_interest() if dims else None
    if interest is None:
        mark("兴趣维度未知")
    elif interest < gates.min_interest:
        mark(f"兴趣分 <{gates.min_interest}")

    if entry.exclusion_reason:
        blocked.append(entry.exclusion_reason)
    if entry.event.status != "active":
        blocked.append("事件已标记 stale")
    if outcome is None or outcome.enrichment is None:
        mark("无富化结果")
    else:
        if gates.require_reachable and outcome.page is not None and not outcome.page.reachable:
            mark("入口不可达")
        if outcome.page is None and not entry.raw_item.description:
            mark("无入口核查记录")
    for rel in repo_products.list_pending_relations(session, status="open"):
        if entry.product.id in (rel.product_id_a, rel.product_id_b):
            blocked.append("存在未决实体关系")
            break
    if was_published_recently(
        session,
        entry.product.id,
        cutoff,
        settings.pipeline.window.republish_cooldown_days,
    ) and not has_update_event(session, entry.product.id, entry.event.id):
        blocked.append("30 天内已刊登且无可验证重要更新")
    return (not blocked), blocked + flags
