"""备份与恢复（PRD 10.4/M9）。

- 每日备份；保留 7 份日备份 + 4 份周备份（最长约 28 天）。
- 备份加密且至少有一份异机副本：本模块只负责本地备份产物，
  异机复制由部署层（如 rclone/rsync）完成。
- 恢复必须恢复到 staging 目录，不能直接覆盖生产。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from viberadar.settings import Settings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(settings: Settings, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    db_path = settings.db_path
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    stamp = now.strftime("%Y%m%d-%H%M%S")
    target_dir = settings.backups_dir / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    db_backup = target_dir / "viberadar.sqlite3"

    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(db_backup))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    if settings.public_dir.exists():
        shutil.copytree(settings.public_dir, target_dir / "public", dirs_exist_ok=True)

    manifest = {
        "created_at": now.isoformat(),
        "db": db_backup.name,
        "sha256": _sha256(db_backup),
        "size_bytes": db_backup.stat().st_size,
        "public_included": (target_dir / "public").exists(),
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _prune_backups(settings)
    return {"dir": str(target_dir), **manifest}


def _prune_backups(settings: Settings) -> None:
    backups = sorted(
        [p for p in settings.backups_dir.iterdir() if p.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    keep = {p.name for p in backups[:7]}  # 7 份日备份
    weekly_keys: set[tuple[int, int]] = set()
    for path in backups[7:]:
        try:
            dt = datetime.strptime(path.name, "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        iso = dt.isocalendar()
        key = (iso[0], iso[1])
        if key not in weekly_keys:
            weekly_keys.add(key)
            keep.add(path.name)
        if len(weekly_keys) >= 4:
            break
    for path in backups:
        if path.name not in keep:
            shutil.rmtree(path, ignore_errors=True)


def restore_to_staging(settings: Settings, backup_dir: Path, staging_dir: Path) -> dict:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"缺少 manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    db_backup = backup_dir / manifest["db"]
    if _sha256(db_backup) != manifest["sha256"]:
        raise RuntimeError("备份校验失败：哈希不一致，拒绝恢复")
    staging_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(db_backup.parent, staging_dir, dirs_exist_ok=True)
    return {
        "staging_dir": str(staging_dir),
        "verified": True,
        "created_at": manifest["created_at"],
        "note": "仅恢复到隔离环境；验证通过后再决定是否替换生产。",
    }
