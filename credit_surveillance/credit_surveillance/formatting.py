"""Decimal quantization shared by the exposure engine and the memos."""

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
SIX_PLACES = Decimal("0.000001")


def q_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def q_ratio(value: Decimal) -> Decimal:
    return value.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


def q_days(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> str:
    return f"{q_money(value):.2f}"


def ratio(value: Decimal) -> str:
    return f"{q_ratio(value):.6f}"


def days(value: Decimal) -> str:
    return f"{q_days(value):.2f}"
