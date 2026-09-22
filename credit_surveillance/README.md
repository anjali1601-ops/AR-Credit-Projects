# Credit Portfolio Surveillance

Periodic review for an **existing** B2B credit portfolio. The desk watches open
accounts for payment drift, over-limit exposure, and broken promises, then
drafts a memo that affirms the limit, reduces it, adds conditions, or suspends
the open account.

Ratios and the reduced limit are computed in code. The narrator only restates
those figures. Every memo lists the figures it used as `key=value` lines. A
limit increase, or a suspension whose exposure is above **$75,000**, stays
`pending_approval` until a named credit manager calls the approve step.

The default narrator is deterministic and offline. Set
`CREDIT_SURVEILLANCE_LLM_PROVIDER=openai` plus `OPENAI_API_KEY` to send the
same cited figures to a hosted model. The figure block on the memo is still
written by the engine.

As-of date for aging is **22 September 2026**, so the seeded outcomes do not
move when the clock does.

## The four accounts

| Account | Id | What the file shows | Action | Who can post |
| --- | --- | --- | --- | --- |
| Northwind Components | `NW-1044` | Inside the limit, pays to terms, no broken promises | Affirm `$100,000` | Analyst |
| Harborline Industrial | `HB-2201` | Days to pay drifted from 32 to 47; half of recent invoices are late | Reduce to `$112,000` | Analyst |
| Vesper Packaging | `VP-3310` | Exposure `$94,000` on an `$80,000` limit, one broken promise | Add conditions, limit unchanged | Analyst |
| Redline Metals | `RL-4408` | 48.75% past due, 67 days past due, three broken promises totaling `$45,000` | Suspend the open account | Credit manager (exposure `$185,000`) |

Harborline's reduction is `min(40%, 15% + 1% × (15 − 10) drift days + 10% × 0.50 late rate) = 25%`.
`$150,000 × 0.75 = $112,500`, stepped down to the nearest thousand: **`$112,000`**.

## Run the demo

Python 3.11+.

```bash
cd credit_surveillance
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

credit-surveillance demo          # reseed and print the four memos
credit-surveillance serve         # http://127.0.0.1:47231
pytest
```

`demo` exits non-zero if the four accounts do not come back as affirm, reduce,
conditions, and suspend.

The desk at <http://127.0.0.1:47231> lists the portfolio, drafts the memos, and
posts or approves them. OpenAPI is at <http://127.0.0.1:47231/docs>.

Other commands:

```bash
credit-surveillance seed
credit-surveillance review
credit-surveillance review --account HB-2201
credit-surveillance show RV-...
credit-surveillance post RV-... --actor "Alex Kim" --role analyst
credit-surveillance approve RV-... --approver "Jordan Hale" --role credit_manager
credit-surveillance request-limit NW-1044 120000.00
```

A higher limit is recommended only when the account would otherwise be affirmed.
That recommendation cannot post until a credit manager approves it. A request
on a deteriorating account does not override a suspend, reduce, or conditions
action.

## Rules

Evaluated in order. Thresholds below are the same constants cited in the memo.

1. **Suspend** when any of these hold:
   - 2 or more broken promises, past-due ratio ≥ 25%, and payment drift ≥ 15 days
   - past-due ratio ≥ 40% and max days past due ≥ 60
   - 3 or more broken promises
2. **Reduce** when drift ≥ 10 days, late rate ≥ 40%, past-due ratio &lt; 25%, and at most one broken promise.
3. **Conditions** when exposure is over the limit or there is a broken promise (and the account was not suspended or reduced).
4. **Affirm** when utilization ≤ 80%, past-due ratio ≤ 5%, drift ≤ 5 days, late rate ≤ 20%, nothing over the limit, and no broken promises.
5. Anything left is **conditions** (outside the affirm band). The account is not affirmed by default.

Payment drift is recent average days to pay minus the baseline average. Recent
is invoices paid in the last 90 days. A promise counts as broken when its
status is `broken`, or when it is still `open` after the promised date. A kept
promise does not count.

Exposure is open receivables plus open orders. Utilization is exposure divided
by the limit. Past-due ratio is past-due receivables divided by open
receivables.

## Authority

| Recommendation | Analyst may post |
| --- | --- |
| Affirm, reduce, or conditions, with no limit increase | Yes |
| Suspend, exposure ≤ $75,000 | Yes |
| Suspend, exposure &gt; $75,000 | No. `POST /reviews/{id}/approve` with role `credit_manager` and a name |
| Any limit increase | No. Same approve step |

`POST /reviews/{id}/post` never bypasses the matrix, even when the actor's role
is credit manager. Drafting a memo does not change the limit or suspend the
account. A later surveillance run supersedes an unposted memo; the stale id
cannot be approved.

## HTTP

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Surveillance desk |
| `GET` | `/health` | Liveness |
| `GET` | `/portfolio` | Totals from the exposure engine |
| `POST` | `/portfolio/seed` | Reload the four accounts |
| `GET` | `/accounts` | Portfolio, with computed facts |
| `GET` | `/accounts/{id}` | Account plus invoices, promises, and open orders |
| `POST` | `/accounts/{id}/limit-request` | Body `{"requested_limit": "120000.00"}` |
| `POST` | `/reviews/run` | Draft memos. Optional `?account_id=` |
| `GET` | `/reviews` | Current memos |
| `GET` | `/reviews/{id}` | One memo |
| `POST` | `/reviews/{id}/post` | Body `{"actor_name": "Alex Kim", "actor_role": "analyst"}` |
| `POST` | `/reviews/{id}/approve` | Body `{"approver_name": "Jordan Hale", "approver_role": "credit_manager"}` |

The database is SQLite at `credit_surveillance/data/portfolio.db`. Override it
with `--db` or `CREDIT_SURVEILLANCE_DB`.

## Tests

```bash
pytest
```

`tests/test_exposure.py` covers the exposure math.
`tests/test_outcomes.py` covers the four seeded actions and the citations.
`tests/test_authority.py` covers the matrix, including the $75,000 boundary.
`tests/test_approve.py` covers the approve endpoint.
`tests/test_narrator.py` covers the offline default and the OpenAI request shape.
