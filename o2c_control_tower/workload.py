"""Collector workload on the open book, plus the age of every waiting action."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from o2c_control_tower.aging import build_aging
from o2c_control_tower.models import ACTION_LABELS, PendingAction, Portfolio


@dataclass(frozen=True, slots=True)
class WaitingAction:
    id: str
    action_type: str
    label: str
    customer_id: str
    customer_name: str
    invoice_id: str | None
    amount: Decimal
    owner: str
    created_on: str
    age_days: int
    reason: str


@dataclass(frozen=True, slots=True)
class CollectorLoad:
    collector: str
    customers: tuple[str, ...]
    open_invoices: int
    open_ar: Decimal
    past_due_invoices: int
    past_due_ar: Decimal
    broken_promises: int
    waiting_actions: int
    workload_score: int


def action_age(action: PendingAction, portfolio: Portfolio) -> int:
    return (portfolio.as_of - action.created_on).days


def waiting_actions(portfolio: Portfolio) -> tuple[WaitingAction, ...]:
    rows: list[WaitingAction] = []
    for action in portfolio.actions:
        customer = portfolio.customer(action.customer_id)
        rows.append(
            WaitingAction(
                id=action.id,
                action_type=action.action_type,
                label=ACTION_LABELS[action.action_type],
                customer_id=action.customer_id,
                customer_name=customer.name,
                invoice_id=action.invoice_id,
                amount=action.amount,
                owner=action.owner,
                created_on=action.created_on.isoformat(),
                age_days=action_age(action, portfolio),
                reason=action.reason,
            )
        )
    rows.sort(key=lambda row: (-row.age_days, row.id))
    return tuple(rows)


def _broken_count(portfolio: Portfolio, customer_ids: set[str]) -> int:
    return sum(
        1
        for promise in portfolio.promises
        if promise.status == "broken" and promise.customer_id in customer_ids
    )


def collector_workload(
    portfolio: Portfolio,
    actions: tuple[WaitingAction, ...],
) -> tuple[CollectorLoad, ...]:
    aging = build_aging(portfolio)
    collectors = sorted({customer.collector for customer in portfolio.customers})
    loads: list[CollectorLoad] = []
    for collector in collectors:
        customers = tuple(
            sorted(
                customer.name
                for customer in portfolio.customers
                if customer.collector == collector
            )
        )
        customer_ids = {
            customer.id
            for customer in portfolio.customers
            if customer.collector == collector
        }
        invoices = [row for row in aging.invoices if row.customer_id in customer_ids]
        past_due = [row for row in invoices if row.days_past_due > 0]
        owned = [action for action in actions if action.owner == collector]
        open_count = len(invoices)
        past_due_count = len(past_due)
        broken = _broken_count(portfolio, customer_ids)
        waiting = len(owned)
        score = open_count + (2 * past_due_count) + (3 * broken) + waiting
        loads.append(
            CollectorLoad(
                collector=collector,
                customers=customers,
                open_invoices=open_count,
                open_ar=sum((row.open_amount for row in invoices), start=Decimal("0.00")),
                past_due_invoices=past_due_count,
                past_due_ar=sum((row.open_amount for row in past_due), start=Decimal("0.00")),
                broken_promises=broken,
                waiting_actions=waiting,
                workload_score=score,
            )
        )
    loads.sort(key=lambda load: (-load.workload_score, load.collector))
    return tuple(loads)


def unapplied_cash(actions: tuple[WaitingAction, ...]) -> Decimal:
    return sum(
        (action.amount for action in actions if action.action_type == "unapplied_cash"),
        start=Decimal("0.00"),
    )
