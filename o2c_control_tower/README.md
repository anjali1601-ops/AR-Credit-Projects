# O2C control tower

Director view of a seeded accounts-receivable book. It computes aging, a DSO bridge, predicted cash for the next 14 days, collector workload, and the agent actions still waiting on a person. The numbers come from the seed. The paragraph at the top only phrases those numbers.

As-of date is **22 September 2026**.

## Run

From the repository root:

```bash
python -m pip install -r o2c_control_tower/requirements.txt
python -m o2c_control_tower
python -m o2c_control_tower serve
```

The server listens on port **47251**.

- Page: http://127.0.0.1:47251/
- Summary JSON: http://127.0.0.1:47251/api/summary
- Also: `/api/aging`, `/api/dso`, `/api/cash-forecast`, `/api/workload`, `/api/actions`

```bash
python -m pytest o2c_control_tower/tests
```

## What a director sees

| View | What it is |
| --- | --- |
| Aging | Open AR in current, 1–30, 31–60, 61–90, and 91+. Due today is current. |
| DSO bridge | Trailing 30-day DSO, and the sales, billing, collection, and credit-memo days that move it. |
| Cash, 14 days | One path per open invoice: an open promise, a due date inside the window, or a past-due rate. |
| Workload | Open AR, past due, broken promises, and actions owned by each collector. |
| Human queue | Credit memo, soft reminder, credit hold, and unapplied cash. Each row has an owner and an age. |

Pending credit memos and unmatched receipts are **not** in AR and **not** in the cash forecast. A posted credit memo is.

## Formulas

Open amount on a date = invoice amount − cash applied on or before that date − credit memos posted on or before that date. Invoices issued after the date are not yet in AR.

```
DSO = open AR × 30 / gross billings in the 30-day window
```

The current window is 24 August–22 September. The prior window is 25 July–23 August. Beginning AR is the book at 23 August.

```
sales effect       = beginning AR × 30 / current sales − beginning DSO
billing effect     = billings × 30 / current sales
collection effect  = −collections × 30 / current sales
credit-memo effect = −posted credit memos × 30 / current sales
```

Those parts equal ending DSO. Gross billings are the sales denominator; credit memos are a bridge line, not a reduction of sales. This seed has no write-offs.

Cash rules, mutually exclusive:

- Open promise inside the window: promised amount, capped at the open balance, × 90%. × 50% if that customer has any broken promise.
- Else, not past due and due inside the window: 80% of the open balance, on the due date.
- Else, past due: 35% (1–30, day 3), 15% (31–60, day 7), 8% (61–90, day 10), 3% (91+, day 14).

Collector score = open invoices + 2 × past-due invoices + 3 × broken promises + waiting actions that collector owns.

## Hand check

These are the totals the tests lock in.

**Aging, 22 September:** current 98,000 · 1–30 9,000 · 31–60 21,000 · 61–90 20,000 · 91+ 12,000 · total 160,000.

**Rollforward:** beginning AR 94,000 + billings 98,000 − collections 30,000 − credit memos 2,000 = ending AR 160,000. Prior sales 92,000. Current sales 98,000.

**DSO, 4 decimals:** 30.6522 − 1.8767 + 30.0000 − 9.1837 − 0.6122 = 48.9796.

**14-day cash:** 52,060, with different non-zero days (largest day is 19,800 on 24 September).

The invoice-level ledger is in `seed.py`. `tests/test_aging.py`, `tests/test_dso.py`, and `tests/test_cash_forecast.py` recompute it independently of the page.
