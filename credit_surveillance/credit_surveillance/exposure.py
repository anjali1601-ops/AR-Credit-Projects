"""Exposure, payment-drift, and broken-promise math.

Every figure the policy uses is produced here. The narrator never recomputes it.
"""

from datetime import date, timedelta
from decimal import Decimal

from credit_surveillance.formatting import q_days, q_money, q_ratio
from credit_surveillance.models import ExposureFacts, Invoice, PromiseToPay

AS_OF = date(2026, 9, 22)
RECENT_WINDOW_DAYS = 90
# Drift above this many days is a watch signal. The affirm rule uses the same band.
DRIFT_SIGNAL_DAYS = Decimal("5.00")


def compute_exposure(
    *,
    credit_limit: Decimal,
    terms_days: int,
    invoices: list[Invoice] | tuple[Invoice, ...],
    promises: list[PromiseToPay] | tuple[PromiseToPay, ...],
    open_order_amounts: list[Decimal] | tuple[Decimal, ...],
    as_of: date = AS_OF,
    recent_window_days: int = RECENT_WINDOW_DAYS,
) -> ExposureFacts:
    """Compute the surveillance snapshot for one open account."""
    if credit_limit <= 0:
        raise ValueError("credit_limit must be positive")
    if terms_days < 1:
        raise ValueError("terms_days must be positive")
    if recent_window_days < 1:
        raise ValueError("recent_window_days must be positive")

    limit = q_money(credit_limit)
    unpaid = []
    paid = []
    for invoice in invoices:
        if invoice.amount < 0:
            raise ValueError(f"invoice {invoice.id} amount cannot be negative")
        if invoice.paid_date is not None and invoice.paid_date < invoice.invoice_date:
            raise ValueError(f"invoice {invoice.id} paid_date precedes invoice_date")
        if invoice.paid_date is None:
            unpaid.append(invoice)
        elif invoice.paid_date <= as_of:
            paid.append(invoice)

    accounts_receivable = q_money(sum((invoice.amount for invoice in unpaid), Decimal("0")))
    past_due = [invoice for invoice in unpaid if invoice.due_date < as_of]
    past_due_ar = q_money(sum((invoice.amount for invoice in past_due), Decimal("0")))
    current_ar = q_money(accounts_receivable - past_due_ar)

    order_total = Decimal("0")
    for amount in open_order_amounts:
        if amount < 0:
            raise ValueError("open order amount cannot be negative")
        order_total += amount
    open_orders = q_money(order_total)

    exposure = q_money(accounts_receivable + open_orders)
    over_limit_amount = q_money(max(Decimal("0"), exposure - limit))
    utilization = q_ratio(exposure / limit)
    if accounts_receivable == 0:
        past_due_ratio = q_ratio(Decimal("0"))
    else:
        past_due_ratio = q_ratio(past_due_ar / accounts_receivable)

    recent_cutoff = as_of - timedelta(days=recent_window_days)
    recent = [invoice for invoice in paid if invoice.paid_date >= recent_cutoff]
    baseline = [invoice for invoice in paid if invoice.paid_date < recent_cutoff]
    avg_recent = _avg_days_to_pay(recent)
    avg_baseline = _avg_days_to_pay(baseline)
    payment_drift_days = q_days(avg_recent - avg_baseline)

    invoices_paid_recent = len(recent)
    invoices_late_recent = sum(
        1 for invoice in recent if _days_to_pay(invoice) > terms_days
    )
    if invoices_paid_recent == 0:
        late_payment_rate = q_ratio(Decimal("0"))
    else:
        late_payment_rate = q_ratio(
            Decimal(invoices_late_recent) / Decimal(invoices_paid_recent)
        )

    broken = [promise for promise in promises if _is_broken(promise, as_of)]
    for promise in promises:
        if promise.amount < 0:
            raise ValueError(f"promise {promise.id} amount cannot be negative")
    broken_promise_amount = q_money(sum((promise.amount for promise in broken), Decimal("0")))
    max_days_past_due = (
        max((as_of - invoice.due_date).days for invoice in past_due) if past_due else 0
    )

    signals: list[str] = []
    if payment_drift_days > DRIFT_SIGNAL_DAYS:
        signals.append("payment_drift")
    if over_limit_amount > 0:
        signals.append("over_limit")
    if len(broken) > 0:
        signals.append("broken_promises")

    return ExposureFacts(
        as_of=as_of,
        credit_limit=limit,
        accounts_receivable=accounts_receivable,
        current_ar=current_ar,
        past_due_ar=past_due_ar,
        open_orders=open_orders,
        exposure=exposure,
        over_limit_amount=over_limit_amount,
        utilization=utilization,
        past_due_ratio=past_due_ratio,
        avg_days_to_pay_recent=avg_recent,
        avg_days_to_pay_baseline=avg_baseline,
        payment_drift_days=payment_drift_days,
        terms_days=terms_days,
        late_payment_rate=late_payment_rate,
        invoices_paid_recent=invoices_paid_recent,
        invoices_late_recent=invoices_late_recent,
        broken_promise_count=len(broken),
        broken_promise_amount=broken_promise_amount,
        max_days_past_due=max_days_past_due,
        signals=tuple(signals),
    )


def _days_to_pay(invoice: Invoice) -> int:
    assert invoice.paid_date is not None
    return (invoice.paid_date - invoice.invoice_date).days


def _avg_days_to_pay(invoices: list[Invoice]) -> Decimal:
    if not invoices:
        return q_days(Decimal("0"))
    total = sum(_days_to_pay(invoice) for invoice in invoices)
    return q_days(Decimal(total) / Decimal(len(invoices)))


def _is_broken(promise: PromiseToPay, as_of: date) -> bool:
    if promise.status == "kept":
        return False
    if promise.status == "broken":
        return True
    return promise.status == "open" and promise.promised_date < as_of
