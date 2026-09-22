# Deduction Workbench

A clerk's desk for US trade deductions. A customer short-pays and sends a debit
memo. The workbench reads that backup, names the reason, checks it against the
contract or the promotion, and proposes a route. A clerk confirms the route.
Nothing is emailed and nothing is posted.

```bash
cd deduction_workbench
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m deduction_workbench seed
python -m deduction_workbench serve
```

Open <http://127.0.0.1:47241>. Ten seeded deductions are waiting. Pick one, read
the backup, the checks, and the cited clause, then confirm the route.

## What it does

| Step | Who | What they do |
| --- | --- | --- |
| Classify | Classifier | Reads the customer email and the debit-memo text and assigns a reason code. |
| Check | Policy agent | Retrieves the customer's agreement and runs deterministic checks against its terms. |
| Route | Router | Proposes a desk. Does not send. |
| Confirm | Clerk | Records the route, or redirects it. Still does not send. |

Reason codes: shortage, pricing, returns, co-op advertising, damaged goods.

| Situation | Desk |
| --- | --- |
| Valid pricing promotion | Sales |
| Valid co-op advertising | Sales co-op (sales owns the agreement; the queue is separate from pricing) |
| Supported shortage | Warehouse |
| Authorized return | Returns |
| Supported damaged goods | Quality |
| Invalid claim | Recovery, with the clause or agreement cited |

Valid routes are five different queues. Invalid claims all go to recovery, and
the citation is the clause the check used.

## Seeded cases

| Case | Customer | Backup | Outcome | Route | Citation |
| --- | --- | --- | --- | --- | --- |
| DED-1001 | Northline Grocery | POD short 62 cases | Valid | Warehouse | MSA-2024-118 §4.2 |
| DED-1002 | Pinnacle Club | Shortage claimed, POD is complete | Invalid | Recovery | MSA-2025-077 §4.2 |
| DED-1003 | Northline Grocery | $1.25 bill-back on HF-4410 inside PROMO-2026-Q1 | Valid | Sales | PROMO-2026-Q1 §1 |
| DED-1004 | Pinnacle Club | Price difference with no promotion on file | Invalid | Recovery | MSA-2025-077 §5.1 |
| DED-1005 | Northline Grocery | 40 cases under RMA-55219 | Valid | Returns | MSA-2024-118 §6.3 |
| DED-1006 | Pinnacle Club | Goods sent back with no RMA | Invalid | Recovery | MSA-2025-077 §6.3 |
| DED-1007 | Northline Grocery | Tear sheet inside the co-op accrual | Valid | Sales co-op | COOP-2025-NL §2 |
| DED-1008 | Pinnacle Club | Advertising deduction, no co-op agreement | Invalid | Recovery | MSA-2025-077 §8.1 |
| DED-1009 | Northline Grocery | Crushed cases, photos, day 4 | Valid | Quality | MSA-2024-118 §7.4 |
| DED-1010 | Northline Grocery | Damage filed 19 days after delivery | Invalid | Recovery | MSA-2024-118 §7.4 |

Agreements live in `src/deduction_workbench/corpus/agreements/`. Retrieval is
lexical and local. The pass/fail rules are code, using the terms written on
each clause (claim windows, the promo rate and cap, the RMA rule, the co-op
proof window).

## CLI

```bash
python -m deduction_workbench list
python -m deduction_workbench routes
python -m deduction_workbench show DED-1003
python -m deduction_workbench confirm DED-1001 --clerk clerk --note "POD is short."
```

`routes` prints each case and exits non-zero if the five valid reason codes
do not land on five different queues. `confirm` writes the clerk's decision
and prints `Nothing was sent.`

The API is the same desk:

- `GET /health`
- `GET /api/cases`
- `GET /api/cases/{id}`
- `POST /api/cases/{id}/confirm` with `{"clerk_id": "clerk", "note": "..."}`
- `POST /api/cases/{id}/run` to classify again, unless a clerk already confirmed it

An optional `queue` on confirm redirects the case (`warehouse`, `sales`,
`sales_coop`, `returns`, `quality`, `recovery`). A second confirm is rejected.
The SQLite file refuses any update that sets `sent` to anything but 0.

## Narrative

The note on the case is the only text a model writes. By default that model is
offline and deterministic: it restates the outcome, the checks, the citation,
and the route. It cannot change them.

To use OpenAI for that note only:

```bash
export DEDUCTION_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4o-mini   # optional
```

If the provider is `openai` but no key is set, the desk stays on the offline
note. Classification, policy, and routing never call out.

Cases are stored in SQLite (`data/workbench.sqlite`, or `DEDUCTION_DB_PATH`).

## Tests

```bash
pytest
```

Covers classification of every seeded backup, the routing table (including a
distinct queue per reason code), and the confirm endpoint.
