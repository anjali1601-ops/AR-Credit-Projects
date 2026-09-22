"""Exception routing: leftovers carry ranked candidates and a safe action set."""

from __future__ import annotations

from cash_application.seed import PRIMARY_REFS, run_case

ACTIONS = {
    "LBX-20260918-014": ("apply", "split", "leave_unapplied"),
    "EDI-820-20260918-VM": ("apply", "split", "leave_unapplied"),
    "EML-20260919-CASCADE": ("apply", "split", "leave_unapplied"),
    "LBX-20260918-088": ("apply", "split", "leave_unapplied"),
    "LBX-20260918-102": ("split", "leave_unapplied"),
    "EDI-820-20260918-RMU": ("apply", "split", "leave_unapplied"),
    "EML-20260920-LAKESHORE": ("apply", "split", "leave_unapplied"),
    "LBX-20260921-221": ("apply", "split", "leave_unapplied"),
}

RECOMMENDED = {
    "LBX-20260918-014": "apply",
    "EDI-820-20260918-VM": "apply",
    "EML-20260919-CASCADE": "apply",
    "LBX-20260918-088": "apply",
    "LBX-20260918-102": "leave_unapplied",
}


def test_primary_cases_land_in_the_right_queue():
    ready = []
    exceptions = []
    for ref in PRIMARY_REFS:
        _extraction, match, decision = run_case(ref)
        (ready if decision.queue == "ready" else exceptions).append((ref, match.kind))
        assert decision.allowed_actions == ACTIONS[ref]
        assert decision.recommended_action == RECOMMENDED[ref]
        assert decision.reason
    assert ready == [
        ("LBX-20260918-014", "full"),
        ("EDI-820-20260918-VM", "full"),
    ]
    assert {kind for _ref, kind in exceptions} == {"short_pay", "overpay", "unapplied"}


def test_exception_payload_includes_ranked_candidates():
    _extraction, match, decision = run_case("EML-20260919-CASCADE")
    assert decision.queue == "exception"
    assert match.candidates
    assert match.candidates[0].selected is True
    assert match.candidates[0].invoice_number == "INV-10510"
    assert match.candidates == tuple(sorted(match.candidates, key=lambda item: (-item.score, item.invoice_number)))
    labels = match.candidates[0].reasons
    assert "invoice_number" in labels
    assert "customer" in labels


def test_unapplied_cash_offers_nearest_amounts_but_not_apply():
    _extraction, match, decision = run_case("LBX-20260918-102")
    assert decision.queue == "exception"
    assert "apply" not in decision.allowed_actions
    assert decision.recommended_action == "leave_unapplied"
    assert match.lines == ()
    assert match.candidates
    assert all(not candidate.selected for candidate in match.candidates)
    assert all("nearest_amount" in candidate.reasons for candidate in match.candidates)
    assert match.candidates[0].invoice_number == "INV-10492"


def test_overpay_and_short_pay_keep_a_one_click_apply():
    for ref in ("LBX-20260918-088", "EML-20260919-CASCADE", "EML-20260920-LAKESHORE"):
        _extraction, match, decision = run_case(ref)
        assert decision.queue == "exception"
        assert decision.allowed_actions[0] == "apply"
        assert "split" in decision.allowed_actions
        assert "leave_unapplied" in decision.allowed_actions
        assert any(candidate.selected for candidate in match.candidates)


def test_persisted_exception_queue_matches_the_router(client):
    body = client.get("/api/exceptions").json()
    refs = {item["external_ref"] for item in body}
    assert "LBX-20260918-014" not in refs
    assert "EDI-820-20260918-VM" not in refs
    assert "EML-20260919-CASCADE" in refs
    assert "LBX-20260918-088" in refs
    assert "LBX-20260918-102" in refs
    assert "EDI-820-20260918-RMU" in refs
    assert "LBX-20260921-221" in refs
    for item in body:
        assert item["candidates"]
        assert item["allowed_actions"] == list(ACTIONS[item["external_ref"]])
        assert item["route_reason"]
        assert item["explainer_provider"] == "offline"
        assert item["status"] != "posted"
        assert item["receipt"] is None
