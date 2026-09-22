# Cash Application Agent

A working slice of a US accounts-receivable cash application desk for
**Harborline Industrial Supply**. Incoming customer payments — lockbox check
stubs, EDI 820 remittances, and short-pay emails — are matched to open
invoices. A clerk confirms before anything hits the ledger.

No API key and no external service. SQLite is the ERP. Matching is
deterministic code. A provider interface writes the clerk-facing note; the
default provider is an offline template.

```bash
cd cash_application
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m cash_application seed
python -m cash_application outcomes
python -m cash_application serve          # http://127.0.0.1:47221
```

## What the three agents do

| Agent | Responsibility |
| --- | --- |
| **Ingestion** | Reads the remittance and extracts the payer, the amount, check/trace/PO references, and the invoice numbers the customer claimed. Lockbox stubs, EDI 820, and email each have their own parser. |
| **Matcher** | Ranks open invoices on invoice number, customer, amount, and reference. Proposes one application: full, short-pay, overpay, or unapplied. |
| **Exception** | Clean full applies wait in a ready queue. Short-pay, overpay, unidentified cash, and anything that does not cite an invoice number go to a clerk with the ranked candidates. |

The explainer only writes the note. It does not pick invoices or amounts.
`CASH_APP_LLM_PROVIDER` defaults to `offline`. Set it to `openai` (and
`OPENAI_API_KEY`) to have a hosted model rewrite that note from the same
facts. See `.env.example`.

Nothing posts without a clerk. Confirm records the clerk's name. There is
no login.

## Seeded remittances

| Ref | Channel | What it is | Outcome |
| --- | --- | --- | --- |
| `LBX-20260918-014` | Lockbox | Northwind check $4,250.00 for INV-10481 | **Full**, ready, one invoice |
| `EDI-820-20260918-VM` | EDI 820 | Vertex ACH $11,050.00 for INV-10530 and INV-10531 | **Full**, ready, two invoices |
| `EML-20260919-CASCADE` | Email | Cascade pays $11,240.00 on a $12,480.00 invoice, $1,240.00 damaged goods | **Short pay**, exception |
| `LBX-20260918-088` | Lockbox | Pinnacle check $6,100.00 on INV-10550 ($5,600.00) | **Overpay**, $500.00 left unapplied |
| `LBX-20260918-102` | Lockbox | Apex Surplus $2,000.00, no account, no invoice | **Unapplied** |
| `EDI-820-20260918-RMU` | EDI 820 | Redwood $15,000.00 with PO RMU-2026-09, no invoice number | Full, held for confirm |
| `EML-20260920-LAKESHORE` | Email | $1,500.00 across INV-10580 and INV-10581 ($1,550.25 open) | Short pay, two lines |
| `LBX-20260921-221` | Lockbox | Northwind $1,875.50 on account, no invoice number | Full on INV-10492, held |

`python -m cash_application outcomes` prints that table and exits non-zero
if the first five shapes are not distinct.

A ninth invoice, INV-10544, is already paid so the matcher can show it
will not take cash.

## How a match is chosen

Cited invoice numbers are the proposal. The payment is applied in that
order: in full when it equals the open balance, as a short-pay waterfall
when it is less, and as an overpay (open balance applied, remainder
unapplied) when it is more.

With no invoice number, the matcher takes the customer's single open
invoice that equals the payment. It does not invent a multi-invoice
combination the remittance did not name, and it does not apply a short-pay
to a guess. Two invoices with the same open amount stay unapplied for a
clerk. A paid invoice is never selected.

Confidence is a fixed result of those facts (a cited exact apply is 0.98,
not a model score). Ready requires a full apply, a cited invoice number,
and confidence of at least 0.90. Every other shape is an exception.

Clerk actions:

- **Apply** — post the proposed lines. Not offered when nothing was selected.
- **Split** — post the amounts typed next to the candidates. The sum cannot
  exceed the remittance or any invoice's open balance. Leftover cash stays
  unapplied.
- **Leave unapplied** — post the receipt on account (or unidentified, if
  the payer is unknown) and do not touch invoices.

## HTTP

`serve` listens on port **47221**.

```bash
curl http://127.0.0.1:47221/health
curl http://127.0.0.1:47221/api/outcomes
curl http://127.0.0.1:47221/api/exceptions
curl -X POST http://127.0.0.1:47221/api/remittances/LBX-20260918-014/confirm \
  -H 'content-type: application/json' \
  -d '{"action":"apply","clerk":"Alex Chen"}'
```

The same confirm is the button on `/remittances/{ref}`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Ready queue, exceptions, posted receipts |
| GET | `/remittances/{ref}` | Candidates, explanation, confirm form |
| POST | `/remittances/{ref}/confirm` | Form post: `action`, `clerk`, optional split amounts |
| GET | `/api/remittances` | Every proposal |
| GET | `/api/remittances/{ref}` | One proposal |
| POST | `/api/remittances` | Ingest a new raw remittance and match it |
| GET | `/api/exceptions` | Open exception queue |
| POST | `/api/remittances/{ref}/confirm` | Apply, split, or leave unapplied |
| GET | `/api/invoices` | Open AR after posting |
| GET | `/api/outcomes` | The five shapes, and whether they still differ |

## CLI

```bash
python -m cash_application seed --reset     # rebuild the SQLite ledger
python -m cash_application process          # match anything still pending
python -m cash_application queue
python -m cash_application show LBX-20260918-088
python -m cash_application confirm LBX-20260918-088 --action apply --clerk "Alex Chen"
python -m cash_application confirm LBX-20260918-088 --action split --clerk "Alex Chen" \
  --lines INV-10550:5600.00,INV-10558:500.00
python -m cash_application outcomes
python -m cash_application serve --port 47221
```

The database file is `cash_application/data/ar.sqlite`. Override it with
`CASH_APP_DB`.

## Tests

```bash
python -m pytest
```

`test_matcher.py` covers the five shapes, reference and amount matches,
paid invoices, and the provider switch. `test_exceptions.py` covers queue
placement, ranked candidates, and the action set. `test_confirm.py` covers
the confirm endpoint: no posting before confirm, full apply, short-pay,
overpay, unapplied, split limits, and a second confirm rejected.
