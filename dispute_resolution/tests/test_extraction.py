"""Ingestion agent: email text in, structured claim out."""

from __future__ import annotations

from decimal import Decimal

import pytest

from dra.agents import IngestionAgent
from dra.inbox.samples import SAMPLE_EMAILS


@pytest.fixture(scope="module")
def agent(_isolated_environment: None) -> IngestionAgent:
    return IngestionAgent()


def _sample(message_id: str) -> dict:
    return next(s for s in SAMPLE_EMAILS if s["message_id"] == message_id)


@pytest.mark.parametrize(
    ("message_id", "invoice", "reason", "amount"),
    [
        ("msg-northwind-damage-0148", "INV-2025-0148", "damaged_goods", "1240.50"),
        ("msg-cascade-discount-0152", "INV-2025-0152", "unauthorized_discount", "612.00"),
        ("msg-vertex-shortage-0161", "INV-2025-0161", "short_shipment", "2132.00"),
        ("msg-northwind-duplicate-0170", "INV-2025-0170", "duplicate_billing", "3480.00"),
    ],
)
def test_extracts_invoice_reason_and_amount(
    agent: IngestionAgent, message_id: str, invoice: str, reason: str, amount: str
) -> None:
    sample = _sample(message_id)
    extraction = agent.extract(
        sender=str(sample["sender"]),
        subject=str(sample["subject"]),
        body=str(sample["body"]),
    )
    assert extraction.is_dispute is True
    assert extraction.invoice_number == invoice
    assert extraction.reason_code == reason
    assert extraction.disputed_amount == Decimal(amount)
    assert extraction.confidence > 0.5


def test_picks_the_deduction_not_the_invoice_total(agent: IngestionAgent) -> None:
    """The remittance sentence carries both figures; the deduction is the claim."""
    extraction = agent.extract(
        sender="payables@cascadehealth.example",
        subject="Remittance for INV-2025-0152",
        body="We remitted $29,988.00 and deducted $612.00 as our 2% early payment discount.",
    )
    assert extraction.disputed_amount == Decimal("612.00")


def test_non_dispute_email_is_not_escalated(agent: IngestionAgent) -> None:
    sample = _sample("msg-harborline-remittance-0181")
    extraction = agent.extract(
        sender=str(sample["sender"]),
        subject=str(sample["subject"]),
        body=str(sample["body"]),
    )
    assert extraction.is_dispute is False
    assert "paid in full" in extraction.triage_note


def test_dispute_without_an_invoice_number_is_held_back(agent: IngestionAgent) -> None:
    extraction = agent.extract(
        sender="ap@northwind-retail.example",
        subject="Deduction on last week's shipment",
        body="We deducted $500.00 for damaged goods on the delivery that arrived Tuesday.",
    )
    assert extraction.is_dispute is False
    assert extraction.invoice_number is None


def test_purchase_order_is_captured(agent: IngestionAgent) -> None:
    sample = _sample("msg-vertex-shortage-0161")
    extraction = agent.extract(
        sender=str(sample["sender"]),
        subject=str(sample["subject"]),
        body=str(sample["body"]),
    )
    assert extraction.po_number == "VX-30288"
