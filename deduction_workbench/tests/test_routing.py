"""Policy outcome and desk. Each reason code lands on a different queue."""

import sqlite3

import pytest

from deduction_workbench.db import connect
from tests.expected import EXPECTED


def test_seeded_cases_follow_the_routing_rules(client) -> None:
    rows = {row["id"]: row for row in client.get("/api/cases").json()}
    assert set(rows) == set(EXPECTED)
    valid_queues = {}
    for case_id, expected in EXPECTED.items():
        row = rows[case_id]
        detail = client.get(f"/api/cases/{case_id}").json()
        assert row["reason_code"] == expected["reason"]
        assert row["outcome"] == expected["outcome"]
        assert row["queue"] == expected["queue"]
        assert row["organization"] == expected["organization"]
        assert row["citation_id"] == expected["citation"]
        assert expected["phrase"] in detail["citation_text"]
        assert detail["citation_id"] in detail["rationale"]
        assert any(hit["clause_id"] == detail["citation_id"] for hit in detail["retrieved"])
        assert detail["sent"] is False
        assert detail["auto_send"] is False
        assert detail["dispatched"] is False
        assert detail["llm_provider"] == "offline"
        assert "Nothing has been sent." in detail["narrative"]
        assert detail["status"] == "awaiting_confirmation"
        if expected["outcome"] == "valid":
            valid_queues[expected["reason"]] = expected["queue"]
        else:
            assert row["queue"] == "recovery"
            assert detail["citation_text"].strip()

    assert valid_queues == {
        "shortage": "warehouse",
        "pricing": "sales",
        "returns": "returns",
        "coop_advertising": "sales_coop",
        "damaged_goods": "quality",
    }
    assert len(set(valid_queues.values())) == 5


def test_each_reason_code_routes_differently(client) -> None:
    rows = client.get("/api/cases").json()
    by_reason = {}
    for row in rows:
        if row["outcome"] != "valid":
            continue
        by_reason.setdefault(row["reason_code"], set()).add(row["queue"])
    assert set(by_reason) == {
        "shortage",
        "pricing",
        "returns",
        "coop_advertising",
        "damaged_goods",
    }
    queues = [next(iter(names)) for names in by_reason.values()]
    assert len(queues) == len(set(queues))
    assert by_reason["shortage"] == {"warehouse"}
    assert by_reason["pricing"] == {"sales"}


def test_valid_promo_goes_to_sales_and_invalid_claims_cite_the_clause(client) -> None:
    promo = client.get("/api/cases/DED-1003").json()
    coop = client.get("/api/cases/DED-1007").json()
    shortage = client.get("/api/cases/DED-1001").json()
    invalid = client.get("/api/cases/DED-1004").json()
    assert promo["outcome"] == "valid"
    assert promo["queue"] == "sales"
    assert promo["organization"] == "sales"
    assert coop["outcome"] == "valid"
    assert coop["organization"] == "sales"
    assert coop["queue"] == "sales_coop"
    assert shortage["queue"] == "warehouse"
    assert invalid["queue"] == "recovery"
    assert invalid["citation_id"]
    assert invalid["citation_text"]
    failed = [check for check in invalid["checks"] if not check["passed"]]
    assert failed
    assert failed[0]["name"] == "promo_covers_sku_and_ship_date"


def test_late_damage_fails_the_notice_window(client) -> None:
    detail = client.get("/api/cases/DED-1010").json()
    checks = {check["name"]: check for check in detail["checks"]}
    assert checks["evidence_on_file"]["passed"] is True
    assert checks["inside_notice_window"]["passed"] is False
    assert detail["queue"] == "recovery"


def test_full_pod_shortage_is_not_a_warehouse_ticket(client) -> None:
    detail = client.get("/api/cases/DED-1002").json()
    checks = {check["name"]: check for check in detail["checks"]}
    assert checks["pod_shows_shortage"]["passed"] is False
    assert detail["queue"] == "recovery"
    assert "MSA-2025-077 §4.2" == detail["citation_id"]


def test_database_refuses_to_mark_a_case_sent(app) -> None:
    conn = connect(app.state.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE decisions SET sent = 1")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert "outbound" not in tables
    assert "messages" not in tables
