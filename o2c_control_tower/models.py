"""Portfolio records. Amounts are decimal dollars, never floats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

ActionType = Literal["credit_memo", "soft_reminder", "credit_hold", "unapplied_cash"]
PromiseStatus = Literal["open", "broken", "kept"]

ACTION_LABELS: dict[ActionType, str] = {
    "credit_memo": "Credit memo",
    "soft_reminder": "Soft reminder",
    "credit_hold": "Credit hold",
    "unapplied_cash": "Unapplied cash",
}


@dataclass(frozen=True, slots=True)
class Customer:
    id: str
    name: str
    collector: str


@dataclass(frozen=True, slots=True)
class Invoice:
    id: str
    customer_id: str
    issued_on: date
    due_on: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AppliedReceipt:
    """Cash applied to one invoice. Unapplied cash is a pending action, not a receipt."""

    id: str
    invoice_id: str
    paid_on: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CreditMemo:
    """Posted credit memo. A memo still waiting on a human is a pending action."""

    id: str
    invoice_id: str
    issued_on: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Promise:
    id: str
    invoice_id: str
    customer_id: str
    promised_on: date
    amount: Decimal
    status: PromiseStatus


@dataclass(frozen=True, slots=True)
class PendingAction:
    id: str
    action_type: ActionType
    customer_id: str
    invoice_id: str | None
    amount: Decimal
    owner: str
    created_on: date
    reason: str


@dataclass(frozen=True, slots=True)
class Portfolio:
    as_of: date
    customers: tuple[Customer, ...]
    invoices: tuple[Invoice, ...]
    receipts: tuple[AppliedReceipt, ...]
    credit_memos: tuple[CreditMemo, ...]
    promises: tuple[Promise, ...]
    actions: tuple[PendingAction, ...]

    def customer(self, customer_id: str) -> Customer:
        for customer in self.customers:
            if customer.id == customer_id:
                return customer
        raise KeyError(customer_id)

    def invoice(self, invoice_id: str) -> Invoice:
        for invoice in self.invoices:
            if invoice.id == invoice_id:
                return invoice
        raise KeyError(invoice_id)
