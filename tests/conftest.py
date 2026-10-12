"""pytest fixtures：临时数据目录、独立数据库与配置。"""

from __future__ import annotations

from pathlib import Path

import pytest

from viberadar.settings import PROJECT_ROOT, load_settings
from viberadar.store import init_db, make_engine, make_session_factory


@pytest.fixture()
def temp_settings(tmp_path: Path):
    data_dir = tmp_path / "data"
    settings = load_settings(config_dir=PROJECT_ROOT / "config", data_dir=data_dir)
    return settings


@pytest.fixture()
def session_factory(temp_settings, tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    engine = make_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def session(session_factory):
    with session_factory() as sess:
        yield sess
        sess.commit()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "app.sqlite3"


@pytest.fixture()
def app(temp_settings, db_path: Path):
    from viberadar.web.app import create_app

    application = create_app(db_path=str(db_path), settings=temp_settings)
    return application


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, headers={"Origin": "http://testserver"}) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _stable_env(monkeypatch):
    """测试不要读取真实凭据；LLM 使用 mock。"""
    monkeypatch.setenv("VIBERADAR_LLM_PROVIDER", "mock")
    monkeypatch.delenv("VIBERADAR_FEISHU_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("VIBERADAR_SITE_URL", "http://testserver")
    yield
