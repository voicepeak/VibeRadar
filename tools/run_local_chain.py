"""Isolated local acceptance service: real model refresh, verified-pack import, real playground.

Discovery input is deliberately fixed to the real MarkItDown repository. No product/feed
row is pre-created. Configure the model in this service's settings page before refreshing.
"""
from __future__ import annotations

import gzip
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime

import uvicorn

from viberadar.contracts import utcnow
from viberadar.contracts.trial import TryRecipe, Verification
from viberadar.settings import PROJECT_ROOT, load_settings
from viberadar.store import get_session_factory
from viberadar.trial.runtime import Docker
from viberadar.trial.recipes import register_recipe
from viberadar.web.app import create_app

ROOT = PROJECT_ROOT / ".local" / "local-chain"
BASE_ID = "sha256:57ff3d0bcef71f45a578ed5136edd9332314b702964199f9b5bdace0912d629f"
BASE_TAG = "viberadar-benchmark/markitdown:20261014061438"


def start_docker(docker):
    if docker.available()["available"]:
        return
    docker.start_desktop()


def prepare_recipe(docker, settings):
    manifest = ROOT / "playground-image.json"
    source = PROJECT_ROOT / "examples" / "markitdown"
    source_hash = hashlib.sha256((source / "app.py").read_bytes()+(source / "index.html").read_bytes()).hexdigest()
    if manifest.exists():
        stored = json.loads(manifest.read_text(encoding="utf-8"))
        archive = settings.data_dir / "recipe-images" / (stored["archive_sha256"]+".tar.gz")
        if stored["source_sha256"] == source_hash and archive.is_file():
            return stored
    try:
        docker.call(["image", "inspect", BASE_ID])
    except RuntimeError:
        cached = PROJECT_ROOT / ".local" / "runtime-benchmark" / "markitdown-prepared.tar.gz"
        if not cached.is_file():
            raise RuntimeError("Prepare the base benchmark image first with tools/prepare_benchmark_image.py")
        docker.call(["load", "--input", str(cached)], timeout=120)
        docker.call(["image", "inspect", BASE_ID])
    docker.call(["tag", BASE_ID, BASE_TAG])
    context = ROOT / "image-build"
    context.mkdir(parents=True, exist_ok=True)
    for name in ("app.py", "index.html"):
        shutil.copyfile(source / name, context / name)
    (context / "Dockerfile").write_text(f"FROM {BASE_TAG}\nCOPY --chown=1000:1000 app.py index.html /opt/viberadar/\nUSER 1000:1000\n", encoding="utf-8")
    cli_config = ROOT / "build-cli"
    plugins = cli_config / "cli-plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    plugin = Path(docker.executable()).parent.parent / "cli-plugins" / "docker-buildx.exe"
    shutil.copyfile(plugin, plugins / plugin.name)
    tag = "viberadar-local/markitdown-playground:"+source_hash[:16]
    docker.call(["--config", str(cli_config), "buildx", "build", "--load", "--network=none",
                 "--pull=false", "--tag", tag, str(context)], timeout=120)
    image = json.loads(docker.call(["image", "inspect", tag]))[0]["Id"]
    archives = settings.data_dir / "recipe-images"
    archives.mkdir(parents=True, exist_ok=True)
    temporary = archives / "playground-export.tmp"
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PROGRAMFILES"}}
    with (ROOT / "image-export.log").open("wb") as errors:
        process = subprocess.Popen([docker.executable(), "image", "save", image],
                                   stdout=subprocess.PIPE, stderr=errors, env=env)
        with gzip.open(temporary, "wb", compresslevel=6) as output:
            while chunk := process.stdout.read(1024*1024):
                output.write(chunk)
        if process.wait():
            raise RuntimeError("Playground image export failed")
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    archive = archives / (digest+".tar.gz")
    temporary.replace(archive)
    stored = {"image": image, "archive_sha256": digest, "source_sha256": source_hash,
              "archive_bytes": archive.stat().st_size, "prepared_at": utcnow().isoformat()}
    manifest.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    return stored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fresh", action="store_true", help="Use a fresh acceptance database; preserve model config and image pack")
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    settings = load_settings(data_dir=ROOT / "data")
    db_path = settings.data_dir / ("chain-"+utcnow().strftime("%Y%m%d%H%M%S")+".sqlite3") if args.fresh else settings.db_path
    # Only the discovery input is fixed. The pipeline and configured model run normally.
    for definition in settings.sources.sources.values():
        definition.enabled = False
    inbox = ROOT / "discovery.jsonl"
    inbox.write_text(json.dumps({"url": "https://github.com/microsoft/markitdown", "title": "MarkItDown",
        "description": "Convert HTML and documents to Markdown; local HTML playground available.",
        "operator": "local-chain-controlled-discovery"})+"\n", encoding="utf-8")
    manual = settings.sources.sources["manual"]
    manual.enabled = True
    manual.config["path"] = str(inbox)
    docker = Docker()
    start_docker(docker)
    pack = prepare_recipe(docker, settings)
    recipe = TryRecipe(slug="markitdown", version="local-playground-v2-"+pack["source_sha256"][:16],
        tier="container", image=pack["image"], image_archive_sha256=pack["archive_sha256"],
        run=["PYTHONPATH=/opt/viberadar/deps python /opt/viberadar/app.py"], healthcheck="/health",
        timeout_minutes=3, ttl_minutes=120,
        license_checked="MarkItDown 0.1.8 MIT; first-party HTML UI (local acceptance scope)",
        verified=Verification(by="local acceptance fixture", at=datetime.fromisoformat(pack["prepared_at"]),
                              device="Windows Docker Desktop local acceptance"),
        guide=["这是 HTML 转 Markdown 的本地试玩样例，支持粘贴或打开 UTF-8 HTML 文件。",
               "本地配方包会自动校验并导入镜像；PDF、Office 和 OCR 不在此样例范围内。"],
        guide_en=["This local playground converts pasted or uploaded UTF-8 HTML to Markdown.",
                  "The verified local pack is imported automatically. PDF, Office and OCR are outside this sample."],
        entry_url="https://github.com/microsoft/markitdown")
    factory = get_session_factory(db_path)
    with factory() as session:
        register_recipe(session, recipe)
        session.commit()
    app = create_app(db_path, settings)
    (ROOT / "current-db.json").write_text(json.dumps({"path": str(db_path)}), encoding="utf-8")
    app.state.local_acceptance_mode = True
    print(f"Local-chain acceptance: http://127.0.0.1:{args.port}/", flush=True)
    print(f"Configure the real model: http://127.0.0.1:{args.port}/admin/llm", flush=True)
    print("Discovery input is fixed; model, refresh, Docker and conversion are real.", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
