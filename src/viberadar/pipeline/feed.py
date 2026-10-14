"""Product-unique rolling feed. Refresh reservation is committed before network I/O."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from viberadar.contracts import InterestDims, new_id, utcnow
from viberadar.ingest import run_all_sources
from viberadar.llm import make_gateway
from viberadar.llm.gateway import BudgetExceeded
from viberadar.store import repo_ingest, repo_ops, repo_pipeline, repo_products
from viberadar.store.models import (
    CandidateRow, ClassificationRow, EnrichmentRow, FeedItemRow, JobLockRow, LaunchEventRow,
    ProductRow, ProductSourceRow, RawItemRow, RefreshRunRow,
)
from .classify import classify_items
from .entities import ResolvedItem, resolve_entities
from .enrich import enrich_product
from .normalize import extract_identity
from .runner import _assess_interest, _build_source_maps, _card_content, _final_score, _store_enrichment, _passes_gates

LOCK = "feed:refresh"
COOLDOWN = timedelta(minutes=3)


def run_view(row):
    return {k: (getattr(row, k).isoformat() if getattr(row, k) is not None and k in
                {"since", "until", "started_at", "finished_at"} else getattr(row, k))
            for k in ("id", "trigger", "status", "since", "until", "new_raw", "new_items",
                      "llm_calls", "budget_exhausted", "delivery", "error", "started_at", "finished_at")}


def reserve_refresh(factory, trigger="button", now=None):
    now = now or utcnow()
    with factory() as s:
        last = s.scalar(select(RefreshRunRow).order_by(RefreshRunRow.started_at.desc()).limit(1))
        if repo_ops.has_lock(s, LOCK, now):
            return {"status": "running", "run_id": last.id if last else None}
        if last and last.started_at + COOLDOWN > now:
            return {"status": "cooldown", "next_at": (last.started_at + COOLDOWN).isoformat()}
        # A crashed worker's lease can expire; its audit entry must cease to say running.
        if last and last.status == "running":
            last.status, last.error, last.finished_at = "failed", "Refresh lease expired", now
        prior = s.scalar(select(RefreshRunRow).where(
            RefreshRunRow.status.in_(["no_changes", "analyzed"])
        ).order_by(RefreshRunRow.until.desc()).limit(1))
        since = max(prior.until - timedelta(hours=2), now - timedelta(hours=24)) if prior else now - timedelta(hours=24)
        row = RefreshRunRow(id=new_id("rfr"), trigger=trigger, since=since, until=now, started_at=now)
        repo_ops.acquire_lock(s, LOCK, row.id, lease_seconds=600, now=now)
        s.add(row)
        s.commit()
        return {"status": "running", "run_id": row.id, "created": True}


class CountedGateway:
    def __init__(self, gateway, session, run_id):
        self.gateway, self.session, self.run_id = gateway, session, run_id
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def chat_json(self, **kwargs):
        # Release SQLite's writer transaction before the Gateway opens its own ledger session.
        kwargs["run_id"] = self.run_id
        self.session.commit()
        self.gateway.check_budget()
        result = self.gateway.chat_json(**kwargs)
        if result.error and "上限" in result.error:
            raise BudgetExceeded(result.error)
        if not result.cached and not (result.error and "上限" in result.error):
            self.calls += result.request_count or (1 if self.gateway.provider == "mock" else 0)
        return result


def _entry(s, row):
    raw = s.get(RawItemRow, row.raw_item_id)
    entry = ResolvedItem(raw, s.get(ProductRow, row.product_id), s.get(LaunchEventRow, row.event_id),
                        extract_identity(raw.target_url or raw.source_url, raw.title, raw.metadata_json or {}, raw.source_key), False, False)
    entry.source_keys = repo_products.distinct_source_keys(s, row.product_id)
    return entry


def refresh_metrics(s, settings, run, transport=None):
    """Revisit older active launches, which disappear from the incremental discovery window."""
    import os
    import re
    import httpx
    from viberadar.contracts import MetricSnapshot
    from .normalize import github_owner_repo
    pairs = s.execute(select(RawItemRow, FeedItemRow).join(FeedItemRow, FeedItemRow.raw_item_id == RawItemRow.id)
        .where(FeedItemRow.state == "active", FeedItemRow.event_at >= run.until-timedelta(hours=72),
               RawItemRow.source_key.in_(["hn", "github"]))).all()
    def last_seen(pair):
        snapshots = repo_ingest.latest_metrics(s, pair[0].id, 1)
        return snapshots[0].captured_at if snapshots else pair[1].first_seen_at
    pairs.sort(key=last_seen)
    # Fair rotation and a bounded request count, including unauthenticated GitHub installations.
    for raw, _ in pairs[:12]:
        definition = settings.sources.sources.get(raw.source_key)
        if not definition or not definition.enabled:
            continue
        headers = {}
        if raw.source_key == "hn":
            if not raw.external_id.isdigit():
                continue
            url = "https://hn.algolia.com/api/v1/items/" + raw.external_id
        else:
            repo = github_owner_repo(raw.target_url or raw.source_url)
            if not repo or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in repo[:2]):
                continue
            url = f"https://api.github.com/repos/{repo[0]}/{repo[1]}"
            if os.environ.get("GITHUB_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        _renew(s, run)
        try:
            with httpx.Client(timeout=10, transport=transport, headers=headers, trust_env=False) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
            metrics = ({"points": data.get("points"), "num_comments": len(data.get("children") or [])}
                       if raw.source_key == "hn" else
                       {"stars": data.get("stargazers_count"), "forks": data.get("forks_count")})
            if any(isinstance(v, (int, float)) for v in metrics.values()):
                repo_ingest.record_metric_snapshot(s, MetricSnapshot(raw_item_id=raw.id, captured_at=utcnow(), metrics=metrics))
        except (httpx.HTTPError, ValueError, TypeError):
            pass  # Missing metrics never erase the last good snapshot or break discovery.
        _renew(s, run)


def _budget(s, settings):
    b = settings.pricing.budget
    return (repo_ops.daily_cost(s) >= Decimal(str(b.get("daily_hard_cap_usd", 2))) or
            repo_ops.monthly_cost(s) >= Decimal(str(b.get("monthly_hard_cap_usd", 30))))


def _renew(s, run):
    lock = s.get(JobLockRow, LOCK)
    if not lock or lock.owner != run.id or lock.lease_expires_at <= utcnow():
        raise RuntimeError("Refresh lease lost")
    lock.lease_expires_at = utcnow() + timedelta(minutes=10)
    s.commit()


def execute_refresh(factory, settings, run_id, *, no_llm=False, skip_ingest=False,
                    llm=None, transports=None, manual_entries=None):
    counted = None
    with factory() as s:
        run = s.get(RefreshRunRow, run_id)
        if run is None or run.status != "running":
            return
        try:
            if not skip_ingest:
                reports = run_all_sources(s, since=run.since, until=run.until, settings=settings,
                                          transports=transports, manual_entries=manual_entries,
                                          release_transactions=True, on_source=lambda: _renew(s, run))
                run.new_raw = sum(r.created for r in reports)
                meaningful = [r for r in reports if r.source_key != "manual" or manual_entries]
                if meaningful and all(r.status.value == "failed" for r in meaningful):
                    raise RuntimeError("All sources failed")
            raws = list(s.scalars(select(RawItemRow).where(
                RawItemRow.first_fetched_at >= run.until - timedelta(days=7))))
            linked = set(s.scalars(select(ProductSourceRow.raw_item_id)))
            # Resolve only new messages; pending products keep their original source.
            resolve_entities(s, [r for r in raws if r.id not in linked], settings, run.since, run.until)
            # Include products previously normalized by the daily pipeline.
            known = set(s.scalars(select(FeedItemRow.product_id)))
            from viberadar.store.models import EventSourceRow
            pairs = s.execute(select(ProductSourceRow, LaunchEventRow).join(
                EventSourceRow, EventSourceRow.raw_item_id == ProductSourceRow.raw_item_id
            ).join(
                LaunchEventRow, LaunchEventRow.id == EventSourceRow.launch_event_id
            ).where(ProductSourceRow.raw_item_id.in_([r.id for r in raws]))).all() if raws else []
            for ps, event in pairs:
                if ps.product_id in known:
                    continue
                raw = s.get(RawItemRow, ps.raw_item_id)
                # The refresh cutoff is captured before fetching. An undated discovery's
                # fetched_at may be a few milliseconds later; it is still part of this run.
                event_at = event.event_at or min(event.first_seen_at, run.until)
                row = FeedItemRow(id=new_id("fed"), product_id=ps.product_id, event_id=event.id,
                                  raw_item_id=raw.id, first_seen_at=raw.first_fetched_at,
                                  event_at=event_at,
                                  state="hidden" if event.status != "active" else "pending")
                s.add(row)
                known.add(ps.product_id)
            _renew(s, run)
            pending = list(s.scalars(select(FeedItemRow).where(FeedItemRow.state == "pending", FeedItemRow.event_at <= run.until)
                                    .order_by(FeedItemRow.first_seen_at, FeedItemRow.id)
                                    .limit(settings.pipeline.limits.deep_enrich_limit)))
            run.budget_exhausted = _budget(s, settings)
            s.commit()
            if pending and not no_llm and not run.budget_exhausted:
                gateway = llm or make_gateway(factory, settings)
                if gateway and gateway.provider not in ("mock", "none"):
                    counted = CountedGateway(gateway, s, run_id)
                elif llm is not None:  # explicit deterministic test gateway
                    counted = CountedGateway(llm, s, run_id)
                for idx, row in enumerate(pending):
                    if _budget(s, settings):
                        run.budget_exhausted = True
                        break
                    entry = _entry(s, row)
                    existing = s.scalar(select(EnrichmentRow).where(EnrichmentRow.product_id == row.product_id)
                                        .order_by(EnrichmentRow.created_at.desc()).limit(1))
                    cached = s.scalar(select(CandidateRow).where(CandidateRow.product_id == row.product_id)
                                      .order_by(CandidateRow.updated_at.desc()).limit(1))
                    s.commit()
                    if existing:
                        # Never refetch or call the model for an already enriched product.
                        classification = s.scalar(select(ClassificationRow).where(
                            ClassificationRow.product_id == row.product_id,
                            ClassificationRow.raw_item_id == row.raw_item_id
                        ).order_by(ClassificationRow.updated_at.desc()).limit(1))
                        row.spam = float(classification.spam_score) if classification else 0
                        pending_relation = any(row.product_id in (r.product_id_a, r.product_id_b)
                            for r in repo_products.list_pending_relations(s, status="open"))
                        if (entry.product.status != "active" or entry.event.status != "active" or
                            row.event_at < run.until-timedelta(days=7) or pending_relation or
                            (classification and classification.relevant is False) or
                            row.spam >= settings.pipeline.quality_gates.max_spam_score):
                            row.state, row.analyzed_at = "hidden", run.until
                            _renew(s, run)
                            continue
                        if cached:
                            row.content_json = dict(cached.content_json)
                        else:
                            guide = existing.try_guide_json
                            row.content_json = {**guide, "name": entry.product.name, "category": entry.product.category,
                                "access_json": existing.access_json, "evidence_status": existing.verification,
                                "checked_at": existing.created_at.isoformat(),
                                "source_links": [{"url": entry.raw_item.source_url, "label": "来源"}],
                                "evidence_ids": [e.id for e in repo_products.evidence_for_product(s, row.product_id)]}
                            if guide.get("en"):
                                row.content_json["i18n"] = {"en": guide["en"]}
                        row.category = row.content_json.get("category")
                        dims = InterestDims(**row.content_json["dims"]) if row.content_json.get("dims") else None
                        row.interest = dims.llm_interest() if dims else None
                        if settings.pipeline.quality_gates.mode == "strict":
                            gates = settings.pipeline.quality_gates
                            if (not classification or classification.relevant is not True or
                                float(classification.confidence) < gates.min_confidence or
                                row.category not in settings.taxonomy.product_category_keys or
                                row.interest is None or row.interest < gates.min_interest or
                                row.content_json.get("evidence_status") == "failed"):
                                row.state, row.analyzed_at = "hidden", run.until
                                _renew(s, run)
                                continue
                    else:
                        classification = repo_pipeline.get_classification(s, run.until.astimezone(settings.pipeline.tz).date(), row.product_id)
                        if classification is None:
                            classify_items(s, [entry], run.until.astimezone(settings.pipeline.tz).date(), run.id,
                                           settings=settings, llm=counted)
                            classification = repo_pipeline.get_classification(s, run.until.astimezone(settings.pipeline.tz).date(), row.product_id)
                        row.category = classification.category if classification else None
                        row.spam = float(classification.spam_score) if classification else 0
                        if classification and (classification.relevant is False or row.spam >= settings.pipeline.quality_gates.max_spam_score):
                            row.state, row.analyzed_at = "hidden", run.until
                            _renew(s, run)
                            continue
                        s.commit()
                        outcome = enrich_product(s, entry.product, entry.raw_item, settings=settings,
                            llm=counted, use_strong_model=idx < settings.pipeline.limits.strong_model_limit,
                            transport=(transports or {}).get("__enrich__"))
                        # Assess playability after evidence/guide collection. A title alone
                        # cannot support the rubric and a real model correctly returns nulls.
                        s.commit()
                        dims = InterestDims(**row.content_json["dims"]) if row.content_json.get("dims") else _assess_interest(s, entry, outcome, settings, counted, None)
                        if dims:
                            row.content_json = {**row.content_json, "dims": dims.model_dump()}
                        s.commit()
                        # Budget short-circuit leaves unfinished analysis pending.
                        if _budget(s, settings) and (outcome.enrichment is None or dims is None):
                            run.budget_exhausted = True
                            break
                        _store_enrichment(s, entry.product, entry.event, outcome)
                        row.content_json = _card_content(s, entry, outcome, settings, dims, classification)
                        row.interest = dims.llm_interest() if dims else None
                        allowed, flags = _passes_gates(s, entry, classification, dims, outcome, settings, run.until, ignore_republish=True)
                        row.content_json = {**row.content_json, "quality_flags": flags}
                        if not allowed:
                            row.state, row.analyzed_at = "hidden", run.until
                            _renew(s, run)
                            continue
                    # Reuse the controlled outbound redirect without coupling Feed to editions.
                    from viberadar.store import repo_editions
                    if entry.product.canonical_url and not row.content_json.get("links"):
                        link = repo_editions.create_outbound_link(s, None, entry.product.canonical_url)
                        row.content_json = {**row.content_json, "links": {"project": link.link_id}}
                    row.state = "active"
                    row.analyzed_at = run.until
                    run.new_items += 1
                    _renew(s, run)
            refresh_metrics(s, settings, run, (transports or {}).get("__metrics__"))
            rerank(s, settings, run.until)
            run.llm_calls = counted.calls if counted else 0
            run.status = "analyzed" if run.new_items or run.llm_calls else "no_changes"
            run.finished_at = utcnow()
            repo_ops.record_audit(s, run.trigger, "feed_refresh", run.id, run_view(run))
            repo_ops.release_lock(s, LOCK, run.id)
            s.commit()
        except BudgetExceeded:
            s.rollback()
            run = s.get(RefreshRunRow, run_id)
            refresh_metrics(s, settings, run, (transports or {}).get("__metrics__"))
            rerank(s, settings, run.until)
            run.status = "analyzed" if run.new_items or (counted and counted.calls) else "no_changes"
            run.budget_exhausted, run.finished_at = True, utcnow()
            run.llm_calls = counted.calls if counted else 0
            repo_ops.release_lock(s, LOCK, run.id)
            s.commit()
        except Exception as exc:
            s.rollback()
            run = s.get(RefreshRunRow, run_id)
            run.status, run.error, run.finished_at = "failed", str(exc)[:300], utcnow()
            run.llm_calls = counted.calls if counted else 0
            repo_ops.release_lock(s, LOCK, run.id)
            s.commit()


def refresh_feed(factory, settings, trigger="cli", now=None, **kwargs):
    reservation = reserve_refresh(factory, trigger, now)
    if reservation.pop("created", False):
        execute_refresh(factory, settings, reservation["run_id"], **kwargs)
    return reservation


def rerank(s, settings, now):
    rows = list(s.scalars(select(FeedItemRow).where(FeedItemRow.state == "active")))
    entries = [_entry(s, r) for r in rows]
    maps = _build_source_maps(s, entries, settings, now, now - timedelta(days=7))
    for row, entry in zip(rows, entries):
        if entry.product.status != "active":
            row.state = "hidden"
            continue
        if row.event_at < now - timedelta(days=7):
            row.state = "archived"
            continue
        dims = InterestDims(**row.content_json["dims"]) if row.content_json.get("dims") else None
        from types import SimpleNamespace
        classification = SimpleNamespace(category=row.category, spam_score=float(row.spam))
        score = _final_score(entry, settings, dims, now, maps, classification)
        snapshot = score.model_dump(mode="json")
        snapshot["source_metrics"] = {key: {"heat": value["heat"][row.product_id].model_dump(),
            "velocity": value["velocity"][row.product_id].model_dump()} for key, value in maps.items()
            if row.product_id in value["heat"] and row.product_id in value["velocity"]}
        row.final_score, row.signals, row.rank_updated_at = score.final_score, snapshot, now
    sticky = [r for r in rows if r.state == "active" and r.must_read_until and r.must_read_until > now]
    eligible = sorted([r for r in rows if r.state == "active" and r not in sticky and
        float(r.interest or 0) >= settings.pipeline.quality_gates.min_interest and
        r.content_json.get("evidence_ids") and float(r.final_score) >= .65], key=lambda r: (-float(r.final_score), r.id))
    for row in eligible[:max(0, 3 - len(sticky))]:
        row.must_read_until = now + timedelta(hours=12)
    s.flush()


def feed_view(s, settings, locale="zh", cursor=None, section=None, now=None):
    from viberadar.i18n import localize_card_content
    from viberadar.trial.recipes import recipe_for_slug, recipe_label
    now = now or utcnow()
    rows = list(s.scalars(select(FeedItemRow).join(ProductRow).where(
        FeedItemRow.state == "active", FeedItemRow.event_at >= now - timedelta(days=7), FeedItemRow.event_at <= now,
        ProductRow.status == "active").order_by(FeedItemRow.event_at.desc(), FeedItemRow.id.desc())))
    must = sorted([r for r in rows if r.must_read_until and r.must_read_until > now], key=lambda r: (-float(r.final_score), r.id))[:3]
    def hot(r):
        source_metrics = (r.signals or {}).get("source_metrics")
        if source_metrics:
            return r.event_at >= now-timedelta(hours=72) and any(
                not x["heat"].get("missing", True) and not x["velocity"].get("missing", True) and
                x["heat"].get("value", 0) >= .6 and x["velocity"].get("value", 0) >= .8
                for x in source_metrics.values())
        sig = (r.signals or {}).get("signals", {})
        h, v = sig.get("source_signal", {}), sig.get("velocity", {})
        return (r.event_at >= now - timedelta(hours=72) and not h.get("missing", True) and
                not v.get("missing", True) and h.get("value", 0) >= .6 and v.get("value", 0) >= .8)
    trending = sorted([r for r in rows if hot(r)], key=lambda r: -(r.signals["signals"]["velocity"]["value"]))[:5]
    latest = [r for r in rows if r.event_at >= now - timedelta(hours=48)]
    def card(r):
        p = s.get(ProductRow, r.product_id)
        c = dict(r.content_json)
        copy = localize_card_content(c, locale, settings.taxonomy.labels(locale).get(r.category, ""))
        recipe = recipe_for_slug(s, p.slug)
        snaps = repo_ingest.latest_metrics(s, r.raw_item_id, 6)
        metric = next(((k, snaps[0].metrics[k]) for k in ("points", "stars", "votes")
                       if snaps and isinstance(snaps[0].metrics.get(k), (int, float))), None)
        velocity = None
        if metric and len(snaps) > 1:
            from viberadar.rank import velocity_from_snapshots
            velocity = velocity_from_snapshots([(x.captured_at, float(x.metrics[metric[0]])) for x in snaps
                                                if isinstance(x.metrics.get(metric[0]), (int, float))])
            if velocity is not None:
                from math import expm1
                velocity = expm1(velocity)  # The rank helper returns log1p(rate), the badge displays units/hour.
        return {**c, **copy, "id": r.id, "edition_id": "", "product_id": p.id, "slug": p.slug,
                "edition_date": "", "highlight": r in must, "is_feed": True,
                "is_new": r.first_seen_at >= now - timedelta(hours=24), "event_at": r.event_at.isoformat(),
                "heat_label": (f"{'★' if metric[0] == 'stars' else 'HN' if s.get(RawItemRow,r.raw_item_id).source_key == 'hn' else '▲'} {metric[1]:,}" +
                    (f" +{velocity:.0f}/h" if velocity is not None else "")) if metric else "",
                "trial": recipe_label(recipe, locale), "links": c.get("links", {})}
    seen = set()
    sections = []
    for key, group in (("must_read", must), ("trending", trending), ("latest", latest)):
        unique = [r for r in group if r.id not in seen]
        seen.update(r.id for r in unique)
        sections.append({"key": key, "items": [card(r) for r in unique]})
    remaining = [r for r in rows if r.id not in seen]
    if section:
        if section not in {"must_read", "trending", "latest", "all"}:
            raise ValueError("Unknown feed section")
        remaining = {"must_read": must, "trending": trending, "latest": latest, "all": rows}[section]
        sections = []
    start = 0
    if cursor:
        ids = [r.id for r in remaining]
        if cursor not in ids:
            raise ValueError("Invalid or expired cursor")
        start = ids.index(cursor) + 1
    page = remaining[start:start + 20]
    sections.append({"key": section or "all", "items": [card(r) for r in page]})
    last = s.scalar(select(RefreshRunRow).order_by(RefreshRunRow.started_at.desc()).limit(1))
    return {"sections": sections, "next_cursor": page[-1].id if page and start + 20 < len(remaining) else None,
            "total": len(rows), "pending": s.scalar(select(func.count()).select_from(FeedItemRow).where(FeedItemRow.state == "pending")),
            "last_run": run_view(last) if last else None}
