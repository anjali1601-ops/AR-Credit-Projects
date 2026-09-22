"""Auditor agent: the validity decision, its evidence and its clause citation."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from dra.agents import AuditorAgent, IngestionAgent
from dra.agents.schemas import AuditDecision, DisputeExtraction
from dra.inbox.samples import SAMPLE_EMAILS


def _audit(message_id: str) -> AuditDecision:
    sample = next(s for s in SAMPLE_EMAILS if s["message_id"] == message_id)
    extraction = IngestionAgent().extract(
        sender=str(sample["sender"]),
        subject=str(sample["subject"]),
        body=str(sample["body"]),
    )
    return AuditorAgent().audit(extraction, claim_date=dt.date.today())


def test_damage_supported_by_the_proof_of_delivery_is_upheld(seeded: dict) -> None:
    decision = _audit("msg-northwind-damage-0148")
    assert decision.decision == "valid"
    assert decision.recommended_credit == Decimal("1240.50")
    assert decision.citation is not None
    assert decision.citation.clause_ref == "7.3"
    assert decision.citation.contract_number == "MSA-2023-014"
    assert all(check.passed for check in decision.checks)


def test_shortage_proven_by_our_own_shipping_log_is_upheld(seeded: dict) -> None:
    decision = _audit("msg-vertex-shortage-0161")
    assert decision.decision == "valid"
    assert decision.recommended_credit == Decimal("2132.00")
    assert decision.facts["shortfall_units"] == 26
    assert decision.citation.clause_ref == "3.3"


def test_discount_taken_outside_the_window_is_declined(seeded: dict) -> None:
    decision = _audit("msg-cascade-discount-0152")
    assert decision.decision == "invalid"
    assert decision.recommended_credit == Decimal("0")
    assert decision.citation.clause_ref == "6.4"
    timing = next(c for c in decision.checks if "discount window" in c.name)
    assert timing.passed is False
    # The schedule exists; it is the payment timing that fails.
    assert next(c for c in decision.checks if "in force" in c.name).passed is True


def test_a_duplicate_claim_with_no_duplicate_is_declined(seeded: dict) -> None:
    decision = _audit("msg-northwind-duplicate-0170")
    assert decision.decision == "invalid"
    assert decision.facts["duplicate_count"] == 0
    assert "only billing" in decision.rationale


def test_the_audit_runs_only_read_only_queries_and_records_them(seeded: dict) -> None:
    decision = _audit("msg-northwind-damage-0148")
    assert len(decision.queries) == 7
    assert all(q["status"] == "ok" for q in decision.queries)
    for query in decision.queries:
        assert query["sql"].lstrip().upper().startswith(("SELECT", "WITH"))
        assert query["params"] == {"invoice_number": "INV-2025-0148"}


def test_an_unknown_invoice_asks_for_more_information(seeded: dict) -> None:
    extraction = DisputeExtraction(
        is_dispute=True,
        invoice_number="INV-9999-0000",
        reason_code="damaged_goods",
        disputed_amount=Decimal("100.00"),
        confidence=0.8,
    )
    decision = AuditorAgent().audit(extraction)
    assert decision.decision == "needs_more_info"
    assert decision.missing_information


def test_a_claim_with_no_invoice_reference_never_touches_the_database() -> None:
    decision = AuditorAgent().audit(
        DisputeExtraction(is_dispute=True, invoice_number=None, reason_code="other")
    )
    assert decision.decision == "needs_more_info"
    assert decision.queries == []


@pytest.mark.parametrize(
    ("message_id", "expected"),
    [(s["message_id"], s["expected"]) for s in SAMPLE_EMAILS if s["expected"] != "skipped"],
)
def test_every_seeded_scenario_lands_on_its_expected_ruling(
    seeded: dict, message_id: str, expected: str
) -> None:
    assert _audit(message_id).decision == expected
