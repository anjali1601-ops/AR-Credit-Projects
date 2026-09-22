"""Hand calculation of cash from 23 September through 6 October 2026.

Promise path (replaces the due-date path for that invoice):
    INV-2001  22,000 × 0.90 = 19,800 on 24 Sep   Northwind, no broken promise
    INV-2005   5,000 × 0.90 =  4,500 on 28 Sep
    INV-2002  15,000 × 0.50 =  7,500 on 1 Oct    Helios has a broken promise
    INV-2004   8,000 × 0.50 =  4,000 on 5 Oct    Orion has a broken promise

Past due, no open promise in the window:
    INV-1007   9,000 × 0.35 =  3,150 on 25 Sep   day 3
    INV-1002   8,000 × 0.15 =  1,200 on 29 Sep   day 7
    INV-1003  13,000 × 0.15 =  1,950 on 29 Sep
    INV-1004  20,000 × 0.08 =  1,600 on 2 Oct    day 10
    INV-1005  12,000 × 0.03 =    360 on 6 Oct    day 14

Due inside the window, no promise:
    INV-2007  10,000 × 0.80 =  8,000 on 4 Oct

Not in the window: INV-2003 due 20 Oct, INV-2006 due 18 Oct.
Total 52,060.
"""

from datetime import date
from decimal import Decimal

from o2c_control_tower.cash_forecast import build_cash_forecast
from o2c_control_tower.seed import load_portfolio

EXPECTED_LINES = {
    ("2026-09-24", "INV-2001", "promise"): Decimal("19800.00"),
    ("2026-09-25", "INV-1007", "past_due"): Decimal("3150.00"),
    ("2026-09-28", "INV-2005", "promise"): Decimal("4500.00"),
    ("2026-09-29", "INV-1002", "past_due"): Decimal("1200.00"),
    ("2026-09-29", "INV-1003", "past_due"): Decimal("1950.00"),
    ("2026-10-01", "INV-2002", "promise"): Decimal("7500.00"),
    ("2026-10-02", "INV-1004", "past_due"): Decimal("1600.00"),
    ("2026-10-04", "INV-2007", "due_date"): Decimal("8000.00"),
    ("2026-10-05", "INV-2004", "promise"): Decimal("4000.00"),
    ("2026-10-06", "INV-1005", "past_due"): Decimal("360.00"),
}

EXPECTED_DAYS = {
    date(2026, 9, 23): Decimal("0.00"),
    date(2026, 9, 24): Decimal("19800.00"),
    date(2026, 9, 25): Decimal("3150.00"),
    date(2026, 9, 26): Decimal("0.00"),
    date(2026, 9, 27): Decimal("0.00"),
    date(2026, 9, 28): Decimal("4500.00"),
    date(2026, 9, 29): Decimal("3150.00"),
    date(2026, 9, 30): Decimal("0.00"),
    date(2026, 10, 1): Decimal("7500.00"),
    date(2026, 10, 2): Decimal("1600.00"),
    date(2026, 10, 3): Decimal("0.00"),
    date(2026, 10, 4): Decimal("8000.00"),
    date(2026, 10, 5): Decimal("4000.00"),
    date(2026, 10, 6): Decimal("360.00"),
}


def test_lines_match_the_hand_calculation_without_double_counting():
    forecast = build_cash_forecast(load_portfolio())
    assert (forecast.start, forecast.end) == (date(2026, 9, 23), date(2026, 10, 6))
    assert len(forecast.days) == 14
    got = {
        (line.expected_on.isoformat(), line.invoice_id, line.source): line.amount
        for line in forecast.lines
    }
    assert got == EXPECTED_LINES
    assert len({invoice for _, invoice, _ in got}) == len(got)
    assert forecast.total == Decimal("52060.00")
    assert sum(EXPECTED_LINES.values()) == Decimal("52060.00")


def test_each_day_in_the_window_is_present_and_nonzero_days_differ():
    forecast = build_cash_forecast(load_portfolio())
    by_day = {day.day: day.amount for day in forecast.days}
    assert by_day == EXPECTED_DAYS
    positive = {amount for amount in by_day.values() if amount > 0}
    assert len(positive) >= 5
    assert sum(by_day.values(), start=Decimal("0.00")) == Decimal("52060.00")
