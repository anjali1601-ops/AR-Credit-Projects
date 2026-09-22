"""Hand calculation of the 30-day DSO bridge.

Windows are 30 inclusive days:
    prior    25 Jul 2026 – 23 Aug 2026    sales 92,000    AR 94,000
    current  24 Aug 2026 – 22 Sep 2026    sales 98,000    AR 160,000

    beginning DSO = 94,000 × 30 / 92,000 = 30.65217391… → 30.6522
    ending DSO    = 160,000 × 30 / 98,000 = 48.97959183… → 48.9796
    sales effect  = 94,000 × 30 / 98,000 − beginning DSO = −1.87666370… → −1.8767
    billings      = 98,000 × 30 / 98,000 = 30
    collections   = −30,000 × 30 / 98,000 = −9.18367346… → −9.1837
    credit memos  = −2,000 × 30 / 98,000 = −0.61224489… → −0.6122

Rounded to 4 decimals the parts still add to ending DSO:
    30.6522 − 1.8767 + 30.0000 − 9.1837 − 0.6122 + 0.0000 = 48.9796
"""

from datetime import date
from decimal import Decimal
from fractions import Fraction

from o2c_control_tower.dso import build_dso, windows
from o2c_control_tower.format import quantize_ratio
from o2c_control_tower.portfolio import billings, collections, invoices_issued, posted_credit_memos
from o2c_control_tower.seed import load_portfolio


def _independent_bridge() -> dict[str, Fraction]:
    days = 30
    beginning_ar = Fraction(94000)
    ending_ar = Fraction(160000)
    prior_sales = Fraction(92000)
    current_sales = Fraction(98000)
    billed = Fraction(98000)
    collected = Fraction(30000)
    memos = Fraction(2000)
    beginning = beginning_ar * days / prior_sales
    ending = ending_ar * days / current_sales
    return {
        "beginning": beginning,
        "ending": ending,
        "sales": beginning_ar * days / current_sales - beginning,
        "billings": billed * days / current_sales,
        "collections": -(collected * days / current_sales),
        "credit_memos": -(memos * days / current_sales),
        "other": Fraction(0),
    }


def test_windows_and_activity_come_from_the_seed():
    portfolio = load_portfolio()
    current, prior = windows(date(2026, 9, 22))
    assert (prior.start, prior.end) == (date(2026, 7, 25), date(2026, 8, 23))
    assert (current.start, current.end) == (date(2026, 8, 24), date(2026, 9, 22))
    assert prior.inclusive_days == 30
    assert current.inclusive_days == 30

    prior_ids = {invoice.id for invoice in invoices_issued(portfolio, prior.start, prior.end)}
    current_ids = {invoice.id for invoice in invoices_issued(portfolio, current.start, current.end)}
    assert prior_ids == {"INV-1101", "INV-1102", "INV-1006", "INV-1007"}
    assert current_ids == {
        "INV-2001",
        "INV-2002",
        "INV-2003",
        "INV-2004",
        "INV-2005",
        "INV-2006",
        "INV-2007",
    }
    assert billings(portfolio, prior.start, prior.end) == Decimal("92000.00")
    assert billings(portfolio, current.start, current.end) == Decimal("98000.00")
    assert collections(portfolio, current.start, current.end) == Decimal("30000.00")
    assert posted_credit_memos(portfolio, current.start, current.end) == Decimal("2000.00")


def test_bridge_matches_an_independent_formula_and_ties():
    bridge = build_dso(load_portfolio())
    expected = _independent_bridge()
    assert bridge.beginning_ar == Decimal("94000.00")
    assert bridge.ending_ar == Decimal("160000.00")
    assert bridge.prior_sales == Decimal("92000.00")
    assert bridge.current_sales == Decimal("98000.00")
    assert bridge.billings == Decimal("98000.00")
    assert bridge.collections == Decimal("30000.00")
    assert bridge.credit_memos == Decimal("2000.00")
    assert bridge.other == Decimal("0.00")
    assert bridge.beginning_dso == expected["beginning"]
    assert bridge.ending_dso == expected["ending"]
    assert bridge.sales_effect == expected["sales"]
    assert bridge.billing_effect == expected["billings"]
    assert bridge.collection_effect == expected["collections"]
    assert bridge.credit_memo_effect == expected["credit_memos"]
    assert bridge.other_effect == expected["other"]
    assert bridge.ties()
    assert (
        bridge.beginning_ar + bridge.billings - bridge.collections - bridge.credit_memos + bridge.other
        == bridge.ending_ar
    )


def test_four_decimal_bridge_still_ties():
    bridge = build_dso(load_portfolio())
    assert quantize_ratio(bridge.beginning_dso) == Decimal("30.6522")
    assert quantize_ratio(bridge.sales_effect) == Decimal("-1.8767")
    assert quantize_ratio(bridge.billing_effect) == Decimal("30.0000")
    assert quantize_ratio(bridge.collection_effect) == Decimal("-9.1837")
    assert quantize_ratio(bridge.credit_memo_effect) == Decimal("-0.6122")
    assert quantize_ratio(bridge.other_effect) == Decimal("0.0000")
    assert quantize_ratio(bridge.ending_dso) == Decimal("48.9796")
    rounded = (
        quantize_ratio(bridge.beginning_dso)
        + quantize_ratio(bridge.sales_effect)
        + quantize_ratio(bridge.billing_effect)
        + quantize_ratio(bridge.collection_effect)
        + quantize_ratio(bridge.credit_memo_effect)
        + quantize_ratio(bridge.other_effect)
    )
    assert rounded == Decimal("48.9796")
    nonzero = [key for key, _, effect in bridge.effects() if effect != 0]
    assert nonzero == ["sales", "billings", "collections", "credit_memos"]
