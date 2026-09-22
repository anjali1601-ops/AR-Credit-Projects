from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dunning import api as api_module


@pytest.fixture()
def client(deps, monkeypatch):
    monkeypatch.setattr(api_module, "_deps", lambda: deps)
    return TestClient(api_module.api)


def test_health_reports_the_active_providers(client):
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["llm_provider"] == "mock"
    assert payload["sentiment_backend"] == "rules"
    assert payload["accounts"] == 9


def test_accounts_endpoint_lists_the_portfolio(client):
    payload = client.get("/accounts").json()

    assert len(payload) == 9
    assert {row["archetype"] for row in payload} == {
        "reliable_but_late",
        "deteriorating_avoidant",
        "high_risk_delinquent",
    }
    assert all(row["past_due_balance"] > 0 for row in payload)


def test_run_endpoint_returns_a_full_sequence(client):
    payload = client.post("/runs", json={"account_id": "acc-3001"}).json()

    assert payload["profile"]["archetype"] == "high_risk_delinquent"
    assert payload["strategy"]["legal_referral"] is True
    assert payload["review"]["passed"] is True
    assert payload["sequence"]["steps"][0]["body"]


def test_run_endpoint_can_omit_bodies(client):
    payload = client.post("/runs", json={"account_id": "ACC-1001", "include_bodies": False}).json()
    assert all(step["body"] == "" for step in payload["sequence"]["steps"])
    assert payload["sequence"]["steps"][0]["subject"]


def test_unknown_account_returns_404(client):
    assert client.post("/runs", json={"account_id": "ACC-0000"}).status_code == 404


def test_eval_endpoint_scores_a_single_account(client):
    payload = client.get("/eval", params={"account_id": "ACC-2001"}).json()

    assert payload["pass_rate"] == 1.0
    assert payload["cases"][0]["expected_archetype"] == "deteriorating_avoidant"
