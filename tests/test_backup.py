"""备份与恢复（PRD 10.4/M9/E24）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from viberadar.ops import backup as backup_mod
from viberadar.store import repo_editions, repo_products

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def test_backup_and_restore_verified(temp_settings, monkeypatch):
    from viberadar.store import get_engine, get_session_factory

    monkeypatch.setenv("VIBERADAR_DB_PATH", str(temp_settings.db_path))
    engine = get_engine(str(temp_settings.db_path), reset=True)
    factory = get_session_factory(str(temp_settings.db_path))
    with factory() as session:
        edition = repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
        product = repo_products.create_product(
            session, "BackupMe", None, None, "url:https://example.com/b", NOW
        )
        repo_editions.replace_items(
            session, edition, edition.revision, [{"product_id": product.id, "content": {}}]
        )
        session.commit()

    result = backup_mod.backup_database(temp_settings, now=NOW)
    backup_dir = temp_settings.backups_dir / NOW.strftime("%Y%m%d-%H%M%S")
    assert (backup_dir / "manifest.json").exists()

    staging = temp_settings.data_dir / "staging"
    restored = backup_mod.restore_to_staging(temp_settings, backup_dir, staging)
    assert restored["verified"] is True
    assert (staging / "viberadar.sqlite3").exists()
    engine.dispose()


def test_restore_rejects_tampered_backup(temp_settings, monkeypatch):
    from viberadar.store import get_engine, get_session_factory

    monkeypatch.setenv("VIBERADAR_DB_PATH", str(temp_settings.db_path))
    engine = get_engine(str(temp_settings.db_path), reset=True)
    factory = get_session_factory(str(temp_settings.db_path))
    with factory() as session:
        repo_editions.create_edition(session, date(2026, 10, 12), "Asia/Shanghai")
        session.commit()
    backup_mod.backup_database(temp_settings, now=NOW)
    backup_dir = temp_settings.backups_dir / NOW.strftime("%Y%m%d-%H%M%S")
    db_file = backup_dir / "viberadar.sqlite3"
    db_file.write_bytes(db_file.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError):
        backup_mod.restore_to_staging(temp_settings, backup_dir, temp_settings.data_dir / "staging")
    engine.dispose()


def test_backup_prune_keeps_limits(temp_settings, monkeypatch):
    from datetime import timedelta

    from viberadar.store import get_engine

    monkeypatch.setenv("VIBERADAR_DB_PATH", str(temp_settings.db_path))
    engine = get_engine(str(temp_settings.db_path), reset=True)
    for day in range(12):
        backup_mod.backup_database(temp_settings, now=NOW + timedelta(days=day))
    backups = [p for p in temp_settings.backups_dir.iterdir() if p.is_dir()]
    assert len(backups) <= 11  # 7 日备份 + 最多 4 周备份
    engine.dispose()


_ = json
