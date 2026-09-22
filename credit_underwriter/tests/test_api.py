"""HTTP API: health, applicants, underwrite, memo, runs, evidence, and trace."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from credit_underwriter import api as api_module
from credit_underwriter.config import DEFAULT_API_PORT


@pytest.fixture()
def client(tmp_settings):
    api_module.reset_state(tmp_settings)
    with TestClient(api_module.app) as test_client:
        yield test_client
    api_module.reset_state()


def test_index_lists_the_service_and_default_port(client):
    payload = client.get("/").json()
    assert payload["service"] == "Autonomous AI Credit Underwriter"
    assert payload["default_port"] == DEFAULT_API_PORT
    assert "/docs" in payload["docs"]


def test_healthz_reports_offline_provider_and_seeded_applicants(client):
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["llm_provider"] == "offline"
    assert payload["live_search_enabled"] is False
    assert payload["corpus_documents"] >= 18
    assert set(payload["seeded_applicants"]) >= {
        "atlas-precision-works",
        "northwind-logistics",
        "veritas-metal-trading",
    }


def test_applicants_endpoint_lists_the_seeded_portfolio(client):
    payload = client.get("/applicants").json()
    ids = {row["applicant_id"] for row in payload}
    assert ids == {
        "atlas-precision-works",
        "northwind-logistics",
        "veritas-metal-trading",
    }
    assert all(row["source"] == "seeded" for row in payload)
    assert all(row["requested_limit"] > 0 for row in payload)


def test_underwrite_returns_a_decision_summary_for_atlas(client):
    payload = client.post("/applications/atlas-precision-works/underwrite").json()

    assert payload["applicant_id"] == "atlas-precision-works"
    assert payload["recommendation"] == "approve"
    assert payload["approved_limit"] == 750_000.0
    assert payload["approved_terms_days"] == 60
    assert payload["final_grade"] == 2
    assert payload["completeness_check_passed"] is True
    assert payload["memo_claims"] > 0
    assert payload["run_id"]
    assert payload["state_hash"]


def test_memo_endpoint_serves_markdown_after_underwriting(client):
    underwrite = client.post("/applications/northwind-logistics/underwrite")
    assert underwrite.status_code == 200
    decision = underwrite.json()

    response = client.get("/applications/northwind-logistics/memo")
    assert response.status_code == 200
    assert "markdown" in response.headers["content-type"]
    body = response.text
    assert "Approve with conditions" in body
    assert "## Evidence appendix" in body
    assert decision["run_id"] in body or "completeness check" in body.lower()


def test_memo_json_includes_the_structured_memo(client):
    client.post("/applications/veritas-metal-trading/underwrite")
    payload = client.get(
        "/applications/veritas-metal-trading/memo", params={"format": "json"}
    ).json()
    assert payload["decision"]["recommendation"] == "decline"
    assert payload["decision"]["approved_limit"] == 0.0
    assert payload["memo"]["final_grade"] == 10
    assert payload["memo"]["sections"]


def test_memo_can_underwrite_lazily_when_missing(client):
    response = client.get("/applications/atlas-precision-works/memo")
    assert response.status_code == 200
    assert "Approve" in response.text


def test_memo_404s_when_lazy_underwrite_is_disabled(client):
    response = client.get(
        "/applications/atlas-precision-works/memo",
        params={"underwrite_if_missing": False},
    )
    assert response.status_code == 404


def test_unknown_applicant_returns_404(client):
    response = client.post("/applications/no-such-company/underwrite")
    assert response.status_code == 404
    assert "unknown applicant" in response.json()["detail"]


def test_submit_then_underwrite_a_posted_application(client, atlas):
    payload = atlas.model_dump(mode="json")
    payload["applicant_id"] = "posted-atlas-clone"
    payload["legal_name"] = "Posted Atlas Clone, Inc."

    accepted = client.post("/applications", json=payload)
    assert accepted.status_code == 201
    body = accepted.json()
    assert body["accepted"] is True
    assert body["applicant_id"] == "posted-atlas-clone"

    listed = {row["applicant_id"]: row for row in client.get("/applicants").json()}
    assert listed["posted-atlas-clone"]["source"] == "submitted"

    decision = client.post("/applications/posted-atlas-clone/underwrite").json()
    assert decision["recommendation"] == "approve"
    assert decision["legal_name"] == "Posted Atlas Clone, Inc."


def test_runs_endpoint_lists_persisted_underwritings(client):
    first = client.post("/applications/atlas-precision-works/underwrite").json()
    listed = client.get("/runs").json()
    assert any(row["run_id"] == first["run_id"] for row in listed)

    fetched = client.get(f"/runs/{first['run_id']}").json()
    assert fetched["recommendation"] == first["recommendation"]
    assert fetched["state_hash"] == first["state_hash"]


def test_run_evidence_and_trace_are_addressable(client):
    decision = client.post("/applications/atlas-precision-works/underwrite").json()
    run_id = decision["run_id"]

    evidence = client.get(f"/runs/{run_id}/evidence").json()
    assert evidence["count"] == len(evidence["items"]) > 0
    kinds = {item["kind"] for item in evidence["items"]}
    assert "ratio" in kinds
    assert "document" in kinds

    ratios = client.get(f"/runs/{run_id}/evidence", params={"kind": "ratio"}).json()
    assert ratios["count"] > 0
    assert all(item["kind"] == "ratio" for item in ratios["items"])

    trace = client.get(f"/runs/{run_id}/trace").json()
    agents = {entry["agent"] for entry in trace["trace"]}
    assert {"supervisor", "financial_analyst", "risk_searcher", "memo_writer", "memo_critic"} <= agents
    assert trace["plan"]


def test_unknown_run_returns_404(client):
    assert client.get("/runs/no-such-run").status_code == 404
    assert client.get("/runs/no-such-run/evidence").status_code == 404
    assert client.get("/runs/no-such-run/trace").status_code == 404
