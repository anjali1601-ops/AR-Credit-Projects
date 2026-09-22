"""Exposure, aging, drift, and broken-promise math."""

from datetime import timedelta
from decimal import Decimal

import pytest

from credit_surveillance.db import get_account, list_invoices, list_open_orders, list_promises
from credit_surveillance.exposure import AS_OF, compute_exposure
from credit_surveillance.models import Invoice, PromiseToPay
from credit_surveillance.seed import seed_database
from credit_surveillance.service import account_facts


def _invoice(invoice_id, amount, due_offset, paid_after=None, terms=30):
    due = AS_OF + timedelta(days=due_offset)
    invoice_date = due - timedelta(days=terms)
    paid = None if paid_after is None else invoice_date + timedelta(days=paid_after)
    return Invoice(
        id=invoice_id,
        account_id="T",
        invoice_date=invoice_date,
        due_date=due,
        amount=Decimal(amount),
        paid_date=paid,
    )


def test_exposure_is_receivables_plus_open_orders():
    facts = compute_exposure(
        credit_limit=Decimal("100"),
        terms_days=30,
        invoices=[
            _invoice("A", "40", 5),
            _invoice("B", "10", -3),
        ],
        promises=[],
        open_order_amounts=[Decimal("25")],
    )
    assert facts.accounts_receivable == Decimal("50.00")
    assert facts.current_ar == Decimal("40.00")
    assert facts.past_due_ar == Decimal("10.00")
    assert facts.open_orders == Decimal("25.00")
    assert facts.exposure == Decimal("75.00")
    assert facts.over_limit_amount == Decimal("0.00")
    assert facts.utilization == Decimal("0.750000")
    assert facts.past_due_ratio == Decimal("0.200000")
    assert facts.max_days_past_due == 3


def test_over_limit_and_ratio_rounding():
    facts = compute_exposure(
        credit_limit=Decimal("70"),
        terms_days=30,
        invoices=[_invoice("A", "75", 10)],
        promises=[],
        open_order_amounts=[],
    )
    assert facts.exposure == Decimal("75.00")
    assert facts.over_limit_amount == Decimal("5.00")
    assert facts.utilization == Decimal("1.071429")
    assert "over_limit" in facts.signals


def test_zero_receivables_have_a_zero_past_due_ratio():
    paid = _invoice("A", "20", -10, paid_after=28)
    facts = compute_exposure(
        credit_limit=Decimal("100"),
        terms_days=30,
        invoices=[paid],
        promises=[],
        open_order_amounts=[Decimal("10")],
    )
    assert facts.accounts_receivable == Decimal("0.00")
    assert facts.past_due_ratio == Decimal("0.000000")
    assert facts.exposure == Decimal("10.00")


def test_payment_drift_and_late_rate():
    # Baseline paid 120 days before as-of, recent paid 10 days before as-of.
    baseline = []
    recent = []
    for index, days in enumerate((20, 22)):
        paid_on = AS_OF - timedelta(days=120 + index)
        invoice_date = paid_on - timedelta(days=days)
        baseline.append(
            Invoice("B" + str(index), "T", invoice_date, invoice_date + timedelta(days=30), Decimal("5"), paid_on)
        )
    for index, days in enumerate((40, 50)):
        paid_on = AS_OF - timedelta(days=10 + index)
        invoice_date = paid_on - timedelta(days=days)
        recent.append(
            Invoice("R" + str(index), "T", invoice_date, invoice_date + timedelta(days=30), Decimal("5"), paid_on)
        )
    facts = compute_exposure(
        credit_limit=Decimal("100"),
        terms_days=30,
        invoices=baseline + recent,
        promises=[],
        open_order_amounts=[],
    )
    assert facts.avg_days_to_pay_baseline == Decimal("21.00")
    assert facts.avg_days_to_pay_recent == Decimal("45.00")
    assert facts.payment_drift_days == Decimal("24.00")
    assert facts.invoices_late_recent == 2
    assert facts.late_payment_rate == Decimal("1.000000")
    assert "payment_drift" in facts.signals


def test_drift_of_five_days_is_not_a_watch_signal():
    invoices = []
    for index, days in enumerate((30, 30)):
        paid_on = AS_OF - timedelta(days=140 + index)
        invoice_date = paid_on - timedelta(days=days)
        invoices.append(
            Invoice(f"B{index}", "T", invoice_date, invoice_date + timedelta(days=30), Decimal("5"), paid_on)
        )
    for index, days in enumerate((35, 35)):
        paid_on = AS_OF - timedelta(days=20 + index)
        invoice_date = paid_on - timedelta(days=days)
        invoices.append(
            Invoice(f"R{index}", "T", invoice_date, invoice_date + timedelta(days=30), Decimal("5"), paid_on)
        )
    facts = compute_exposure(
        credit_limit=Decimal("100"),
        terms_days=30,
        invoices=invoices,
        promises=[],
        open_order_amounts=[],
    )
    assert facts.payment_drift_days == Decimal("5.00")
    assert "payment_drift" not in facts.signals


def test_broken_promises_ignore_kept_and_future_open():
    promises = [
        PromiseToPay("1", "T", Decimal("5"), AS_OF - timedelta(days=3), "kept"),
        PromiseToPay("2", "T", Decimal("7"), AS_OF + timedelta(days=4), "open"),
        PromiseToPay("3", "T", Decimal("9"), AS_OF - timedelta(days=1), "open"),
        PromiseToPay("4", "T", Decimal("11"), AS_OF - timedelta(days=8), "broken"),
    ]
    facts = compute_exposure(
        credit_limit=Decimal("100"),
        terms_days=30,
        invoices=[],
        promises=promises,
        open_order_amounts=[Decimal("1")],
    )
    assert facts.broken_promise_count == 2
    assert facts.broken_promise_amount == Decimal("20.00")
    assert "broken_promises" in facts.signals


def test_credit_limit_must_be_positive():
    with pytest.raises(ValueError, match="credit_limit"):
        compute_exposure(
            credit_limit=Decimal("0"),
            terms_days=30,
            invoices=[],
            promises=[],
            open_order_amounts=[],
        )


def test_seeded_portfolio_facts(conn):
    seed_database(conn)
    expected = {
        "NW-1044": {
            "exposure": Decimal("60000.00"),
            "past_due_ar": Decimal("2000.00"),
            "past_due_ratio": Decimal("0.047619"),
            "utilization": Decimal("0.600000"),
            "over_limit_amount": Decimal("0.00"),
            "avg_days_to_pay_baseline": Decimal("28.00"),
            "avg_days_to_pay_recent": Decimal("29.00"),
            "payment_drift_days": Decimal("1.00"),
            "late_payment_rate": Decimal("0.000000"),
            "broken_promise_count": 0,
            "broken_promise_amount": Decimal("0.00"),
            "max_days_past_due": 12,
            "signals": (),
        },
        "HB-2201": {
            "exposure": Decimal("115000.00"),
            "past_due_ar": Decimal("18000.00"),
            "past_due_ratio": Decimal("0.189474"),
            "utilization": Decimal("0.766667"),
            "over_limit_amount": Decimal("0.00"),
            "avg_days_to_pay_baseline": Decimal("32.00"),
            "avg_days_to_pay_recent": Decimal("47.00"),
            "payment_drift_days": Decimal("15.00"),
            "late_payment_rate": Decimal("0.500000"),
            "invoices_late_recent": 3,
            "broken_promise_count": 1,
            "broken_promise_amount": Decimal("4000.00"),
            "max_days_past_due": 25,
            "signals": ("payment_drift", "broken_promises"),
        },
        "VP-3310": {
            "exposure": Decimal("94000.00"),
            "past_due_ar": Decimal("3000.00"),
            "past_due_ratio": Decimal("0.041667"),
            "utilization": Decimal("1.175000"),
            "over_limit_amount": Decimal("14000.00"),
            "avg_days_to_pay_baseline": Decimal("29.00"),
            "avg_days_to_pay_recent": Decimal("31.00"),
            "payment_drift_days": Decimal("2.00"),
            "late_payment_rate": Decimal("0.166667"),
            "broken_promise_count": 1,
            "broken_promise_amount": Decimal("8000.00"),
            "max_days_past_due": 10,
            "signals": ("over_limit", "broken_promises"),
        },
        "RL-4408": {
            "exposure": Decimal("185000.00"),
            "past_due_ar": Decimal("78000.00"),
            "past_due_ratio": Decimal("0.487500"),
            "utilization": Decimal("0.925000"),
            "over_limit_amount": Decimal("0.00"),
            "avg_days_to_pay_baseline": Decimal("35.00"),
            "avg_days_to_pay_recent": Decimal("72.00"),
            "payment_drift_days": Decimal("37.00"),
            "late_payment_rate": Decimal("1.000000"),
            "broken_promise_count": 3,
            "broken_promise_amount": Decimal("45000.00"),
            "max_days_past_due": 67,
            "signals": ("payment_drift", "broken_promises"),
        },
    }

    for account_id, wanted in expected.items():
        account = get_account(conn, account_id)
        facts = account_facts(conn, account)
        for field, value in wanted.items():
            assert getattr(facts, field) == value, f"{account_id}.{field}"
        assert list_invoices(conn, account_id)
        assert list_open_orders(conn, account_id)
        assert list_promises(conn, account_id)
