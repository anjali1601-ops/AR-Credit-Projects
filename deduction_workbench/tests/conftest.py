import pytest
from fastapi.testclient import TestClient

from deduction_workbench.api import create_app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DEDUCTION_LLM_PROVIDER", "offline")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return create_app(tmp_path / "workbench.sqlite")


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client
