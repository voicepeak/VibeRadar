"""Prepare a fixed MarkItDown test image once; never cache a user's mutable trial.

Wheels download in the existing constrained runtime. Image assembly uses an offline,
non-root build with a first-party Dockerfile. This is a benchmark, not a published recipe.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import time
from pathlib import Path

from benchmark_trial import recipe_for_benchmark, TimedDocker
from viberadar.contracts import utcnow
from viberadar.settings import PROJECT_ROOT, load_settings
from viberadar.store import get_session_factory
from viberadar.store.models import TrialSessionRow
from viberadar.trial.recipes import register_recipe
from viberadar.trial.runtime import create_trial, execute_trial, stop_trial, workspace


def main():
    out = PROJECT_ROOT / ".local" / "runtime-benchmark"
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    context = out / ("prepared-context-"+stamp)
    bundle = context / "bundle"
    bundle.mkdir(parents=True)
    settings = load_settings(data_dir=out / "preparation-data")
    factory = get_session_factory(settings.db_path)
    docker = TimedDocker()
    availability = docker.available()
    if not availability["available"]:
        raise SystemExit("Docker is unavailable")
    recipe = recipe_for_benchmark("markitdown", "prepare-"+stamp, availability["version"])
    app_encoded = recipe.install[-1].split("b64decode('", 1)[1].split("')", 1)[0]
    recipe.slug = "prepare-markitdown"
    recipe.install = ["mkdir -p /workspace/.tmp /workspace/wheels",
        "TMPDIR=/workspace/.tmp python -m pip download --disable-pip-version-check --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple --dest /workspace/wheels markitdown==0.1.8"]
    # First-party read-only directory server, no third-party application or user trial starts.
    recipe.run = ["python -m http.server 3000 --bind 0.0.0.0"]
    with factory() as session:
        register_recipe(session, recipe)
        session.commit()
    record = {"scope": "MarkItDown 0.1.8 base conversion, isolated benchmark image",
              "started_at": utcnow().isoformat(), "model_calls": 0}
    output = out / ("prepared-image-"+stamp+".json")
    started = time.perf_counter()
    session_id, token = create_trial(factory, settings, recipe.slug)
    try:
        print("Downloading wheels in the constrained preparation session", flush=True)
        execute_trial(factory, settings, session_id, token, docker)
        with factory() as session:
            row = session.get(TrialSessionRow, session_id)
            if row.status != "ready":
                raise RuntimeError(row.error or row.status)
        record["wheel_preparation_seconds"] = round(time.perf_counter()-started, 3)
        wheels = workspace(settings, session_id) / "work" / "wheels"
        files = list(wheels.iterdir())
        if not files or any(not p.is_file() or p.is_symlink() or p.suffix != ".whl" for p in files):
            raise RuntimeError("Wheel preparation returned unexpected files")
        record["wheels"] = [{"file": p.name, "bytes": p.stat().st_size,
                              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)]
        shutil.copytree(wheels, bundle / "wheels")
        (bundle / "app.py").write_bytes(base64.b64decode(app_encoded))
        base_image = json.loads(docker.call(["image", "inspect", "python:3.13-slim"]))[0]["RepoDigests"][0]
        record["base_image"] = base_image
        (context / "Dockerfile").write_text(
            f"FROM {base_image} AS builder\n"
            "COPY --chown=1000:1000 bundle/ /opt/viberadar/\n"
            "USER 1000:1000\n"
            "RUN --network=none python -m pip install --disable-pip-version-check --no-cache-dir --no-index --find-links=/opt/viberadar/wheels --target=/opt/viberadar/deps markitdown==0.1.8\n"
            f"FROM {base_image}\n"
            "COPY --from=builder --chown=1000:1000 /opt/viberadar/deps /opt/viberadar/deps\n"
            "COPY --from=builder --chown=1000:1000 /opt/viberadar/app.py /opt/viberadar/app.py\n"
            "USER 1000:1000\n", encoding="utf-8")
        build_started = time.perf_counter()
        print("Assembling a non-root, offline image from the prepared wheels", flush=True)
        tag = "viberadar-benchmark/markitdown:"+stamp
        # Use a private CLI config with the signed Desktop buildx plugin, never a personal
        # Docker config that could inject registry credentials or automatic proxy build args.
        cli_config = out / "build-cli"
        plugin_dir = cli_config / "cli-plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin = Path(docker.executable()).parent.parent / "cli-plugins" / "docker-buildx.exe"
        if plugin.is_file():
            shutil.copyfile(plugin, plugin_dir / plugin.name)
        record.update(status="building", context=str(context))
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        docker.call(["--config", str(cli_config), "buildx", "build", "--load", "--network=none",
                     "--pull=false", "--tag", tag, str(context)], timeout=300)
        record["image_build_seconds"] = round(time.perf_counter()-build_started, 3)
        info = json.loads(docker.call(["image", "inspect", tag]))[0]
        record.update(image=info["Id"], image_bytes=info["Size"],
                      preparation_seconds=round(time.perf_counter()-started, 3), status="prepared")
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in record.items() if k != "wheels"}), flush=True)
        print(str(output), flush=True)
    except Exception as exc:
        record.update(status="failed", error=str(exc).replace(token, "[virtual key]"),
                      preparation_seconds=round(time.perf_counter()-started, 3))
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        stop_trial(factory, settings, session_id, docker, remove=True)


if __name__ == "__main__":
    main()
