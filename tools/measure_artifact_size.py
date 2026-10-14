"""Measure source distribution and a prepared image separately, without publishing."""
import gzip
import json
import os
from pathlib import Path
import subprocess
import time
import zipfile

from viberadar.settings import PROJECT_ROOT
from viberadar.trial.runtime import Docker


def main():
    root = PROJECT_ROOT / ".local" / "runtime-benchmark"
    image = "sha256:57ff3d0bcef71f45a578ed5136edd9332314b702964199f9b5bdace0912d629f"
    docker = Docker()
    if not docker.available()["available"]:
        cli = Path(docker.executable())
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        subprocess.Popen([str(cli.parent.parent.parent / "Docker Desktop.exe")], startupinfo=info)
        deadline = time.perf_counter()+60
        while not docker.available()["available"]:
            if time.perf_counter() >= deadline:
                raise RuntimeError("Docker did not become available")
            time.sleep(2)
    inspection = json.loads(docker.call(["image", "inspect", image]))[0]
    archive = root / "markitdown-prepared.tar.gz"
    log = root / "image-save.stderr.log"
    started = time.perf_counter()
    raw = 0
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PROGRAMFILES"}}
    with log.open("wb") as errors:
        process = subprocess.Popen([docker.executable(), "image", "save", image],
                                   stdout=subprocess.PIPE, stderr=errors, env=env)
        with gzip.open(archive, "wb", compresslevel=6) as dest:
            while chunk := process.stdout.read(1024*1024):
                raw += len(chunk)
                dest.write(chunk)
        code = process.wait()
    if code:
        raise RuntimeError(log.read_text(encoding="utf-8", errors="replace"))
    record = {"image_expanded_bytes": inspection["Size"], "docker_save_stream_bytes": raw,
              "compressed_archive_bytes": archive.stat().st_size,
              "save_and_compress_seconds": round(time.perf_counter()-started, 3)}
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    cwd=PROJECT_ROOT).decode("utf-8").split("\0")
    source_zip = root / "viberadar-source.zip"
    total = files = 0
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for name in paths:
            path = PROJECT_ROOT / name
            if name and path.is_file():
                zipped.write(path, arcname=name)
                total += path.stat().st_size
                files += 1
    record.update(source_archive_bytes=source_zip.stat().st_size, source_uncompressed_bytes=total,
                  source_files=files, source_scope="Git-visible source, frontend, configs, docs, tests; excludes Python environment, images and runtime data")
    (root / "package-size.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
