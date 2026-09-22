"""AR aging from days past due on the as-of date.

Days past due = as-of date minus due date. Zero or negative is current
(due today is not past due). Buckets are 1–30, 31–60, 61–90, and 91+.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from o2c_control_tower.models import Portfolio
from o2c_control_tower.portfolio import OpenItem, open_items

BUCKET_ORDER = ("current", "1_30", "31_60", "61_90", "91_plus")

BUCKET_LABELS = {
    "current": "Current",
    "1_30": "1–30",
    "31_60": "31–60",
    "61_90": "61–90",
    "91_plus": "91+",
}


def bucket_for(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1_30"
    if days_past_due <= 60:
        return "31_60"
    if days_past_due <= 90:
        return "61_90"
    return "91_plus"


@dataclass(frozen=True, slots=True)
class AgingBucket:
    key: str
    label: str
    amount: Decimal
    invoice_count: int

@dataclass(frozen=True, slots=True)
class AgedInvoice:
    invoice_id: str
    customer_id: str
    customer_name: str
    collector: str
    issued_on: str
    due_on: str
    amount: Decimal
    open_amount: Decimal
    days_past_due: int
    bucket: str


@dataclass(frozen=True, slots=True)
class AgingReport:
    buckets: tuple[AgingBucket, ...]
    invoices: tuple[AgedInvoice, ...]

    @property
    def total(self) -> Decimal:
        return sum((bucket.amount for bucket in self.buckets), start=Decimal("0.00"))

    def amount(self, key: str) -> Decimal:
        for bucket in self.buckets:
            if bucket.key == key:
                return bucket.amount
        raise KeyError(key)

    def count(self, key: str) -> int:
        for bucket in self.buckets:
            if bucket.key == key:
                return bucket.invoice_count
        raise KeyError(key)


def _aged(portfolio: Portfolio, item: OpenItem) -> AgedInvoice:
    customer = portfolio.customer(item.customer_id)
    return AgedInvoice(
        invoice_id=item.invoice_id,
        customer_id=item.customer_id,
        customer_name=customer.name,
        collector=customer.collector,
        issued_on=item.invoice.issued_on.isoformat(),
        due_on=item.invoice.due_on.isoformat(),
        amount=item.invoice.amount,
        open_amount=item.open_amount,
        days_past_due=item.days_past_due,
        bucket=bucket_for(item.days_past_due),
    )


def build_aging(portfolio: Portfolio) -> AgingReport:
    aged = tuple(_aged(portfolio, item) for item in open_items(portfolio, portfolio.as_of))
    buckets: list[AgingBucket] = []
    for key in BUCKET_ORDER:
        rows = [row for row in aged if row.bucket == key]
        buckets.append(
            AgingBucket(
                key=key,
                label=BUCKET_LABELS[key],
                amount=sum((row.open_amount for row in rows), start=Decimal("0.00")),
                invoice_count=len(rows),
            )
        )
    return AgingReport(buckets=tuple(buckets), invoices=aged)
