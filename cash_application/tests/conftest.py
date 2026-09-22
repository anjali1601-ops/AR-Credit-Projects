"""Isolated SQLite files for API tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cash_application.db import reset_engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASH_APP_DB", str(tmp_path / "ar.sqlite"))
    monkeypatch.setenv("CASH_APP_LLM_PROVIDER", "offline")
    reset_engine()
    from cash_application.api import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()
