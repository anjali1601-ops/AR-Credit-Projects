"""Hand calculation of aging as of 22 September 2026.

Open balances after posted cash and the posted credit memo:

    Current   INV-2001 22,000  due 24 Sep
              INV-2002 30,000  due 1 Oct
              INV-2003 16,000  due 20 Oct
              INV-2004  8,000  due 10 Oct
              INV-2005  5,000  due 15 Oct
              INV-2006  7,000  due 18 Oct
              INV-2007 10,000  due 4 Oct
                        98,000
    1–30      INV-1007  9,000  due 19 Sep, 3 days
    31–60     INV-1002  8,000  due 31 Jul, 53 days (20,000 − 12,000)
              INV-1003 13,000  due 19 Aug, 34 days (15,000 − 2,000)
                        21,000
    61–90     INV-1004 20,000  due 29 Jun, 85 days (25,000 − 5,000)
    91+       INV-1005 12,000  due 1 May, 144 days
    Total              160,000
"""

from datetime import date
from decimal import Decimal

from o2c_control_tower.aging import build_aging
from o2c_control_tower.portfolio import open_items
from o2c_control_tower.seed import load_portfolio


def test_each_bucket_is_nonzero_and_the_total_is_open_ar():
    report = build_aging(load_portfolio())
    assert report.amount("current") == Decimal("98000.00")
    assert report.amount("1_30") == Decimal("9000.00")
    assert report.amount("31_60") == Decimal("21000.00")
    assert report.amount("61_90") == Decimal("20000.00")
    assert report.amount("91_plus") == Decimal("12000.00")
    assert report.total == Decimal("160000.00")
    assert all(bucket.amount > 0 for bucket in report.buckets)
    assert [bucket.invoice_count for bucket in report.buckets] == [7, 1, 2, 1, 1]


def test_named_invoices_land_in_the_hand_calculated_bucket():
    by_id = {row.invoice_id: row for row in build_aging(load_portfolio()).invoices}
    assert by_id["INV-1007"].days_past_due == 3
    assert by_id["INV-1007"].bucket == "1_30"
    assert by_id["INV-1002"].days_past_due == 53
    assert by_id["INV-1002"].open_amount == Decimal("8000.00")
    assert by_id["INV-1003"].days_past_due == 34
    assert by_id["INV-1003"].open_amount == Decimal("13000.00")
    assert by_id["INV-1004"].days_past_due == 85
    assert by_id["INV-1005"].days_past_due == 144
    assert by_id["INV-2001"].bucket == "current"
    assert by_id["INV-2001"].days_past_due == -2
    assert "INV-1006" not in by_id
    assert "INV-1101" not in by_id
    assert "INV-1102" not in by_id


def test_beginning_open_items_match_the_23_august_ledger():
    items = {
        item.invoice_id: item.open_amount
        for item in open_items(load_portfolio(), date(2026, 8, 23))
    }
    assert items == {
        "INV-1002": Decimal("20000.00"),
        "INV-1003": Decimal("15000.00"),
        "INV-1004": Decimal("20000.00"),
        "INV-1005": Decimal("12000.00"),
        "INV-1006": Decimal("18000.00"),
        "INV-1007": Decimal("9000.00"),
    }
    assert sum(items.values()) == Decimal("94000.00")
