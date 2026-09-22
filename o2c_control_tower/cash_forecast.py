"""Cash expected on each of the next 14 days.

An open invoice contributes through exactly one path:

1. Open promise dated inside the window: promised amount (capped at the open
   balance) times confidence. Confidence is 0.50 when that customer has any
   broken promise, otherwise 0.90. The uncovered remainder is not also forecast.
2. Otherwise, if the invoice is not past due and the due date is inside the
   window: 80% of the open balance, on the due date.
3. Otherwise, if the invoice is past due: a bucket rate on a fixed day inside
   the window (1–30 at 35% on day 3, 31–60 at 15% on day 7, 61–90 at 8% on
   day 10, 91+ at 3% on day 14).

Kept and broken promises are history. They change confidence and workload,
and they do not themselves drop cash into the forward window. Unapplied cash
is already received, so it is not forecast again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from o2c_control_tower.aging import bucket_for
from o2c_control_tower.format import TWOPLACES
from o2c_control_tower.models import Portfolio
from o2c_control_tower.portfolio import OpenItem, open_items

FORECAST_DAYS = 14
ON_TIME_RATE = Decimal("0.80")
PROMISE_CONFIDENCE_CLEAN = Decimal("0.90")
PROMISE_CONFIDENCE_BROKEN = Decimal("0.50")
PAST_DUE_CURVE: dict[str, tuple[Decimal, int]] = {
    "1_30": (Decimal("0.35"), 3),
    "31_60": (Decimal("0.15"), 7),
    "61_90": (Decimal("0.08"), 10),
    "91_plus": (Decimal("0.03"), 14),
}


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class CashLine:
    expected_on: date
    invoice_id: str
    customer_id: str
    customer_name: str
    source: str
    amount: Decimal
    detail: str


@dataclass(frozen=True, slots=True)
class CashDay:
    day: date
    amount: Decimal
    lines: tuple[CashLine, ...]


@dataclass(frozen=True, slots=True)
class CashForecast:
    start: date
    end: date
    days: tuple[CashDay, ...]
    lines: tuple[CashLine, ...]

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), start=Decimal("0.00"))


def _window(as_of: date) -> tuple[date, date]:
    return as_of + timedelta(days=1), as_of + timedelta(days=FORECAST_DAYS)


def _broken_customers(portfolio: Portfolio) -> set[str]:
    return {
        promise.customer_id
        for promise in portfolio.promises
        if promise.status == "broken"
    }


def _line_for(portfolio: Portfolio, item: OpenItem, broken: set[str]) -> CashLine | None:
    start, end = _window(portfolio.as_of)
    customer = portfolio.customer(item.customer_id)
    in_window = [
        promise
        for promise in portfolio.promises
        if promise.invoice_id == item.invoice_id
        and promise.status == "open"
        and start <= promise.promised_on <= end
    ]
    if in_window:
        promised = min(
            sum((promise.amount for promise in in_window), start=Decimal("0.00")),
            item.open_amount,
        )
        confidence = (
            PROMISE_CONFIDENCE_BROKEN
            if item.customer_id in broken
            else PROMISE_CONFIDENCE_CLEAN
        )
        # One date: the earliest open promise in the window. This seed has one.
        expected_on = min(promise.promised_on for promise in in_window)
        rate_label = f"{int(confidence * 100)}%"
        broken_note = " Customer has a broken promise." if item.customer_id in broken else ""
        return CashLine(
            expected_on=expected_on,
            invoice_id=item.invoice_id,
            customer_id=item.customer_id,
            customer_name=customer.name,
            source="promise",
            amount=_cents(promised * confidence),
            detail=f"Open promise, {rate_label} confidence.{broken_note}",
        )

    if item.days_past_due <= 0 and start <= item.invoice.due_on <= end:
        return CashLine(
            expected_on=item.invoice.due_on,
            invoice_id=item.invoice_id,
            customer_id=item.customer_id,
            customer_name=customer.name,
            source="due_date",
            amount=_cents(item.open_amount * ON_TIME_RATE),
            detail="Due inside 14 days with no promise. 80% of the open balance.",
        )

    if item.days_past_due > 0:
        bucket = bucket_for(item.days_past_due)
        rate, offset = PAST_DUE_CURVE[bucket]
        return CashLine(
            expected_on=portfolio.as_of + timedelta(days=offset),
            invoice_id=item.invoice_id,
            customer_id=item.customer_id,
            customer_name=customer.name,
            source="past_due",
            amount=_cents(item.open_amount * rate),
            detail=(
                f"Past due {_bucket_phrase(bucket)}. "
                f"{int(rate * 100)}% expected inside 14 days."
            ),
        )
    return None


def _bucket_phrase(bucket: str) -> str:
    return {
        "1_30": "1–30",
        "31_60": "31–60",
        "61_90": "61–90",
        "91_plus": "91+",
    }[bucket]


def build_cash_forecast(portfolio: Portfolio) -> CashForecast:
    start, end = _window(portfolio.as_of)
    broken = _broken_customers(portfolio)
    lines: list[CashLine] = []
    for item in open_items(portfolio, portfolio.as_of):
        line = _line_for(portfolio, item, broken)
        if line is None or line.amount == 0:
            continue
        if not start <= line.expected_on <= end:
            raise ValueError(f"{line.invoice_id} cash date falls outside the 14-day window")
        lines.append(line)
    lines.sort(key=lambda line: (line.expected_on, line.invoice_id))

    days: list[CashDay] = []
    cursor = start
    while cursor <= end:
        day_lines = tuple(line for line in lines if line.expected_on == cursor)
        days.append(
            CashDay(
                day=cursor,
                amount=sum((line.amount for line in day_lines), start=Decimal("0.00")),
                lines=day_lines,
            )
        )
        cursor += timedelta(days=1)
    if len(days) != FORECAST_DAYS:
        raise ValueError("cash forecast must cover 14 days")
    return CashForecast(start=start, end=end, days=tuple(days), lines=tuple(lines))
