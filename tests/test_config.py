"""配置校验：非法配置阻止新任务并保持旧版本可用（PRD 9.3/E23）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from viberadar.settings import PROJECT_ROOT, ConfigError, load_dotenv, load_settings


def test_load_dotenv_injects_and_does_not_override(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 注释\n"
        'VIBERADAR_TEST_NEW_KEY="quoted-value"\n'
        "export VIBERADAR_TEST_EXPORTED=plain\n"
        "VIBERADAR_TEST_EXISTING=from-file\n"
        "BAD LINE WITHOUT EQUALS\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBERADAR_TEST_EXISTING", "from-env")
    monkeypatch.delenv("VIBERADAR_TEST_NEW_KEY", raising=False)
    monkeypatch.delenv("VIBERADAR_TEST_EXPORTED", raising=False)
    injected = load_dotenv(env_file)
    import os

    assert injected == 2
    assert os.environ["VIBERADAR_TEST_NEW_KEY"] == "quoted-value"
    assert os.environ["VIBERADAR_TEST_EXPORTED"] == "plain"
    assert os.environ["VIBERADAR_TEST_EXISTING"] == "from-env"
    monkeypatch.delenv("VIBERADAR_TEST_NEW_KEY")
    monkeypatch.delenv("VIBERADAR_TEST_EXPORTED")


@pytest.fixture()
def config_copy(tmp_path: Path) -> Path:
    src = PROJECT_ROOT / "config"
    dst = tmp_path / "config"
    shutil.copytree(src, dst)
    return dst


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_missing_config_file_raises(config_copy: Path, tmp_path: Path):
    (config_copy / "pipeline.yaml").unlink()
    with pytest.raises(ConfigError):
        load_settings(config_dir=config_copy, data_dir=tmp_path / "data")


def test_invalid_taxonomy_trial_category(config_copy: Path, tmp_path: Path):
    rubric = yaml.safe_load((config_copy / "rubric.yaml").read_text(encoding="utf-8"))
    rubric["trial_categories"] = ["not-a-category"]
    _write(config_copy / "rubric.yaml", rubric)
    with pytest.raises(ConfigError):
        load_settings(config_dir=config_copy, data_dir=tmp_path / "data")


def test_invalid_threshold_ordering(config_copy: Path, tmp_path: Path):
    pipeline = yaml.safe_load((config_copy / "pipeline.yaml").read_text(encoding="utf-8"))
    pipeline["quality_gates"]["min_confidence"] = 0.4
    pipeline["quality_gates"]["manual_review_confidence"] = 0.6
    _write(config_copy / "pipeline.yaml", pipeline)
    with pytest.raises(ConfigError):
        load_settings(config_dir=config_copy, data_dir=tmp_path / "data")


def test_invalid_category_keys(config_copy: Path, tmp_path: Path):
    taxonomy = yaml.safe_load((config_copy / "taxonomy.yaml").read_text(encoding="utf-8"))
    taxonomy["product_category_keys"].append("ghost-category")
    _write(config_copy / "taxonomy.yaml", taxonomy)
    with pytest.raises(ConfigError):
        load_settings(config_dir=config_copy, data_dir=tmp_path / "data")


def test_config_hash_changes_with_content(config_copy: Path, tmp_path: Path):
    base = load_settings(config_dir=config_copy, data_dir=tmp_path / "data")
    pipeline = yaml.safe_load((config_copy / "pipeline.yaml").read_text(encoding="utf-8"))
    pipeline["limits"]["public_target"] = 7
    _write(config_copy / "pipeline.yaml", pipeline)
    changed = load_settings(config_dir=config_copy, data_dir=tmp_path / "data")
    assert base.config_hash != changed.config_hash


def test_valid_config_loads(config_copy: Path, tmp_path: Path):
    settings = load_settings(config_dir=config_copy, data_dir=tmp_path / "data")
    assert settings.pipeline.timezone == "Asia/Shanghai"
    assert settings.pipeline.schedule.cutoff == "07:30"
    assert settings.pipeline.schedule.publish == "09:00"
