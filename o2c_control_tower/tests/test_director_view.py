"""Queue, workload, narrative, CLI, and HTTP all read the same snapshot."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from o2c_control_tower.app import app
from o2c_control_tower.render import render_html, render_text
from o2c_control_tower.summary import build_control_tower

ROOT = Path(__file__).resolve().parents[2]
NARRATIVE = (
    "As of 22 September 2026, open receivables are $160,000.00. "
    "Trailing 30-day DSO is 48.98 days, compared with 30.65 days at the start of the window. "
    "Billings added 30.00 days, collections removed 9.18 days, sales volume removed 1.88 days, "
    "and credit memos removed 0.61 days. "
    "Predicted cash over the next 14 days is $52,060.00. "
    "6 actions are waiting on a person. "
    "The oldest has been open 14 days: soft reminder, Helios Retail, owner Ava Chen."
)


def test_every_waiting_action_has_an_owner_and_an_age():
    tower = build_control_tower()
    assert [action.action_type for action in tower.actions] == [
        "soft_reminder",
        "credit_hold",
        "unapplied_cash",
        "credit_memo",
        "soft_reminder",
        "unapplied_cash",
    ]
    assert [(action.id, action.owner, action.age_days) for action in tower.actions] == [
        ("ACT-SR-1003", "Ava Chen", 14),
        ("ACT-CH-ORION", "Marcus Hale", 10),
        ("ACT-UA-ORION", "Marcus Hale", 7),
        ("ACT-CM-2006", "Priya Shah", 4),
        ("ACT-SR-1002", "Ava Chen", 2),
        ("ACT-UA-BLUE", "Priya Shah", 1),
    ]
    assert all(action.owner and action.age_days >= 0 for action in tower.actions)
    assert tower.unapplied_cash == Decimal("5700.00")
    assert tower.outside_collectors == ("Priya Shah",)


def test_collector_scores():
    tower = build_control_tower()
    by_name = {load.collector: load for load in tower.collectors}
    ava = by_name["Ava Chen"]
    marcus = by_name["Marcus Hale"]
    assert ava.open_ar == Decimal("94000.00")
    assert ava.past_due_ar == Decimal("30000.00")
    assert (ava.open_invoices, ava.past_due_invoices, ava.broken_promises, ava.waiting_actions) == (7, 3, 1, 2)
    assert ava.workload_score == 7 + 2 * 3 + 3 * 1 + 2
    assert marcus.open_ar == Decimal("66000.00")
    assert marcus.past_due_ar == Decimal("32000.00")
    assert (marcus.open_invoices, marcus.past_due_invoices, marcus.broken_promises, marcus.waiting_actions) == (
        5,
        2,
        1,
        2,
    )
    assert marcus.workload_score == 14
    assert ava.open_ar + marcus.open_ar == tower.open_ar


def test_narrative_only_phrases_the_computed_figures():
    tower = build_control_tower()
    assert tower.narrative == NARRATIVE
    assert "$160,000.00" in tower.narrative
    assert "$52,060.00" in tower.narrative


def test_cli_and_page_print_the_same_figures():
    tower = build_control_tower()
    text = render_text(tower)
    page = render_html(tower)
    for figure in ("$160,000.00", "48.9796", "$52,060.00", "Ava Chen", "Priya Shah"):
        assert figure in text
        assert figure in page
    result = subprocess.run(
        [sys.executable, "-m", "o2c_control_tower"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == text


def test_http_summary_matches_the_hand_totals():
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "as_of": "2026-09-22"}

    summary = client.get("/api/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["kpis"]["open_ar"] == "160000.00"
    assert body["kpis"]["beginning_dso_days"] == "30.6522"
    assert body["kpis"]["ending_dso_days"] == "48.9796"
    assert body["kpis"]["dso_change_days"] == "18.3274"
    assert body["kpis"]["predicted_cash_14d"] == "52060.00"
    assert body["kpis"]["actions_waiting"] == 6
    assert body["dso_bridge"]["ties"] is True
    assert body["dso_bridge"]["components"][-1]["running"] == "48.9796"
    assert body["dso_bridge"]["components"][-2]["running"] == "48.9796"
    assert body["aging"]["buckets"][0]["share"] == "61.3%"
    assert body["aging"]["buckets"][1]["share"] == "5.6%"
    assert body["aging"]["buckets"][2]["share"] == "13.1%"
    assert body["aging"]["buckets"][3]["share"] == "12.5%"
    assert body["aging"]["buckets"][4]["share"] == "7.5%"
    assert {row["type"] for row in body["actions"]} == {
        "credit_memo",
        "soft_reminder",
        "credit_hold",
        "unapplied_cash",
    }

    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "$160,000.00" in page.text
    assert "48.9796" in page.text
    assert client.get("/api/aging").status_code == 200
    assert client.get("/api/dso").status_code == 200
    assert client.get("/api/cash-forecast").status_code == 200
    assert client.get("/api/workload").status_code == 200
    assert client.get("/api/actions").status_code == 200
