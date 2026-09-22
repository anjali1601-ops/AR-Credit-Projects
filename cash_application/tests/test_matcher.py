"""Deterministic ingestion and matching. The explainer is not in this path."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cash_application.exceptions import route, signature
from cash_application.explain import ExplainFacts, OfflineExplainer, OpenAIExplainer, get_explainer
from cash_application.ingest import IngestError, parse_remittance
from cash_application.matcher import CustomerView, InvoiceView, propose
from cash_application.money import money
from cash_application.seed import run_case

EXPECTED = {
    "LBX-20260918-014": {
        "kind": "full",
        "lines": ("INV-10481",),
        "amounts": ("4250.00",),
        "confidence": "0.98",
        "queue": "ready",
        "unapplied": "0.00",
        "short_fall": "0.00",
        "signature": ("full", 1, "ready", False, False),
    },
    "EDI-820-20260918-VM": {
        "kind": "full",
        "lines": ("INV-10530", "INV-10531"),
        "amounts": ("8900.00", "2150.00"),
        "confidence": "0.96",
        "queue": "ready",
        "unapplied": "0.00",
        "short_fall": "0.00",
        "signature": ("full", 2, "ready", False, False),
    },
    "EML-20260919-CASCADE": {
        "kind": "short_pay",
        "lines": ("INV-10510",),
        "amounts": ("11240.00",),
        "confidence": "0.90",
        "queue": "exception",
        "unapplied": "0.00",
        "short_fall": "1240.00",
        "signature": ("short_pay", 1, "exception", False, True),
    },
    "LBX-20260918-088": {
        "kind": "overpay",
        "lines": ("INV-10550",),
        "amounts": ("5600.00",),
        "confidence": "0.88",
        "queue": "exception",
        "unapplied": "500.00",
        "short_fall": "0.00",
        "signature": ("overpay", 1, "exception", True, False),
    },
    "LBX-20260918-102": {
        "kind": "unapplied",
        "lines": (),
        "amounts": (),
        "confidence": "0.12",
        "queue": "exception",
        "unapplied": "2000.00",
        "short_fall": "0.00",
        "signature": ("unapplied", 0, "exception", True, False),
    },
}


@pytest.mark.parametrize("ref", list(EXPECTED))
def test_five_remittance_types_have_distinct_outcomes(ref):
    extraction, match, decision = run_case(ref)
    expected = EXPECTED[ref]
    assert match.kind == expected["kind"]
    assert tuple(line.invoice_number for line in match.lines) == expected["lines"]
    assert tuple(f"{line.apply_amount}" for line in match.lines) == expected["amounts"]
    assert f"{match.confidence:.2f}" == expected["confidence"]
    assert f"{match.unapplied_cash}" == expected["unapplied"]
    assert f"{match.short_fall}" == expected["short_fall"]
    assert decision.queue == expected["queue"]
    assert signature(match, decision) == expected["signature"]
    assert extraction.amount == match.payment_amount


def test_five_signatures_do_not_collide():
    signatures = [signature(*run_case(ref)[1:]) for ref in EXPECTED]
    assert len(set(signatures)) == 5


def test_reference_match_without_invoice_number_is_held():
    extraction, match, decision = run_case("EDI-820-20260918-RMU")
    assert extraction.invoice_numbers == ()
    assert extraction.payer_account == "RM-55077"
    assert tuple(line.invoice_number for line in match.lines) == ("INV-10570",)
    assert match.kind == "full"
    assert f"{match.confidence:.2f}" == "0.86"
    assert decision.queue == "exception"
    assert "reference" in match.lines[0].reasons


def test_amount_and_customer_without_invoice_number_is_held():
    extraction, match, decision = run_case("LBX-20260921-221")
    assert extraction.invoice_numbers == ()
    assert tuple(line.invoice_number for line in match.lines) == ("INV-10492",)
    assert match.kind == "full"
    assert f"{match.confidence:.2f}" == "0.78"
    assert decision.queue == "exception"
    assert "invoice_number" not in match.lines[0].reasons


def test_multi_invoice_short_pay_waterfalls_in_cited_order():
    _extraction, match, decision = run_case("EML-20260920-LAKESHORE")
    assert match.kind == "short_pay"
    assert [(line.invoice_number, f"{line.apply_amount}") for line in match.lines] == [
        ("INV-10580", "1100.00"),
        ("INV-10581", "400.00"),
    ]
    assert f"{match.short_fall}" == "50.25"
    assert f"{match.confidence:.2f}" == "0.84"
    assert decision.queue == "exception"


def test_paid_invoice_is_not_applied():
    extraction, match, decision = run_case("EDI-820-20260918-VM")
    assert "INV-10544" not in {line.invoice_number for line in match.lines}
    paid = parse_remittance(
        "email",
        "\n".join(
            [
                "Payer: Vertex Manufacturing LLC",
                "Account: VM-30881",
                "Remitting: $940.00",
                "Invoices: INV-10544",
            ]
        ),
    )
    from cash_application.seed import customer_views, invoice_views

    result = propose(paid, invoice_views(), customer_views())
    paid_route = route(result)
    assert result.kind == "unapplied"
    assert result.lines == ()
    assert any("already paid" in note for note in result.notes)
    assert paid_route.queue == "exception"
    assert decision.queue == "ready"


def test_ambiguous_equal_amounts_are_not_auto_selected():
    customers = (CustomerView("AA-10001", "Acme Brass", ()),)
    invoices = (
        InvoiceView("INV-20001", "AA-10001", "Acme Brass", "PO-1", money("100.00"), "open"),
        InvoiceView("INV-20002", "AA-10001", "Acme Brass", "PO-2", money("100.00"), "open"),
    )
    extraction = parse_remittance(
        "lockbox",
        "\n".join(
            [
                "Payer: Acme Brass",
                "Account: AA-10001",
                "Check number: 10",
                "Check amount: 100.00",
                "Invoices:",
            ]
        ),
    )
    result = propose(extraction, invoices, customers)
    assert result.kind == "unapplied"
    assert result.lines == ()
    assert any("Several open invoices" in note for note in result.notes)


def test_cited_invoice_on_another_customer_is_an_exception():
    from cash_application.exceptions import route
    from cash_application.seed import customer_views, invoice_views

    extraction = parse_remittance(
        "email",
        "\n".join(
            [
                "Payer: Northwind Retail Co.",
                "Account: NW-10042",
                "Remitting: $11240.00",
                "Invoices: INV-10510",
            ]
        ),
    )
    result = propose(extraction, invoice_views(), customer_views())
    decision = route(result)
    assert result.customer_mismatch is True
    assert result.lines[0].invoice_number == "INV-10510"
    assert result.confidence == Decimal("0.55")
    assert decision.queue == "exception"


def test_blank_lockbox_fields_stay_on_their_own_line():
    extraction, _match, _decision = run_case("LBX-20260918-102")
    assert extraction.payer_name == "APEX SURPLUS LIQUIDATORS"
    assert extraction.payer_account is None
    assert extraction.invoice_numbers == ()
    assert extraction.payment_reference == "904418"
    assert extraction.amount == money("2000.00")


def test_ingest_reads_edi_line_amounts_and_rejects_a_blank_email():
    _extraction, match, _decision = run_case("EDI-820-20260918-VM")
    extraction, _, _ = run_case("EDI-820-20260918-VM")
    assert extraction.line_amounts["INV-10530"] == money("8900.00")
    assert extraction.line_amounts["INV-10531"] == money("2150.00")
    assert extraction.payment_reference == "820-20260918-VM"
    assert match.payment_amount == money("11050.00")
    with pytest.raises(IngestError):
        parse_remittance("email", "From: nobody@example.com\nPlease call me.")


def test_same_remittance_matches_twice_the_same_way():
    first = run_case("LBX-20260918-014")
    second = run_case("LBX-20260918-014")
    assert first[1] == second[1]
    assert first[2] == second[2]


def test_offline_explainer_describes_the_match_without_changing_it():
    extraction, match, decision = run_case("LBX-20260918-014")
    facts = ExplainFacts(
        channel="lockbox",
        external_ref="LBX-20260918-014",
        payer_name=extraction.payer_name,
        payer_account=extraction.payer_account,
        payment_reference=extraction.payment_reference,
        amount=f"{match.payment_amount}",
        kind=match.kind,
        confidence=f"{match.confidence:.2f}",
        recommended_action=decision.recommended_action,
        queue=decision.queue,
        applied_amount=f"{match.applied_amount}",
        unapplied_cash=f"{match.unapplied_cash}",
        short_fall=f"{match.short_fall}",
        deduction_note=None,
        invoices_cited=True,
        lines=(
            {
                "invoice_number": "INV-10481",
                "customer_name": "Northwind Retail Co.",
                "customer_account": "NW-10042",
                "open_amount": "4250.00",
                "apply_amount": "4250.00",
            },
        ),
        notes=(),
    )
    note = OfflineExplainer().explain(facts)
    assert note.provider == "offline"
    assert "INV-10481" in note.text
    assert "$4,250.00" in note.text
    assert "will not post" in note.text
    assert match.kind == "full"


def test_provider_switch_is_env_only(monkeypatch):
    monkeypatch.delenv("CASH_APP_LLM_PROVIDER", raising=False)
    assert isinstance(get_explainer(), OfflineExplainer)
    monkeypatch.setenv("CASH_APP_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    explainer = get_explainer()
    assert isinstance(explainer, OpenAIExplainer)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        explainer.explain(
            ExplainFacts(
                channel="email",
                external_ref="X",
                payer_name=None,
                payer_account=None,
                payment_reference=None,
                amount="1.00",
                kind="unapplied",
                confidence="0.12",
                recommended_action="leave_unapplied",
                queue="exception",
                applied_amount="0.00",
                unapplied_cash="1.00",
                short_fall="0.00",
                deduction_note=None,
                invoices_cited=False,
                lines=(),
                notes=(),
            )
        )
    monkeypatch.setenv("CASH_APP_LLM_PROVIDER", "nope")
    with pytest.raises(ValueError):
        get_explainer()
