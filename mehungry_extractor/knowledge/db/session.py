"""SQLite engine + session helpers.

Deterministic posture: foreign keys enforced, and the schema is created idempotently via
``create_all`` (guarded by ``SCHEMA_VERSION``) — Alembic is deferred until the schema
stabilizes (spec §13). Reads elsewhere always impose an explicit ORDER BY so row order is
stable across runs.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .schema import Base


def default_db_path() -> Path:
    return Path(os.environ.get("MEHUNGRY_DB", "data/mehungry.sqlite"))


def get_engine(db_path: Optional[str | Path] = None) -> Engine:
    path = Path(db_path) if db_path is not None else default_db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_conn, _record):  # enforce FK constraints on SQLite
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
