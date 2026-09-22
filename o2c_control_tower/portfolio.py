"""Open-item balances derived from invoices, applied cash, and posted memos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from o2c_control_tower.models import Invoice, Portfolio


@dataclass(frozen=True, slots=True)
class OpenItem:
    invoice: Invoice
    open_amount: Decimal
    days_past_due: int

    @property
    def invoice_id(self) -> str:
        return self.invoice.id

    @property
    def customer_id(self) -> str:
        return self.invoice.customer_id


def reductions_through(portfolio: Portfolio, invoice_id: str, as_of: date) -> Decimal:
    paid = sum(
        (
            receipt.amount
            for receipt in portfolio.receipts
            if receipt.invoice_id == invoice_id and receipt.paid_on <= as_of
        ),
        start=Decimal("0.00"),
    )
    memos = sum(
        (
            memo.amount
            for memo in portfolio.credit_memos
            if memo.invoice_id == invoice_id and memo.issued_on <= as_of
        ),
        start=Decimal("0.00"),
    )
    return paid + memos


def open_amount(portfolio: Portfolio, invoice: Invoice, as_of: date) -> Decimal:
    if invoice.issued_on > as_of:
        return Decimal("0.00")
    remaining = invoice.amount - reductions_through(portfolio, invoice.id, as_of)
    if remaining < 0:
        raise ValueError(f"{invoice.id} is negative on {as_of.isoformat()}")
    return remaining


def open_items(portfolio: Portfolio, as_of: date) -> tuple[OpenItem, ...]:
    items: list[OpenItem] = []
    for invoice in portfolio.invoices:
        remaining = open_amount(portfolio, invoice, as_of)
        if remaining == 0:
            continue
        items.append(
            OpenItem(
                invoice=invoice,
                open_amount=remaining,
                days_past_due=(as_of - invoice.due_on).days,
            )
        )
    items.sort(key=lambda item: (-item.days_past_due, item.invoice_id))
    return tuple(items)


def open_ar(portfolio: Portfolio, as_of: date) -> Decimal:
    return sum(
        (item.open_amount for item in open_items(portfolio, as_of)),
        start=Decimal("0.00"),
    )


def billings(portfolio: Portfolio, start: date, end: date) -> Decimal:
    return sum(
        (
            invoice.amount
            for invoice in portfolio.invoices
            if start <= invoice.issued_on <= end
        ),
        start=Decimal("0.00"),
    )


def invoices_issued(portfolio: Portfolio, start: date, end: date) -> tuple[Invoice, ...]:
    return tuple(
        invoice
        for invoice in portfolio.invoices
        if start <= invoice.issued_on <= end
    )


def collections(portfolio: Portfolio, start: date, end: date) -> Decimal:
    return sum(
        (
            receipt.amount
            for receipt in portfolio.receipts
            if start <= receipt.paid_on <= end
        ),
        start=Decimal("0.00"),
    )


def posted_credit_memos(portfolio: Portfolio, start: date, end: date) -> Decimal:
    return sum(
        (memo.amount for memo in portfolio.credit_memos if start <= memo.issued_on <= end),
        start=Decimal("0.00"),
    )
