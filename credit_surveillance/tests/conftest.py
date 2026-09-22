from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from credit_surveillance.api import create_app
from credit_surveillance.db import connect


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "portfolio.db")
    yield connection
    connection.close()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CREDIT_SURVEILLANCE_LLM_PROVIDER", raising=False)
    application = create_app(tmp_path / "api.db", seed_on_startup=True)
    with TestClient(application) as test_client:
        yield test_client
