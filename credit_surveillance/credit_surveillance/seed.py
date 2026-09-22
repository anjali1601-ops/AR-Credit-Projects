"""Four open accounts, each built so the policy lands on a different action."""

import sqlite3
from datetime import timedelta
from decimal import Decimal

from credit_surveillance.db import (
    clear_portfolio,
    insert_account,
    insert_invoice,
    insert_open_order,
    insert_promise,
)
from credit_surveillance.exposure import AS_OF
from credit_surveillance.formatting import q_money
from credit_surveillance.models import Account, Invoice, OpenOrder, PromiseToPay

# Paid-history windows are measured from AS_OF. Recent invoices are paid inside
# 90 days. Baseline invoices are paid before that cutoff.
_RECENT_AGE = 15
_BASELINE_AGE = 140


def seed_database(conn: sqlite3.Connection) -> list[str]:
    """Replace the portfolio with the four demonstration accounts."""
    clear_portfolio(conn)
    for spec in _SPECS:
        _insert_spec(conn, spec)
    return [spec["id"] for spec in _SPECS]


def _insert_spec(conn: sqlite3.Connection, spec: dict) -> None:
    account = Account(
        id=spec["id"],
        name=spec["name"],
        segment=spec["segment"],
        terms_days=spec["terms_days"],
        credit_limit=q_money(Decimal(spec["credit_limit"])),
        status="open",
        conditions=(),
        analyst_note=spec["analyst_note"],
        requested_limit=None,
    )
    insert_account(conn, account)
    sequence = 1
    for offset, days_to_pay in enumerate(spec["baseline_days"]):
        insert_invoice(
            conn,
            _paid_invoice(spec, sequence, "baseline", offset, days_to_pay),
        )
        sequence += 1
    for offset, days_to_pay in enumerate(spec["recent_days"]):
        insert_invoice(
            conn,
            _paid_invoice(spec, sequence, "recent", offset, days_to_pay),
        )
        sequence += 1
    for offset, (amount, days_past_due) in enumerate(spec["open_invoices"]):
        due = (
            AS_OF + timedelta(days=28)
            if days_past_due is None
            else AS_OF - timedelta(days=days_past_due)
        )
        insert_invoice(
            conn,
            Invoice(
                id=f"{spec['id']}-INV-{sequence:03d}",
                account_id=spec["id"],
                invoice_date=due - timedelta(days=spec["terms_days"]),
                due_date=due,
                amount=q_money(Decimal(amount)),
                paid_date=None,
            ),
        )
        sequence += 1
    for offset, (amount, description) in enumerate(spec["open_orders"], start=1):
        insert_open_order(
            conn,
            OpenOrder(
                id=f"{spec['id']}-PO-{offset:02d}",
                account_id=spec["id"],
                amount=q_money(Decimal(amount)),
                description=description,
            ),
        )
    for offset, (amount, status, days_ago) in enumerate(spec["promises"], start=1):
        insert_promise(
            conn,
            PromiseToPay(
                id=f"{spec['id']}-PTP-{offset:02d}",
                account_id=spec["id"],
                amount=q_money(Decimal(amount)),
                promised_date=AS_OF - timedelta(days=days_ago),
                status=status,
            ),
        )


def _paid_invoice(spec: dict, sequence: int, cohort: str, offset: int, days_to_pay: int) -> Invoice:
    if cohort == "recent":
        paid_date = AS_OF - timedelta(days=_RECENT_AGE + offset)
    else:
        paid_date = AS_OF - timedelta(days=_BASELINE_AGE + offset * 7)
    invoice_date = paid_date - timedelta(days=days_to_pay)
    return Invoice(
        id=f"{spec['id']}-INV-{sequence:03d}",
        account_id=spec["id"],
        invoice_date=invoice_date,
        due_date=invoice_date + timedelta(days=spec["terms_days"]),
        amount=q_money(Decimal("5000")),
        paid_date=paid_date,
    )


_SPECS = (
    {
        "id": "NW-1044",
        "name": "Northwind Components",
        "segment": "Building products distributor",
        "terms_days": 30,
        "credit_limit": "100000.00",
        "analyst_note": "Seasonal buyer. Pays inside terms. Limit last set March 2026.",
        "baseline_days": (27, 28, 28, 29, 28, 28),
        "recent_days": (28, 29, 30, 29),
        "open_invoices": (("40000.00", None), ("2000.00", 12)),
        "open_orders": (("18000.00", "September replenishment"),),
        "promises": (("5000.00", "kept", 20),),
    },
    {
        "id": "HB-2201",
        "name": "Harborline Industrial",
        "segment": "Industrial MRO",
        "terms_days": 30,
        "credit_limit": "150000.00",
        "analyst_note": "Core MRO account. Days to pay have slipped since June.",
        "baseline_days": (30, 32, 32, 34, 32, 32),
        "recent_days": (30, 30, 30, 64, 64, 64),
        "open_invoices": (("77000.00", None), ("18000.00", 25)),
        "open_orders": (("20000.00", "Q3 contract release"),),
        "promises": (("4000.00", "broken", 12),),
    },
    {
        "id": "VP-3310",
        "name": "Vesper Packaging",
        "segment": "Packaging converter",
        "terms_days": 30,
        "credit_limit": "80000.00",
        "analyst_note": "Growing releases. Exposure is sitting above the limit.",
        "baseline_days": (28, 29, 30, 29, 29, 29),
        "recent_days": (30, 30, 30, 30, 30, 36),
        "open_invoices": (("69000.00", None), ("3000.00", 10)),
        "open_orders": (("22000.00", "Corrugate release"),),
        "promises": (("8000.00", "broken", 6),),
    },
    {
        "id": "RL-4408",
        "name": "Redline Metals",
        "segment": "Metals service center",
        "terms_days": 30,
        "credit_limit": "200000.00",
        "analyst_note": "Three missed promise-to-pay dates. Past-due invoices are aged past 60 days.",
        "baseline_days": (33, 35, 36, 35, 34, 37),
        "recent_days": (68, 70, 72, 74, 76, 72),
        "open_invoices": (("82000.00", None), ("78000.00", 67)),
        "open_orders": (("25000.00", "Mill release"),),
        "promises": (
            ("15000.00", "broken", 40),
            ("18000.00", "broken", 25),
            ("12000.00", "broken", 9),
        ),
    },
)
