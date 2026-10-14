"""Measure real local trials in a separate data directory; does not verify shipped recipes.

Run with Docker Desktop started. No real model keys or paid model calls are used.
MarkItDown is tested with its base HTML converter, not all optional format extras.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import httpx

from viberadar.contracts import utcnow
from viberadar.contracts.trial import TryRecipe, Verification
from viberadar.settings import PROJECT_ROOT, load_settings
from viberadar.store import get_session_factory
from viberadar.store.models import TrialEventRow, TrialSessionRow
from viberadar.trial.recipes import register_recipe
from viberadar.trial.runtime import Docker, create_trial, execute_trial, stop_trial
from sqlalchemy import select


class TimedDocker(Docker):
    def __init__(self):
        self.commands = []

    def call(self, args, timeout=30):
        started = time.perf_counter()
        ok = False
        try:
            result = super().call(args, timeout)
            ok = True
            return result
        finally:
            # Never persist command args: they can contain per-session virtual keys.
            self.commands.append({"operation": args[0], "seconds": round(time.perf_counter()-started, 3), "ok": ok})


def recipe_for_benchmark(name, version, device):
    common = dict(slug="benchmark-"+name, version=version, tier="container", image="python:3.13-slim",
                  timeout_minutes=8, ttl_minutes=2,
                  verified=Verification(by="isolated benchmark only", at=utcnow(), device=device))
    if name == "python":
        return TryRecipe(**common, run=["python -m http.server 3000 --bind 0.0.0.0"],
                         license_checked="First-party smoke fixture")
    app = '''from io import BytesIO
from markitdown import MarkItDown
from http.server import BaseHTTPRequestHandler, HTTPServer
result = MarkItDown().convert_stream(BytesIO(b"<h1>Timing test</h1><p>Real HTML conversion.</p>"), file_extension=".html").text_content
assert "Timing test" in result and "Real HTML conversion" in result
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data=result.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)
HTTPServer(("0.0.0.0",3000), Handler).serve_forever()
'''
    encoded = base64.b64encode(app.encode()).decode()
    return TryRecipe(**common, license_checked="MarkItDown 0.1.8 MIT (PyPI / official repository)",
                     package_hosts=["pypi.org", "files.pythonhosted.org"],
                     install=["mkdir -p /workspace/.tmp",
                              "TMPDIR=/workspace/.tmp python -m pip install --disable-pip-version-check --no-cache-dir --index-url https://pypi.org/simple --target /workspace/deps markitdown==0.1.8",
                              "python -c \"import base64;open('/workspace/app.py','wb').write(base64.b64decode('"+encoded+"'))\""],
                     run=["PYTHONPATH=/workspace/deps python /workspace/app.py"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", choices=["python", "markitdown"], default="python")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--cold-image", action="store_true", help="Remove this benchmark image without force before run 1")
    parser.add_argument("--prepared-image", help="Content-addressed, locally prepared MarkItDown benchmark image")
    args = parser.parse_args()
    out = PROJECT_ROOT / ".local" / "runtime-benchmark"
    out.mkdir(parents=True, exist_ok=True)
    settings = load_settings(data_dir=out / "data")
    factory = get_session_factory(settings.db_path)
    availability = Docker().available()
    if not availability["available"]:
        raise SystemExit("Docker engine is unavailable")
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    recipe = recipe_for_benchmark(args.tool, "timing-"+stamp, availability["version"])
    if args.prepared_image:
        if args.tool != "markitdown" or args.cold_image:
            parser.error("Prepared image is for MarkItDown and cannot be combined with --cold-image")
        recipe.image = args.prepared_image
        recipe.slug = "benchmark-markitdown-prepared"
        recipe.install = []
        recipe.package_hosts = []
        recipe.run = ["PYTHONPATH=/opt/viberadar/deps python /opt/viberadar/app.py"]
    with factory() as session:
        register_recipe(session, recipe)
        session.commit()
    records = []
    output = out / (args.tool+("-prepared" if args.prepared_image else "")+"-"+stamp+".json")
    if args.cold_image:
        # No force: Docker refuses to remove an image referenced by another container.
        Docker().call(["image", "rm", recipe.image])
    for i in range(args.runs):
        engine = TimedDocker()
        print(json.dumps({"tool": args.tool, "run": i+1, "stage": "started"}), flush=True)
        started = time.perf_counter()
        session_id, token = create_trial(factory, settings, recipe.slug)
        record = {"tool": args.tool, "run": i+1, "session_id": session_id,
                  "image": recipe.image, "model_calls": 0,
                  "cache_condition": "prepared immutable image; new workspace" if args.prepared_image else
                                     "image removed" if i == 0 and args.cold_image else "image retained; new workspace, no dependency cache",
                  "scope": "first-party smoke" if args.tool == "python" else "MarkItDown 0.1.8 base HTML conversion"}
        try:
            execute_trial(factory, settings, session_id, token, engine)
            elapsed = time.perf_counter()-started
            with factory() as session:
                row = session.get(TrialSessionRow, session_id)
                record.update(status=row.status, ready_seconds=round(elapsed, 3) if row.status == "ready" else None,
                              attempt_seconds=round(elapsed, 3), error=row.error,
                              events=[{"kind": event.kind, "at": event.created_at.isoformat(), "message": event.message}
                                      for event in session.scalars(select(TrialEventRow).where(TrialEventRow.session_id == session_id)
                                                                  .order_by(TrialEventRow.created_at))])
                if row.status == "ready":
                    response = httpx.get(row.url, timeout=5, trust_env=False)
                    response.raise_for_status()
                    if args.tool == "markitdown":
                        assert "# Timing test" in response.text and "Real HTML conversion" in response.text
                    record["real_output_checked"] = True
            record["startup_commands"] = list(engine.commands)
        finally:
            cleanup_started = time.perf_counter()
            try:
                stop_trial(factory, settings, session_id, engine, remove=True)
                record["cleanup_seconds"] = round(time.perf_counter()-cleanup_started, 3)
            except Exception as exc:
                record["cleanup_error"] = str(exc)
            records.append(record)
            output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in record.items() if k not in ("events", "startup_commands")}, ensure_ascii=False), flush=True)
        if record.get("status") != "ready":
            print("Benchmark stopped after an actual startup failure; inspect " + str(output), flush=True)
            return 1
    print(str(output), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
