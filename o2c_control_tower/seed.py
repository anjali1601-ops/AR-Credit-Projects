"""Seeded book of business as of 22 September 2026.

The portfolio is small enough to recompute by hand. Posted cash and the one
posted credit memo change AR. Receipts that have not been matched sit only in
the human queue and do not reduce AR. Pending credit memos are the same.

Hand ledger, close of 23 August 2026 (beginning AR):

    INV-1002  20,000
    INV-1003  15,000
    INV-1004  20,000   (25,000 billed, 5,000 applied 1 August)
    INV-1005  12,000
    INV-1006  18,000
    INV-1007   9,000
    INV-1101       0   (paid 15 August, counted in prior-window sales)
    INV-1102       0   (paid 20 August, counted in prior-window sales)
              --------
              94,000

Current window 24 August–22 September:

    Billings     98,000   INV-2001 through INV-2007
    Collections  30,000   12,000 on INV-1002 and 18,000 on INV-1006
    Credit memos  2,000   posted against INV-1003
    Ending AR   160,000   94,000 + 98,000 − 30,000 − 2,000
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from o2c_control_tower.format import money
from o2c_control_tower.models import (
    AppliedReceipt,
    CreditMemo,
    Customer,
    Invoice,
    PendingAction,
    Portfolio,
    Promise,
)

AS_OF = date(2026, 9, 22)


def _m(amount: str) -> Decimal:
    return money(amount)


def load_portfolio() -> Portfolio:
    customers = (
        Customer("C-NORTH", "Northwind Foods", "Ava Chen"),
        Customer("C-HELIOS", "Helios Retail", "Ava Chen"),
        Customer("C-BLUE", "Blue Harbor Logistics", "Marcus Hale"),
        Customer("C-ORION", "Orion Health", "Marcus Hale"),
    )
    invoices = (
        Invoice("INV-1002", "C-NORTH", date(2026, 7, 1), date(2026, 7, 31), _m("20000")),
        Invoice("INV-1003", "C-HELIOS", date(2026, 7, 20), date(2026, 8, 19), _m("15000")),
        Invoice("INV-1004", "C-BLUE", date(2026, 5, 15), date(2026, 6, 29), _m("25000")),
        Invoice("INV-1005", "C-ORION", date(2026, 4, 1), date(2026, 5, 1), _m("12000")),
        Invoice("INV-1006", "C-NORTH", date(2026, 8, 10), date(2026, 9, 9), _m("18000")),
        Invoice("INV-1007", "C-HELIOS", date(2026, 8, 20), date(2026, 9, 19), _m("9000")),
        Invoice("INV-1101", "C-HELIOS", date(2026, 7, 28), date(2026, 8, 27), _m("40000")),
        Invoice("INV-1102", "C-BLUE", date(2026, 8, 5), date(2026, 9, 19), _m("25000")),
        Invoice("INV-2001", "C-NORTH", date(2026, 8, 25), date(2026, 9, 24), _m("22000")),
        Invoice("INV-2002", "C-HELIOS", date(2026, 9, 1), date(2026, 10, 1), _m("30000")),
        Invoice("INV-2003", "C-BLUE", date(2026, 9, 5), date(2026, 10, 20), _m("16000")),
        Invoice("INV-2004", "C-ORION", date(2026, 9, 10), date(2026, 10, 10), _m("8000")),
        Invoice("INV-2005", "C-NORTH", date(2026, 9, 15), date(2026, 10, 15), _m("5000")),
        Invoice("INV-2006", "C-HELIOS", date(2026, 9, 18), date(2026, 10, 18), _m("7000")),
        # Milestone billing due inside the forecast window, with no promise.
        Invoice("INV-2007", "C-BLUE", date(2026, 9, 20), date(2026, 10, 4), _m("10000")),
    )
    receipts = (
        AppliedReceipt("RCT-1004", "INV-1004", date(2026, 8, 1), _m("5000")),
        AppliedReceipt("RCT-1101", "INV-1101", date(2026, 8, 15), _m("40000")),
        AppliedReceipt("RCT-1102", "INV-1102", date(2026, 8, 20), _m("25000")),
        AppliedReceipt("RCT-1002", "INV-1002", date(2026, 9, 1), _m("12000")),
        AppliedReceipt("RCT-1006", "INV-1006", date(2026, 9, 10), _m("18000")),
    )
    credit_memos = (
        CreditMemo("CM-1003", "INV-1003", date(2026, 9, 12), _m("2000")),
    )
    promises = (
        Promise("PRM-1005", "INV-1005", "C-ORION", date(2026, 8, 1), _m("12000"), "broken"),
        Promise("PRM-1006", "INV-1006", "C-NORTH", date(2026, 9, 10), _m("18000"), "kept"),
        Promise("PRM-1007", "INV-1007", "C-HELIOS", date(2026, 9, 15), _m("9000"), "broken"),
        Promise("PRM-1102", "INV-1102", "C-BLUE", date(2026, 8, 20), _m("25000"), "kept"),
        Promise("PRM-2001", "INV-2001", "C-NORTH", date(2026, 9, 24), _m("22000"), "open"),
        Promise("PRM-2002", "INV-2002", "C-HELIOS", date(2026, 10, 1), _m("15000"), "open"),
        Promise("PRM-2004", "INV-2004", "C-ORION", date(2026, 10, 5), _m("8000"), "open"),
        Promise("PRM-2005", "INV-2005", "C-NORTH", date(2026, 9, 28), _m("5000"), "open"),
    )
    actions = (
        PendingAction(
            "ACT-SR-1003",
            "soft_reminder",
            "C-HELIOS",
            "INV-1003",
            _m("13000"),
            "Ava Chen",
            date(2026, 9, 8),
            "Balance remains after the posted $2,000 credit memo. The reminder waits on the collector.",
        ),
        PendingAction(
            "ACT-CH-ORION",
            "credit_hold",
            "C-ORION",
            None,
            _m("20000"),
            "Marcus Hale",
            date(2026, 9, 12),
            "Balance is in the 91+ bucket and a promise was broken. The agent will not place the hold until a person confirms it.",
        ),
        PendingAction(
            "ACT-UA-ORION",
            "unapplied_cash",
            "C-ORION",
            None,
            _m("1200"),
            "Marcus Hale",
            date(2026, 9, 15),
            "Receipt with no remittance advice. It is not applied, so it is not in AR or in the cash forecast.",
        ),
        PendingAction(
            "ACT-CM-2006",
            "credit_memo",
            "C-HELIOS",
            "INV-2006",
            _m("700"),
            "Priya Shah",
            date(2026, 9, 18),
            "Short-shipment claim. The draft credit memo is not posted and does not move DSO.",
        ),
        PendingAction(
            "ACT-SR-1002",
            "soft_reminder",
            "C-NORTH",
            "INV-1002",
            _m("8000"),
            "Ava Chen",
            date(2026, 9, 20),
            "53 days past due after a partial payment. The soft reminder waits on the collector.",
        ),
        PendingAction(
            "ACT-UA-BLUE",
            "unapplied_cash",
            "C-BLUE",
            None,
            _m("4500"),
            "Priya Shah",
            date(2026, 9, 21),
            "Receipt on 21 September has no invoice match. A person has to point it at an invoice.",
        ),
    )
    portfolio = Portfolio(
        as_of=AS_OF,
        customers=customers,
        invoices=invoices,
        receipts=receipts,
        credit_memos=credit_memos,
        promises=promises,
        actions=actions,
    )
    _validate(portfolio)
    return portfolio


def _validate(portfolio: Portfolio) -> None:
    customer_ids = {customer.id for customer in portfolio.customers}
    if len(customer_ids) != len(portfolio.customers):
        raise ValueError("duplicate customer id")
    invoice_ids = {invoice.id for invoice in portfolio.invoices}
    if len(invoice_ids) != len(portfolio.invoices):
        raise ValueError("duplicate invoice id")

    for invoice in portfolio.invoices:
        if invoice.customer_id not in customer_ids:
            raise ValueError(f"{invoice.id} references unknown customer")
        if invoice.due_on < invoice.issued_on:
            raise ValueError(f"{invoice.id} is due before it was issued")
        if invoice.amount <= 0:
            raise ValueError(f"{invoice.id} amount must be positive")

    settled: dict[str, Decimal] = {invoice.id: Decimal("0.00") for invoice in portfolio.invoices}
    for receipt in portfolio.receipts:
        if receipt.invoice_id not in invoice_ids:
            raise ValueError(f"{receipt.id} references unknown invoice")
        if receipt.amount <= 0:
            raise ValueError(f"{receipt.id} amount must be positive")
        settled[receipt.invoice_id] += receipt.amount
    for memo in portfolio.credit_memos:
        if memo.invoice_id not in invoice_ids:
            raise ValueError(f"{memo.id} references unknown invoice")
        if memo.amount <= 0:
            raise ValueError(f"{memo.id} amount must be positive")
        settled[memo.invoice_id] += memo.amount
    for invoice in portfolio.invoices:
        if settled[invoice.id] > invoice.amount:
            raise ValueError(f"{invoice.id} is over-applied")

    for promise in portfolio.promises:
        if promise.invoice_id not in invoice_ids:
            raise ValueError(f"{promise.id} references unknown invoice")
        invoice = portfolio.invoice(promise.invoice_id)
        if promise.customer_id != invoice.customer_id:
            raise ValueError(f"{promise.id} customer does not match the invoice")
        if promise.amount <= 0 or promise.amount > invoice.amount:
            raise ValueError(f"{promise.id} amount is outside the invoice")

    action_ids = set()
    for action in portfolio.actions:
        if action.id in action_ids:
            raise ValueError(f"duplicate action {action.id}")
        action_ids.add(action.id)
        if action.customer_id not in customer_ids:
            raise ValueError(f"{action.id} references unknown customer")
        if action.invoice_id is not None and action.invoice_id not in invoice_ids:
            raise ValueError(f"{action.id} references unknown invoice")
        if action.amount < 0:
            raise ValueError(f"{action.id} amount cannot be negative")
        if not action.owner.strip():
            raise ValueError(f"{action.id} needs an owner")
        if action.created_on > portfolio.as_of:
            raise ValueError(f"{action.id} is created after the as-of date")
