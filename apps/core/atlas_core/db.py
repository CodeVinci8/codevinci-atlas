"""Движок и сессии SQLAlchemy (SQLite WAL) — Master Spec §8.1."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_Session: sessionmaker | None = None


def _configure_sqlite(dbapi_conn, _rec) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")
    cur.execute("PRAGMA busy_timeout=5000;")
    cur.close()


def init_engine(db_url: str, db_path: str | None = None) -> Engine:
    global _engine, _Session
    if db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(db_url, future=True, connect_args={"check_same_thread": False})
    event.listen(_engine, "connect", _configure_sqlite)
    _Session = sessionmaker(bind=_engine, class_=Session, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("engine не инициализирован; вызовите init_engine()")
    return _engine


def session_scope() -> Session:
    if _Session is None:
        raise RuntimeError("session factory не инициализирована")
    return _Session()
