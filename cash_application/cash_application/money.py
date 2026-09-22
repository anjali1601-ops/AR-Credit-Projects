"""Cents-exact money. Matching never uses binary floats."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: object) -> Decimal:
    """Quantize to cents. Accepts Decimal, int, or a numeric string."""
    if isinstance(value, Decimal):
        amount = value
    else:
        text = str(value).strip().replace("$", "").replace(",", "")
        if not text:
            raise ValueError("missing amount")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"not a money amount: {value!r}") from exc
    return amount.quantize(TWO_PLACES)


def money_eq(left: object, right: object) -> bool:
    return money(left) == money(right)


def format_money(value: object) -> str:
    amount = money(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"
