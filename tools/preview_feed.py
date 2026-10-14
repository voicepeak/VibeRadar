"""Isolated UI fixture. Never seeds or rewrites the user's data directory."""
from datetime import timedelta
from pathlib import Path
import sys

from viberadar.contracts import utcnow, new_id
from viberadar.settings import PROJECT_ROOT, load_settings
from viberadar.store import get_session_factory
from viberadar.store.models import FeedItemRow, ProductRow, LaunchEventRow, RawItemRow, Source, RefreshRunRow


def seed_preview():
    settings = load_settings(data_dir=PROJECT_ROOT/".local"/"qa-feed")
    factory = get_session_factory(settings.db_path)
    now = utcnow()
    # Source demo content from the existing acceptance fixture, explicitly synthetic.
    sys.path.insert(0, str(PROJECT_ROOT/"tools"))
    from seed_acceptance import SPECS, seed_normal, seed_empty
    with factory() as s:
        if not s.get(Source, "manual"):
            s.add(Source(key="manual", label="UI fixtures", enabled=True))
            s.flush()
        for i, spec in enumerate(SPECS):
            pid = "preview-"+str(i)
            if s.get(ProductRow, pid):
                continue
            p = ProductRow(id=pid, slug=spec["name"].lower().replace(" ", "-"), name=spec["name"],
                canonical_url="https://example.com/"+str(i), canonical_key="preview:"+str(i),
                first_seen_at=now-timedelta(hours=i*2), created_at=now, updated_at=now,
                category=spec["category"], status="active")
            s.add(p)
            s.flush()
            raw = RawItemRow(id="preview-raw-"+str(i), source_key="manual", external_id=str(i), title=p.name,
                source_url=p.canonical_url, first_fetched_at=now, last_fetched_at=now,
                published_at=now-timedelta(hours=i*2))
            event = LaunchEventRow(id="preview-event-"+str(i), product_id=p.id, kind="discovery", event_at=raw.published_at,
                first_seen_at=now, event_fingerprint=raw.id, created_at=now, status="active")
            s.add_all([raw,event])
            s.flush()
            zh, en = spec["zh"], spec["en"]
            content = {"name": p.name, "category": p.category, "category_label": spec["label"],
                "one_liner": zh["one_liner"], "why_interesting": zh["why"], "user_story": zh["story"],
                "steps": zh["steps"], "mode": spec["mode"], "access_text": spec["access"]["zh"],
                "checked_at": now.isoformat(), "evidence_status": "unverified", "evidence_ids": [],
                "source_links": [{"url": p.canonical_url, "label": "项目/官网"}], "mobile_note_key": "mobile.unknown",
                "i18n": {"en": {"one_liner": en["one_liner"], "why_interesting": en["why"], "user_story": en["story"], "steps": en["steps"]}}}
            s.add(FeedItemRow(id="fed_preview"+str(i), product_id=p.id, raw_item_id=raw.id, event_id=event.id,
                state="active", category=p.category, final_score=.9-i*.03, first_seen_at=raw.published_at,
                event_at=raw.published_at, analyzed_at=now, content_json=content,
                must_read_until=now+timedelta(hours=12) if i < 2 else None,
                signals={"signals": {"source_signal": {"value": .9, "missing": False}, "velocity": {"value": .9 if i==2 else .2, "missing": False}}}))
        if not s.get(RefreshRunRow, "preview-refresh"):
            s.add(RefreshRunRow(id="preview-refresh", trigger="cli", status="analyzed", since=now-timedelta(hours=24),
                               until=now, started_at=now, finished_at=now, new_items=len(SPECS)))
        today = now.astimezone(settings.pipeline.tz).date()
        seed_normal(s, today-timedelta(days=1))
        seed_empty(s, today-timedelta(days=2))
        s.commit()
    return settings


if __name__ == "__main__":
    settings = seed_preview()
    import uvicorn
    from viberadar.web.app import create_app
    print("Synthetic UI fixtures only: http://127.0.0.1:8011", flush=True)
    uvicorn.run(create_app(settings.db_path, settings), host="127.0.0.1", port=8011)
