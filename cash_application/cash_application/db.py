"""SQLite session helpers. Tests point CASH_APP_DB at a temp file."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from cash_application.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def default_db_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "ar.sqlite"


def db_path() -> Path:
    override = os.environ.get("CASH_APP_DB", "").strip()
    return Path(override) if override else default_db_path()


def reset_engine() -> None:
    """Drop the cached engine so a new CASH_APP_DB takes effect."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)

        @event.listens_for(_engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record) -> None:  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    init_db()
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. The request commits if the route did not raise."""
    init_db()
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
