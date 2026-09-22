"""Engine/session plumbing, including the hardened read-only connection.

Two access paths exist on purpose:

* ``session_scope()`` — the read/write ORM session. Only explicit application
  code (case transitions, credit memos, seeding) uses it.
* ``readonly_connection()`` — the connection handed to the Text-to-SQL path.
  The transaction itself is forced read-only at the database level, so a query
  that somehow slips past the SQL guardrails still cannot mutate anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from dra.db.models import Base
from dra.settings import get_settings


class ReadOnlySQLError(RuntimeError):
    """Raised when a statement is rejected before or during read-only execution."""


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {"future": True, "echo": get_settings().sql_echo}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return kwargs


@lru_cache(maxsize=8)
def _build_engine(url: str) -> Engine:
    eng = create_engine(url, **_engine_kwargs(url))
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return eng


def engine() -> Engine:
    return _build_engine(get_settings().effective_database_url)


def readonly_engine() -> Engine:
    return _build_engine(get_settings().effective_readonly_database_url)


def reset_engines() -> None:
    """Drop cached engines; used by tests that repoint the database URL."""
    _build_engine.cache_clear()


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


@contextmanager
def readonly_connection() -> Iterator:
    """Yield a connection whose transaction cannot write, whatever it is given."""
    settings = get_settings()
    eng = readonly_engine()
    conn = eng.connect()
    try:
        if eng.dialect.name == "sqlite":
            # query_only rejects INSERT/UPDATE/DELETE/DDL at the SQLite layer.
            conn.exec_driver_sql("PRAGMA query_only = ON")
        else:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(
                f"SET LOCAL statement_timeout = {int(settings.sql_statement_timeout_ms)}"
            )
        yield conn
    finally:
        conn.rollback()
        if eng.dialect.name == "sqlite":
            try:
                conn.exec_driver_sql("PRAGMA query_only = OFF")
            except Exception:  # pragma: no cover - connection already closed
                pass
        conn.close()


def init_db(drop: bool = False) -> None:
    eng = engine()
    if drop:
        Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)


def ping() -> bool:
    with engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
