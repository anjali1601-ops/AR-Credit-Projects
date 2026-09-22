"""Every test runs against a throwaway SQLite database and in-memory vector store."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_environment(tmp_path_factory: pytest.TempPathFactory) -> None:
    data_dir: Path = tmp_path_factory.mktemp("dra-test-data")
    os.environ.update(
        {
            "DRA_DATA_DIR": str(data_dir),
            "DRA_DATABASE_URL": f"sqlite+pysqlite:///{data_dir / 'test.sqlite3'}",
            "DRA_LLM_PROVIDER": "mock",
            # Chroma is exercised by the seed script and the manual demo; tests use
            # the in-memory store so they stay fast and leave nothing behind.
            "DRA_VECTOR_STORE": "memory",
            "DRA_INBOX_POLL_ENABLED": "false",
            "DRA_API_PORT": "47821",
        }
    )

    from dra.llm import reset_llm_cache
    from dra.rag.store import reset_store_cache
    from dra.settings import reset_settings_cache

    reset_settings_cache()
    reset_llm_cache()
    reset_store_cache()


@pytest.fixture(scope="session")
def seeded(_isolated_environment: None) -> dict:
    from dra.db.seed import seed_all

    return seed_all(reset=True, with_inbox=True)


@pytest.fixture()
def fresh_db(_isolated_environment: None):
    """A database reseeded from scratch for tests that mutate case state."""
    from dra.db.seed import seed_all

    seed_all(reset=True, with_inbox=True)
    yield
