"""Assemble the director snapshot from the seeded portfolio."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from o2c_control_tower.aging import AgingReport, build_aging
from o2c_control_tower.cash_forecast import CashForecast, build_cash_forecast
from o2c_control_tower.dso import DsoBridge, build_dso
from o2c_control_tower.models import Portfolio
from o2c_control_tower.narrative import write_narrative
from o2c_control_tower.seed import load_portfolio
from o2c_control_tower.workload import (
    CollectorLoad,
    WaitingAction,
    collector_workload,
    unapplied_cash,
    waiting_actions,
)


@dataclass(frozen=True, slots=True)
class ControlTower:
    as_of: date
    narrative: str
    aging: AgingReport
    dso: DsoBridge
    cash: CashForecast
    collectors: tuple[CollectorLoad, ...]
    actions: tuple[WaitingAction, ...]
    open_ar: Decimal
    unapplied_cash: Decimal

    @property
    def outside_collectors(self) -> tuple[str, ...]:
        names = {load.collector for load in self.collectors}
        return tuple(sorted({action.owner for action in self.actions if action.owner not in names}))


def build_control_tower(portfolio: Portfolio | None = None) -> ControlTower:
    book = load_portfolio() if portfolio is None else portfolio
    aging = build_aging(book)
    dso = build_dso(book)
    cash = build_cash_forecast(book)
    actions = waiting_actions(book)
    collectors = collector_workload(book, actions)
    if aging.total != dso.ending_ar:
        raise ValueError("aging total does not equal ending AR")
    return ControlTower(
        as_of=book.as_of,
        narrative=write_narrative(
            as_of=book.as_of,
            open_ar=dso.ending_ar,
            dso=dso,
            predicted_cash=cash.total,
            actions=actions,
        ),
        aging=aging,
        dso=dso,
        cash=cash,
        collectors=collectors,
        actions=actions,
        open_ar=dso.ending_ar,
        unapplied_cash=unapplied_cash(actions),
    )
