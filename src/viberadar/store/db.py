"""数据库引擎、会话与初始化（PRD 8.3：SQLite + WAL + foreign_keys + busy_timeout）。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def make_engine(db_path: str | Path | None = None, echo: bool = False) -> Engine:
    if db_path is None or str(db_path) == ":memory:":
        engine = create_engine(
            "sqlite+pysqlite://",
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite+pysqlite:///{path.as_posix()}",
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        # 关闭 pysqlite 隐式 BEGIN，由下面的 begin 事件统一发 BEGIN IMMEDIATE，
        # 避免 WAL 下"先读后写"事务升级导致的 database is locked（写锁 + busy_timeout 才生效）。
        dbapi_conn.isolation_level = None
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):  # noqa: ANN001
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def init_db(engine: Engine) -> None:
    """P0 迁移策略：按 metadata 建表；结构变更时用 Alembic 或迁移脚本。"""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}


def _key(db_path: str | Path | None) -> str:
    if db_path is None:
        from viberadar.settings import get_settings

        return str(get_settings().db_path)
    return str(db_path)


def get_engine(db_path: str | Path | None = None, reset: bool = False) -> Engine:
    key = _key(db_path)
    if key not in _engines or reset:
        # 注意：key 是解析后的默认路径；必须用 key 建文件库，否则会落入内存库
        _engines[key] = make_engine(key) if db_path is None else make_engine(db_path)
        init_db(_engines[key])
        _factories.pop(key, None)
    return _engines[key]


def get_session_factory(db_path: str | Path | None = None) -> sessionmaker[Session]:
    key = _key(db_path)
    if key not in _factories:
        _factories[key] = make_session_factory(get_engine(db_path))
    return _factories[key]


def session_scope(db_path: str | Path | None = None) -> Session:
    return get_session_factory(db_path)()
