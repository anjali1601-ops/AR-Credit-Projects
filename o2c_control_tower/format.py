"""Display helpers. Rounding is half-up so the page matches the tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

TWOPLACES = Decimal("0.01")
FOURPLACES = Decimal("0.0001")
ONEPLACE = Decimal("0.1")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def quantize_ratio(value: Fraction, quantum: Decimal = FOURPLACES) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = ROUND_HALF_UP
        as_decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return as_decimal.quantize(quantum)


def format_money(amount: Decimal) -> str:
    quantized = money(amount)
    sign = "-" if quantized < 0 else ""
    return f"{sign}${abs(quantized):,.2f}"


def format_days(value: Fraction, places: int = 4) -> str:
    quantum = Decimal("1").scaleb(-places)
    quantized = quantize_ratio(value, quantum)
    return f"{quantized:.{places}f}"


def format_percent(share: Decimal) -> str:
    return f"{(share * Decimal(100)).quantize(ONEPLACE, rounding=ROUND_HALF_UP):.1f}%"


def long_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"
