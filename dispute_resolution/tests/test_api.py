"""HTTP surface: inbox simulation, case list/detail, one-click approvals."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(fresh_db: None) -> Iterator[TestClient]:
    from dra.api.main import app

    with TestClient(app) as test_client:
        yield test_client


def _process_inbox(client: TestClient) -> list[dict]:
    response = client.post("/api/inbox/poll", params={"limit": 25})
    assert response.status_code == 200
    return response.json()


def test_health_reports_the_offline_defaults(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "mock"
    assert body["database"] == "sqlite"


def test_inbox_lists_the_seeded_messages(client: TestClient) -> None:
    messages = client.get("/api/inbox").json()
    assert len(messages) == 5
    assert all(m["status"] == "unread" for m in messages)


def test_polling_opens_a_case_per_dispute_and_skips_the_rest(client: TestClient) -> None:
    results = _process_inbox(client)
    assert sum(1 for r in results if r["status"] == "processed") == 4
    assert sum(1 for r in results if r["status"] == "skipped") == 1

    cases = client.get("/api/cases").json()
    assert len(cases) == 4
    assert {c["state"] for c in cases} == {"awaiting_approval"}
    assert sorted(c["decision"] for c in cases) == ["invalid", "invalid", "valid", "valid"]


def test_case_detail_exposes_evidence_clause_and_the_sql_that_was_run(
    client: TestClient,
) -> None:
    _process_inbox(client)
    case = client.get("/api/cases", params={"decision": "valid"}).json()[0]
    detail = client.get(f"/api/cases/{case['id']}").json()

    assert detail["evidence"]
    assert detail["cited_clause_ref"]
    assert detail["queries"] and all(q["status"] == "ok" for q in detail["queries"])
    assert {d["kind"] for d in detail["drafts"]} == {"credit_memo", "supervisor_email"}
    assert detail["credit_memo"]["status"] == "draft"
    assert "/approve/" in detail["approval_links"]["approve_url"]
    assert "/reject/" in detail["approval_links"]["reject_url"]


def test_an_invalid_claim_drafts_a_rebuttal_instead_of_a_credit(client: TestClient) -> None:
    _process_inbox(client)
    case = client.get("/api/cases", params={"decision": "invalid"}).json()[0]
    detail = client.get(f"/api/cases/{case['id']}").json()

    assert detail["credit_memo"] is None
    assert [d["kind"] for d in detail["drafts"]] == ["customer_email"]
    body = detail["drafts"][0]["body"]
    assert detail["cited_clause_ref"].split()[-1] in body
    assert "not able to accept this deduction" in body


def test_one_click_approval_issues_the_credit_memo(client: TestClient) -> None:
    _process_inbox(client)
    case = client.get("/api/cases", params={"decision": "valid"}).json()[0]
    token_url = client.get(f"/api/cases/{case['id']}").json()["approval_links"]["approve_url"]
    token = token_url.rsplit("/", 1)[-1]

    page = client.get(f"/approve/{token}")
    assert page.status_code == 200
    assert "Approved" in page.text

    detail = client.get(f"/api/cases/{case['id']}").json()
    assert detail["state"] == "resolved"
    assert detail["credit_memo"]["status"] == "issued"
    assert detail["resolution"].startswith("credit_memo_issued:")
    assert any(d["kind"] == "customer_email" and d["status"] == "sent" for d in detail["drafts"])


def test_a_used_approval_link_cannot_be_replayed(client: TestClient) -> None:
    _process_inbox(client)
    case = client.get("/api/cases", params={"decision": "valid"}).json()[0]
    token = client.get(f"/api/cases/{case['id']}").json()["approval_links"][
        "approve_url"
    ].rsplit("/", 1)[-1]

    assert "Approved" in client.get(f"/approve/{token}").text
    assert "not awaiting approval" in client.get(f"/approve/{token}").text


def test_rejecting_voids_the_memo_and_sends_nothing(client: TestClient) -> None:
    _process_inbox(client)
    case = client.get("/api/cases", params={"decision": "valid"}).json()[0]
    response = client.post(
        f"/api/cases/{case['id']}/reject",
        json={"actor": "ar.lead@acme-supply.example", "note": "want the photos first"},
    )
    assert response.status_code == 200

    detail = client.get(f"/api/cases/{case['id']}").json()
    assert detail["state"] == "rejected"
    assert detail["credit_memo"]["status"] == "voided"
    assert all(d["status"] == "draft" for d in detail["drafts"])


def test_approving_a_case_that_is_not_awaiting_approval_is_a_conflict(
    client: TestClient,
) -> None:
    _process_inbox(client)
    case = client.get("/api/cases").json()[0]
    assert client.post(f"/api/cases/{case['id']}/approve", json={}).status_code == 200
    assert client.post(f"/api/cases/{case['id']}/approve", json={}).status_code == 409


def test_unknown_case_returns_404(client: TestClient) -> None:
    assert client.get("/api/cases/4242").status_code == 404


def test_posting_a_new_email_runs_the_whole_pipeline(client: TestClient) -> None:
    response = client.post(
        "/api/inbox/messages",
        json={
            "sender": "ap@vertexmfg.example",
            "sender_name": "Priya Raman",
            "subject": "Deduction on INV-2025-0161",
            "body": (
                "We are short-paying invoice INV-2025-0161 by $2,132.00 because 26 of the "
                "500 bushings on PO VX-30288 never arrived."
            ),
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["state"] == "awaiting_approval"
    assert payload["decision"] == "valid"


def test_contract_search_exposes_the_same_retrieval_the_auditor_uses(
    client: TestClient,
) -> None:
    hits = client.get(
        "/api/contracts/search",
        params={
            "q": "goods damaged in transit noted on the proof of delivery",
            "customer_code": "CUST-1001",
            "reason_code": "damaged_goods",
        },
    ).json()
    assert hits[0]["clause_ref"] == "7.3"
    assert hits[0]["source_pdf"].endswith(".pdf")


def test_the_dashboard_renders(client: TestClient) -> None:
    _process_inbox(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "Dispute Desk" in page.text
    assert "CASE-" in page.text

    case_id = client.get("/api/cases").json()[0]["id"]
    detail_page = client.get(f"/cases/{case_id}")
    assert detail_page.status_code == 200
    assert "Evidence the auditor relied on" in detail_page.text
