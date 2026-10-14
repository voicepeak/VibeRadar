from statistics import median
from sqlalchemy import select
from viberadar.contracts import new_id, utcnow
from viberadar.contracts.trial import TryRecipe
from viberadar.store.models import TryRecipeRow, TrialSessionRow, TrialEventRow, ProductRow


def register_recipe(s, recipe: TryRecipe):
    content = recipe.model_dump(mode="json")
    existing = s.scalar(select(TryRecipeRow).where(TryRecipeRow.slug == recipe.slug, TryRecipeRow.version == recipe.version))
    if existing:
        if TryRecipe(**existing.content_json).model_dump(mode="json") != content:
            raise ValueError("Recipe version is immutable; choose a new version")
        return existing
    row = TryRecipeRow(id=new_id("rcp"), slug=recipe.slug, version=recipe.version, content_json=content,
        verified_at=recipe.verified.at if recipe.verified else None, created_at=utcnow())
    s.add(row)
    s.flush()
    return row


def recipe_for_slug(s, slug):
    direct = s.scalar(select(TryRecipeRow).where(TryRecipeRow.slug == slug).order_by(TryRecipeRow.created_at.desc()).limit(1))
    if direct:
        return direct
    product = s.scalar(select(ProductRow).where(ProductRow.slug == slug))
    if not product or not product.canonical_key:
        return None
    # GitHub discovery slugs contain owner+repo. Curated recipes may use a short slug;
    # bind them by the same canonical identity as discovery, never by a fuzzy name.
    from viberadar.pipeline.normalize import extract_identity
    for row in s.scalars(select(TryRecipeRow).order_by(TryRecipeRow.created_at.desc())):
        recipe = TryRecipe(**row.content_json)
        url = recipe.repo or recipe.entry_url
        if url and extract_identity(url, "").canonical_key == product.canonical_key:
            return row
    return None


def recipe_label(row, locale="zh"):
    if not row:
        return None
    r = TryRecipe(**row.content_json)
    tier = r.tier.value if row.verified_at and r.license_checked and not row.needs_review else "guided"
    labels = {"zh": {"instant": "打开即玩", "container": "一键启动", "compose": "一键启动 · 多服务", "guided": "本地指引", "blocked": "看演示"},
              "en": {"instant": "Open & try", "container": "Launch locally", "compose": "Launch services", "guided": "Setup guide", "blocked": "Watch demo"}}
    return {"tier": tier, "label": labels[locale][tier], "version": r.version}


def recipe_stats(s, row):
    sessions = list(s.scalars(select(TrialSessionRow).where(TrialSessionRow.recipe_id == row.id)))
    times = [(x.ready_at - x.started_at).total_seconds() for x in sessions if x.ready_at]
    # Earlier cleanup versions set failures to stopped; the immutable event history preserves the outcome.
    failures = set(s.scalars(select(TrialEventRow.session_id).join(TrialSessionRow,
        TrialSessionRow.id == TrialEventRow.session_id).where(TrialSessionRow.recipe_id == row.id,
        TrialEventRow.kind.in_(["failed", "timeout"]))))
    terminal = [x for x in sessions if x.ready_at or x.status in ("failed", "timeout") or x.id in failures]
    return {"sessions": len(sessions), "success_rate": len(times) / len(terminal) if terminal else None,
            "median_ready_seconds": median(times) if times else None,
            "consecutive_failures": row.consecutive_failures, "needs_review": row.needs_review,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None}
