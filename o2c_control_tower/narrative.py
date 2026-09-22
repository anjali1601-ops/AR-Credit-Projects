"""Offline narrative.

This is a fixed phrasing model. Every figure is passed in from the calculation
layer. The same inputs always produce the same paragraph, and the model never
chooses a number.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

from o2c_control_tower.cash_forecast import FORECAST_DAYS
from o2c_control_tower.dso import DsoBridge
from o2c_control_tower.format import format_days, format_money, long_date
from o2c_control_tower.workload import WaitingAction


def _join(parts: list[str]) -> str:
    if not parts:
        return "No measured driver moved DSO"
    titled = [parts[0][0].upper() + parts[0][1:], *parts[1:]]
    if len(titled) == 1:
        return titled[0]
    return ", ".join(titled[:-1]) + f", and {titled[-1]}"


def _driver_clause(label: str, effect: Fraction) -> str:
    days = format_days(abs(effect), places=2)
    verb = "added" if effect > 0 else "removed"
    return f"{label.lower()} {verb} {days} days"


def write_narrative(
    *,
    as_of: date,
    open_ar: Decimal,
    dso: DsoBridge,
    predicted_cash: Decimal,
    actions: tuple[WaitingAction, ...],
) -> str:
    ranked = sorted(dso.effects(), key=lambda item: (-abs(item[2]), item[0]))
    clauses = [_driver_clause(label, effect) for _, label, effect in ranked if effect != 0]
    if not actions:
        queue = "No actions are waiting on a person."
    else:
        oldest = max(actions, key=lambda action: (action.age_days, action.id))
        noun = "action is" if len(actions) == 1 else "actions are"
        day_word = "day" if oldest.age_days == 1 else "days"
        queue = (
            f"{len(actions)} {noun} waiting on a person. "
            f"The oldest has been open {oldest.age_days} {day_word}: "
            f"{oldest.label.lower()}, {oldest.customer_name}, owner {oldest.owner}."
        )
    return (
        f"As of {long_date(as_of)}, open receivables are {format_money(open_ar)}. "
        f"Trailing {dso.days}-day DSO is {format_days(dso.ending_dso, 2)} days, "
        f"compared with {format_days(dso.beginning_dso, 2)} days at the start of the window. "
        f"{_join(clauses)}. "
        f"Predicted cash over the next {FORECAST_DAYS} days is {format_money(predicted_cash)}. "
        f"{queue}"
    )
