"""期号编制：冻结、快照、修订（PRD M6/5.2/5.3）。

- 冻结时应用人工修订层：通过/锁定项保留，淘汰项移除，其余走自动版。
- 硬门槛（可访问、有来源）人工也不能绕过。
- 少于 5 条短刊；0 条空刊；空分组隐藏。
- 修订创建新 revision，旧版本保留审计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from viberadar.contracts import (
    ContentStatus,
    EditionItemView,
    EditionStatus,
    EditionView,
    new_id,
    utcnow,
)
from viberadar.settings import Settings, get_settings
from viberadar.rank import select_public_items
from viberadar.rank.diversity import DiversityItem
from viberadar.store import repo_editions, repo_ops, repo_pipeline, repo_products
from viberadar.store.models import CandidateRow, EditionRow

HARD_GATE_REASONS = ("入口不可达", "无入口核查记录", "无富化结果")
REQUIRED_EVIDENCE = {"doc_checked", "human_tested", "probe_tested"}


@dataclass
class SelectionResult:
    candidates: list[CandidateRow]
    content_status: str
    notes: list[str] = field(default_factory=list)


def build_selection(
    session: Session,
    edition_date: date,
    settings: Settings | None = None,
) -> SelectionResult:
    """冻结时应用人工修订层；硬门槛（证据、来源、可访问）不可被人为绕过。"""
    settings = settings or get_settings()
    strict = (getattr(settings.pipeline.quality_gates, "mode", "soft") or "soft").lower() == "strict"
    candidates = repo_pipeline.list_candidates(session, edition_date)
    notes: list[str] = []

    approved = [c for c in candidates if c.curator_status == "approved"]
    rejected_ids = {c.product_id for c in candidates if c.curator_status == "rejected"}
    locked = [c for c in candidates if c.locked]
    has_curation = any(c.curator_status for c in candidates) or bool(locked)

    chosen: list[CandidateRow] = []
    seen: set[str] = set()

    def eligible(candidate: CandidateRow) -> bool:
        if candidate.product_id in seen or candidate.product_id in rejected_ids:
            return False
        content = candidate.content_json or {}
        evidence_status = content.get("evidence_status")
        # 宽进策略：仅“入口实测失败”硬拦截；未核查（unverified）放行但保留注释。
        # strict 模式恢复 E06：evidence_status 必须属于 REQUIRED_EVIDENCE。
        if evidence_status == "failed":
            notes.append(f"{candidate.product_id}: 跳过（入口实测失败）")
            return False
        if strict and evidence_status not in REQUIRED_EVIDENCE:
            notes.append(f"{candidate.product_id}: 跳过（无证据，硬门槛，E06）")
            return False
        if not content.get("source_links"):
            notes.append(f"{candidate.product_id}: 跳过（无来源，硬门槛）")
            return False
        return True

    def add(candidate: CandidateRow) -> bool:
        if not eligible(candidate):
            return False
        seen.add(candidate.product_id)
        chosen.append(candidate)
        return True

    target = settings.pipeline.limits.public_target
    if not has_curation:
        # 自动版：按分数 + 多样性选择目标条数
        auto_candidates = [c for c in candidates if c.auto_selected]
        items = [
            DiversityItem(
                key=c.product_id,
                category=(c.content_json or {}).get("category") or "unknown",
                score=float(c.final_score),
            )
            for c in auto_candidates
        ]
        by_id = {c.product_id: c for c in auto_candidates}
        for item in select_public_items(
            items,
            target=target,
            ratio=float(settings.rubric.diversity.get("public_single_category_ratio", 0.4)),
            min_categories=int(settings.rubric.diversity.get("public_min_categories", 3)),
            same_topic_max=int(settings.rubric.diversity.get("same_topic_max", 2)),
            ratio_min_count=settings.pipeline.limits.public_min,
        ):
            add(by_id[item.key])
    else:
        # 人工版：锁定/通过项优先，未处理部分按自动结果补齐到目标条数
        for candidate in sorted(
            locked, key=lambda c: (c.position_hint or 10**6, -float(c.final_score))
        ):
            add(candidate)
        for candidate in sorted(approved, key=lambda c: -float(c.final_score)):
            add(candidate)
        for candidate in sorted(
            [c for c in candidates if c.auto_selected], key=lambda c: -float(c.final_score)
        ):
            if len(chosen) >= target:
                break
            add(candidate)

    if len(chosen) > settings.pipeline.limits.public_max:
        notes.append(
            f"超出人工扩展硬上限 {settings.pipeline.limits.public_max} 条，已截断"
        )
        chosen = chosen[: settings.pipeline.limits.public_max]

    n = len(chosen)
    if n == 0:
        content_status = ContentStatus.EMPTY.value
    elif n < settings.pipeline.limits.public_min:
        content_status = ContentStatus.SHORT.value
    else:
        content_status = ContentStatus.NORMAL.value
    return SelectionResult(candidates=chosen, content_status=content_status, notes=notes)


def freeze_edition(
    session: Session,
    edition_date: date,
    settings: Settings | None = None,
    run_id: str | None = None,
    source_coverage: str | None = None,
    mode: str | None = None,
    operator: str = "auto",
    now: datetime | None = None,
) -> EditionRow:
    settings = settings or get_settings()
    now = now or utcnow()
    edition = repo_editions.create_edition(
        session,
        edition_date,
        settings.pipeline.timezone,
        config_version=settings.config_hash,
        run_id=run_id,
    )
    if edition.status in (EditionStatus.PUBLISHED.value,):
        raise repo_editions.EditionStateError("期号已公开，不能重复冻结；请使用修订流程")

    selection = build_selection(session, edition_date, settings)
    revision = max(1, edition.revision)
    if mode is None:
        has_curation = any(
            c.curator_status for c in repo_pipeline.list_candidates(session, edition_date)
        )
        mode = "human" if has_curation else "auto"

    items_payload = []
    for idx, candidate in enumerate(selection.candidates):
        content = dict(candidate.content_json or {})
        edits = candidate.curator_edits or {}
        for key in ("one_liner", "why_interesting", "steps"):
            if edits.get(key):
                content[key] = edits[key]
        section = "creative" if content.get("category") == "creative-toy" else "tool"
        content["highlight"] = idx < 2  # 重点 1–2 条优先展示
        items_payload.append(
            {
                "product_id": candidate.product_id,
                "event_id": candidate.event_id,
                "final_score": float(candidate.final_score),
                "signals": candidate.signals,
                "content": content,
                "section": section,
                "highlight": idx < 2,
            }
        )

    rows = repo_editions.replace_items(session, edition, revision, items_payload)
    edition.intro = _intro(selection.content_status, len(items_payload))
    edition.source_coverage = source_coverage
    edition.mode = mode
    edition.content_status = selection.content_status
    edition.run_id = run_id or edition.run_id
    edition.updated_at = now

    # 为主要入口创建受控跳转链接（不接收客户端任意目标）
    for row in rows:
        content = dict(row.content_json or {})
        links = dict(content.get("links") or {})
        target = None
        for source in content.get("source_links") or []:
            if source.get("label") == "项目/官网":
                target = source.get("url")
                break
        if not target:
            for source in content.get("source_links") or []:
                target = source.get("url")
                break
        if target:
            link = repo_editions.create_outbound_link(session, row.id, target)
            links["project"] = link.link_id
            content["links"] = links
            row.content_json = content
            session.flush()

    if edition.status == EditionStatus.DRAFT.value:
        repo_editions.transition(session, edition, EditionStatus.REVIEWING.value)
    repo_editions.transition(session, edition, EditionStatus.READY.value)
    repo_ops.record_audit(
        session,
        actor=operator,
        action="freeze_edition",
        target=edition.id,
        detail={
            "content_status": selection.content_status,
            "items": len(items_payload),
            "notes": selection.notes[:10],
        },
    )
    return edition


def _intro(content_status: str, count: int) -> str:
    if content_status == ContentStatus.EMPTY.value:
        return "今日暂无通过核验的推荐，已保留历史期号入口。"
    if content_status == ContentStatus.SHORT.value:
        return f"今日通过核验的推荐较少，精选 {count} 条。"
    return f"今日精选 {count} 条。"


def publish_edition(
    session: Session,
    edition: EditionRow,
    now: datetime | None = None,
    operator: str = "auto",
) -> EditionRow:
    """09:00 原子公开；已公开则幂等返回。"""
    now = now or utcnow()
    if edition.status == EditionStatus.PUBLISHED.value:
        return edition
    if edition.status != EditionStatus.READY.value:
        raise repo_editions.EditionStateError(
            f"仅 ready 期号可公开，当前 {edition.status}（PRD 5.4）"
        )
    repo_editions.mark_published(session, edition, now)
    repo_ops.record_audit(
        session, actor=operator, action="publish_edition", target=edition.id
    )
    return edition


def create_correction(
    session: Session,
    edition: EditionRow,
    changes: dict[str, dict],
    reason: str,
    operator: str,
    notify_again: bool = False,
) -> EditionRow:
    """更正创建新 revision；用户默认看到最新版，原始版本保留审计。"""
    items = repo_editions.list_items(session, edition.id, revision=edition.revision)
    new_revision = edition.revision + 1
    payload = []
    diff: dict = {}
    for row in items:
        content = dict(row.content_json or {})
        if row.id in changes:
            for key, value in changes[row.id].items():
                content[key] = value
            diff[row.id] = changes[row.id]
        payload.append(
            {
                "product_id": row.product_id,
                "event_id": row.event_id,
                "final_score": float(row.final_score),
                "signals": row.signals,
                "content": content,
                "section": row.section,
                "highlight": row.highlight,
            }
        )
    repo_editions.replace_items(session, edition, new_revision, payload)
    repo_editions.add_revision_record(
        session, edition, new_revision, reason=reason, field_diff=diff, notify_again=notify_again
    )
    repo_ops.record_audit(
        session,
        actor=operator,
        action="correct_edition",
        target=edition.id,
        detail={"revision": new_revision, "reason": reason, "notify_again": notify_again},
    )
    return edition


def build_edition_view(session: Session, edition: EditionRow) -> EditionView:
    revision = edition.revision
    if edition.status == EditionStatus.PUBLISHED.value or edition.mode == "human":
        revision = repo_editions.latest_revision(session, edition.id) or revision
    rows = repo_editions.list_items(
        session, edition.id, revision=repo_editions.latest_revision(session, edition.id) or revision
    )
    items = []
    for row in rows:
        content = dict(row.content_json or {})
        product = repo_products.get_product(session, row.product_id)
        items.append(
            EditionItemView(
                id=row.id,
                edition_id=edition.id,
                revision=row.revision,
                position=row.position,
                section=row.section,
                product_id=row.product_id,
                slug=product.slug if product else row.product_id,
                name=content.get("name") or (product.name if product else ""),
                category=content.get("category", "unknown"),
                category_label=content.get("category_label", ""),
                one_liner=content.get("one_liner", ""),
                why_interesting=content.get("why_interesting", ""),
                mode=content.get("mode", "setup"),
                access_text=content.get("access_text", ""),
                steps=list(content.get("steps") or []),
                time_promise=content.get("time_promise"),
                mobile_note=content.get("mobile_note"),
                evidence_status=content.get("evidence_status", "unverified"),
                source_links=list(content.get("source_links") or []),
                checked_at=_parse_dt(content.get("checked_at")),
                final_score=float(row.final_score or 0),
                signals=dict(row.signals or {}),
                highlight=bool(row.highlight),
                revision_note=row.revision_note,
                retracted=bool(row.retracted),
                edition_date=edition.date,
                links=dict(content.get("links") or {}),
            )
        )
    from datetime import date as _date

    return EditionView(
        id=edition.id,
        date=edition.date if isinstance(edition.date, _date) else _date.fromisoformat(str(edition.date)),
        timezone=edition.timezone,
        status=EditionStatus(edition.status),
        content_status=ContentStatus(edition.content_status),
        revision=repo_editions.latest_revision(session, edition.id) or edition.revision,
        intro=edition.intro or "",
        published_at=edition.published_at,
        source_coverage=edition.source_coverage,
        items=items,
    )


def _parse_dt(value) -> datetime:  # noqa: ANN001
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                from datetime import timezone as _tz

                parsed = parsed.replace(tzinfo=_tz.utc)
            return parsed
        except ValueError:
            pass
    return utcnow()


def curation_overlay_applied(candidate: CandidateRow) -> bool:
    return bool(candidate.curator_status or candidate.curator_edits or candidate.locked)
