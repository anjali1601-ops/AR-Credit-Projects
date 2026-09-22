"""Trailing 30-day DSO and an additive bridge of what moved it.

    DSO = open AR × 30 / gross billings in the 30-day window

Beginning DSO uses AR at the prior close and billings in the prior window.
Ending DSO uses AR at the as-of date and billings in the current window.

The bridge holds the current window's sales as the base:

    sales effect      = beginning AR × 30 / current sales − beginning DSO
    billing effect    = billings × 30 / current sales
    collection effect = −collections × 30 / current sales
    credit-memo effect = −posted credit memos × 30 / current sales
    other effect      = other × 30 / current sales

Those parts sum exactly to ending DSO, because ending AR equals beginning AR
plus billings minus collections minus posted credit memos plus other.
Sales in the denominator are gross billings, not billings net of credit memos.
This seed has no write-offs, so other is zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from o2c_control_tower.format import quantize_ratio
from o2c_control_tower.models import Portfolio
from o2c_control_tower.portfolio import billings, collections, open_ar, posted_credit_memos

PERIOD_DAYS = 30


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date

    @property
    def inclusive_days(self) -> int:
        return (self.end - self.start).days + 1


def windows(as_of: date) -> tuple[Window, Window]:
    current_end = as_of
    current_start = as_of - timedelta(days=PERIOD_DAYS - 1)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=PERIOD_DAYS - 1)
    current = Window(current_start, current_end)
    prior = Window(prior_start, prior_end)
    if current.inclusive_days != PERIOD_DAYS or prior.inclusive_days != PERIOD_DAYS:
        raise ValueError("DSO windows must each cover 30 inclusive days")
    return current, prior


@dataclass(frozen=True, slots=True)
class DsoBridge:
    days: int
    current: Window
    prior: Window
    beginning_ar: Decimal
    ending_ar: Decimal
    prior_sales: Decimal
    current_sales: Decimal
    billings: Decimal
    collections: Decimal
    credit_memos: Decimal
    other: Decimal
    beginning_dso: Fraction
    ending_dso: Fraction
    sales_effect: Fraction
    billing_effect: Fraction
    collection_effect: Fraction
    credit_memo_effect: Fraction
    other_effect: Fraction

    def effects(self) -> tuple[tuple[str, str, Fraction], ...]:
        return (
            ("sales", "Sales volume", self.sales_effect),
            ("billings", "Billings", self.billing_effect),
            ("collections", "Collections", self.collection_effect),
            ("credit_memos", "Credit memos", self.credit_memo_effect),
            ("other", "Other adjustments", self.other_effect),
        )

    def ties(self) -> bool:
        total = self.beginning_dso + sum((effect for _, _, effect in self.effects()), start=Fraction(0))
        return total == self.ending_dso


def _ratio(amount: Decimal, days: int, sales: Decimal) -> Fraction:
    if sales == 0:
        raise ValueError("DSO sales denominator is zero")
    return Fraction(amount) * days / Fraction(sales)


def build_dso(portfolio: Portfolio) -> DsoBridge:
    current, prior = windows(portfolio.as_of)
    beginning = open_ar(portfolio, prior.end)
    ending = open_ar(portfolio, current.end)
    prior_sales = billings(portfolio, prior.start, prior.end)
    current_sales = billings(portfolio, current.start, current.end)
    billed = current_sales
    collected = collections(portfolio, current.start, current.end)
    memos = posted_credit_memos(portfolio, current.start, current.end)
    other = Decimal("0.00")
    rollforward = beginning + billed - collected - memos + other
    if rollforward != ending:
        raise ValueError(
            f"AR rollforward {rollforward} does not equal ending AR {ending}"
        )

    beginning_dso = _ratio(beginning, PERIOD_DAYS, prior_sales)
    ending_dso = _ratio(ending, PERIOD_DAYS, current_sales)
    sales_effect = _ratio(beginning, PERIOD_DAYS, current_sales) - beginning_dso
    bridge = DsoBridge(
        days=PERIOD_DAYS,
        current=current,
        prior=prior,
        beginning_ar=beginning,
        ending_ar=ending,
        prior_sales=prior_sales,
        current_sales=current_sales,
        billings=billed,
        collections=collected,
        credit_memos=memos,
        other=other,
        beginning_dso=beginning_dso,
        ending_dso=ending_dso,
        sales_effect=sales_effect,
        billing_effect=_ratio(billed, PERIOD_DAYS, current_sales),
        collection_effect=-_ratio(collected, PERIOD_DAYS, current_sales),
        credit_memo_effect=-_ratio(memos, PERIOD_DAYS, current_sales),
        other_effect=_ratio(other, PERIOD_DAYS, current_sales),
    )
    if not bridge.ties():
        raise ValueError("DSO bridge does not tie to ending DSO")
    # Touch the quantizer so a broken rounding context fails at build time.
    quantize_ratio(bridge.ending_dso)
    return bridge
