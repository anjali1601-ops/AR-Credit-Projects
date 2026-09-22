# AR Dispute Resolution & Negotiation Agent

A working slice of an agentic system for **accounts-receivable short payments**. A
customer emails "we paid you less because X"; three agents open a case, prove or
disprove the claim against the ERP and the signed contract, draft the reply, and hand
a supervisor one click to sign off.

It runs end to end with **no API keys and no external services**: SQLite instead of
PostgreSQL, a deterministic offline LLM behind a provider interface, and a local
ChromaDB index over contract PDFs the seeder generates. Both the LLM and the database
swap to the real thing with one environment variable.

```
python -m dra seed && python -m dra serve     # then open http://127.0.0.1:47821
```

---

## What it does

| Agent | Responsibility |
| --- | --- |
| **Ingestion** | Watches a simulated inbox. Decides whether a message is a short-payment claim at all, then extracts the invoice number, PO, reason code and disputed amount. |
| **Auditor** | Interrogates the ERP over a guarded read-only Text-to-SQL path (invoice, payments, shipping log, discount schedules, contract terms, duplicate billing), retrieves the governing contract clause with RAG over the contract PDFs, and rules the claim `valid`, `invalid` or `needs_more_info`. |
| **Negotiation** | Turns the ruling into what a human sends. Invalid: a polite but firm rebuttal quoting the exact clause and the specific records. Valid: a credit memo draft plus a supervisor email with one-click approve/reject links. |

Nothing reaches the customer without a human click. Approval is the only place a
credit memo is posted.

### The four seeded scenarios

| Invoice | Customer | Claim | Ruling | Why |
| --- | --- | --- | --- | --- |
| INV-2025-0148 | Northwind Retail | $1,240.50 damaged goods | **valid** | POD records 27 damaged crates worth $1,242.00, claimed 6 days after delivery — inside the 10-day window in MSA-2023-014 §7.3 |
| INV-2025-0161 | Vertex Manufacturing | $2,132.00 short shipment | **valid** | Our own shipping log shows 474 of 500 shipped; SA-2024-007 §3.3 says our records establish the shortage |
| INV-2025-0152 | Cascade Health | $612.00 early-payment discount | **invalid** | The 2% schedule is live, but cleared funds arrived on day 25, and TC-2022-031 §6.4 earns the discount by payment timing alone |
| INV-2025-0170 | Northwind Retail | $3,480.00 duplicate billing | **invalid** | PO NW-89004 was billed exactly once; the invoice they point at is a different PO, already paid |

A fifth email is a plain remittance advice and is triaged out without opening a case.

---

## Architecture

```
                    inbox_messages (simulated mailbox, no mail server)
                              │
        reactive poller ──────┤  FastAPI background task, every 3s
                              ▼
   ┌──────────────┐   ingested    ┌────────────┐   audited   ┌───────────────┐
   │  Ingestion   │ ────────────► │  Auditor   │ ──────────► │  Negotiation  │
   └──────────────┘               └────────────┘             └───────────────┘
          │                        │         │                       │ drafted
          │ LLM: extract           │         │ RAG: clause           ▼
          │                        │         │              awaiting_approval
          │        guarded Text-to-SQL       │                       │ 1 click
          ▼                        ▼         ▼                       ▼
   dispute_cases  ◄──── case_events / sql_audit_log ────►  resolved | rejected
```

Every arrow is a persisted transition. The case row, the handoff events, the SQL that
was executed and the drafts all live in the database, so a case is fully replayable and
a crash mid-pipeline leaves a resumable case rather than a lost one.

```
dra/
├── agents/          ingestion.py · auditor.py · negotiation.py · schemas.py
├── workflow/        states.py (state machine) · orchestrator.py (the only writer)
├── sql/             guardrails.py (static validation) · text_to_sql.py (the tool)
├── rag/             embeddings.py · store.py (Chroma) · contracts.py (PDF → clauses)
│   └── contract_sources/   three contracts in markdown, rendered to PDF at seed time
├── llm/             base.py (interface) · mock.py (default) · hosted.py (OpenAI/Anthropic)
│   └── offline/     the deterministic extraction, SQL planning and drafting logic
├── db/              models.py · session.py · schema_catalog.py · seed.py
├── inbox/           simulator.py · samples.py
└── api/             main.py · schemas.py · templates/ (server-rendered dashboard)
```

### Safe database access

The Text-to-SQL path has three independent defences, and the agents have no other way
to reach the database:

1. **Schema-scoped prompting.** The model sees only the eight ERP tables, rendered from
   live SQLAlchemy metadata so the description cannot drift. Workflow tables (cases,
   drafts, credit memos, tokens) are never described to it.
2. **Static guardrails** (`dra/sql/guardrails.py`). One statement only; must start with
   `SELECT`/`WITH`; every write and DDL keyword rejected; `SELECT … INTO` rejected;
   filesystem and network functions (`pg_read_file`, `load_extension`, `dblink`, …)
   rejected; tables checked against the allow-list; schema-qualified names refused;
   comments stripped before parsing so nothing hides behind them; a row limit injected;
   and every bind parameter must have a supplied value. Values are never formatted into
   the SQL string.
3. **Read-only execution.** The query runs on a connection whose transaction the
   database itself holds read-only (`PRAGMA query_only` on SQLite, `SET TRANSACTION
   READ ONLY` plus a statement timeout on PostgreSQL). A write that somehow got past
   the guardrails still cannot land.

Writes — case transitions, credit memos, invoice status — only ever happen through
`DisputeOrchestrator`, in explicit ORM code.

Every query the auditor runs is stored in `sql_audit_log` and shown on the case page
alongside its bound parameters, row count and duration.

### The decision is evidence-linked, not vibes

The LLM extracts and drafts; the *ruling* is a rule engine over rows the auditor
actually read. Each reason code has named policy checks (damage on the POD, claim
inside the contractual notice window, claimed amount within the supported value, …) and
the case stores every check with its pass/fail and the figures behind it. That is what
makes the rebuttal quotable and the approval reviewable.

### RAG over the contracts

Three contracts live as markdown under `dra/rag/contract_sources/`. Seeding renders
them to real PDFs with fpdf2, parses the clauses back out of the rendered PDFs with
pypdf, and indexes each clause in ChromaDB with a deterministic hashed bag-of-words
encoder (no model download, no API key, identical results on every run). Retrieval is
filtered to the customer's own contract and reranked with reason-specific anchor terms
so the citation is the operative clause, not merely a similar one.

---

## Running it

Requires Python 3.11+.

```bash
cd dispute_resolution
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m dra seed      # schema + fixtures + contract PDFs + Chroma index + inbox
python -m dra serve     # http://127.0.0.1:47821
```

`seed` drops and recreates everything; `python -m dra seed --keep` leaves existing
tables alone. Runtime artefacts (SQLite file, generated PDFs, Chroma index) land in
`dispute_resolution/var/` and are gitignored.

### Walking the demo

With the server running, the background poller picks the seeded email up within a few
seconds. Open <http://127.0.0.1:47821> and you will see four cases already decided and
waiting on you, plus the remittance advice that was triaged out.

Click any case to see the evidence, the policy checks, the clause that was retrieved,
the SQL the auditor ran, and the drafted correspondence — then **Approve** or
**Reject**.

Prefer the terminal?

```bash
python -m dra poll                      # process every unread email
python -m dra cases                     # one line per case
python -m dra show CASE-2026-0001       # timeline, drafts, credit memo
python -m dra approve CASE-2026-0001    # issues the credit memo
python -m dra show CASE-2026-0002       # the firm rebuttal, citing TC-2022-031 §6.4
```

Or over HTTP:

```bash
curl -s localhost:47821/api/inbox/poll -X POST | jq
curl -s localhost:47821/api/cases | jq '.[] | {case_number, decision, disputed_amount}'
curl -s localhost:47821/api/cases/1 | jq '{rationale: .decision_rationale, clause: .cited_clause_ref}'
curl -s localhost:47821/api/cases/1/approve -X POST -H 'content-type: application/json' \
     -d '{"actor":"ar.lead@acme-supply.example"}' | jq
```

Send in your own dispute and watch it go through the whole pipeline synchronously:

```bash
curl -s localhost:47821/api/inbox/messages -X POST -H 'content-type: application/json' -d '{
  "sender": "ap@northwind-retail.example",
  "subject": "Short pay on INV-2025-0148",
  "body": "We short-paid invoice INV-2025-0148 by $1,240.50 for damaged crates on PO NW-88421."
}' | jq
```

### API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dispute desk dashboard |
| `GET` | `/cases/{id}` | Case detail: evidence, checks, clause, SQL, drafts |
| `GET` | `/approve/{token}` · `/reject/{token}` | The one-click links in the supervisor email |
| `GET` | `/health` | Provider, database and queue status |
| `GET` | `/api/inbox` | The simulated mailbox |
| `POST` | `/api/inbox/messages` | Deliver a new email (runs the pipeline by default) |
| `POST` | `/api/inbox/poll` | Process everything unread |
| `GET` | `/api/cases` · `/api/cases/{id}` | List and detail, filterable by `state`/`decision` |
| `POST` | `/api/cases/{id}/approve` · `/reject` | Supervisor decision as JSON |
| `GET` | `/api/contracts/search` | The auditor's clause retrieval, exposed for inspection |
| `GET` | `/docs` | OpenAPI |

---

## Switching to a real LLM or PostgreSQL

Copy `.env.example` to `.env` and edit, or export the variables.

**Hosted LLM.** The agents are written against `LLMProvider`; the default `mock`
implementation answers each task deterministically offline. Swap it with:

```bash
pip install openai                    # or: pip install anthropic
export DRA_LLM_PROVIDER=openai        # or: anthropic
export DRA_OPENAI_API_KEY=sk-...
export DRA_OPENAI_MODEL=gpt-4o-mini
```

No agent code changes. The LangChain prompts in `dra/prompts.py` are already what gets
sent — schema-scoped SQL generation, extraction with a strict JSON contract, and the
drafting prompts. A hosted model writes free-form SQL; it goes through exactly the same
guardrails and read-only executor as the offline planner's, which is the point of the
split.

**PostgreSQL.**

```bash
pip install "psycopg[binary]"
docker run -d --name dra-pg -e POSTGRES_PASSWORD=dra -e POSTGRES_USER=dra \
           -e POSTGRES_DB=dra -p 5432:5432 postgres:16
export DRA_DATABASE_URL="postgresql+psycopg://dra:dra@localhost:5432/dra"
python -m dra seed
```

The models, guardrails and queries are portable — no SQLite-only SQL is generated. For
a hard guarantee at the role level, create a `SELECT`-only role and point
`DRA_READONLY_DATABASE_URL` at it; the Text-to-SQL path will use it while application
writes keep the main connection. PGVector instead of ChromaDB would slot in behind the
`VectorStore` protocol in `dra/rag/store.py`.

### Settings worth knowing

| Variable | Default | Meaning |
| --- | --- | --- |
| `DRA_LLM_PROVIDER` | `mock` | `mock`, `openai`, `anthropic` |
| `DRA_DATABASE_URL` | SQLite under `var/` | Any SQLAlchemy URL |
| `DRA_READONLY_DATABASE_URL` | same as above | Dedicated read-only role for Text-to-SQL |
| `DRA_VECTOR_STORE` | `chroma` | `chroma` or `memory` |
| `DRA_API_PORT` | `47821` | HTTP port |
| `DRA_INBOX_POLL_ENABLED` | `true` | The reactive background poller |
| `DRA_AUTO_APPROVE_LIMIT` | `0` | Auto-approve credits at or below this; `0` always asks a human |

---

## Tests

```bash
pip install pytest httpx
python -m pytest
```

67 tests covering extraction (including picking the deduction out of a sentence that
also contains the invoice total, and refusing to escalate a plain remittance advice),
the SQL guardrails (every write and DDL form, statements smuggled behind comments,
tables outside the schema, and the database-level read-only enforcement), the validity
decision for all four scenarios with their clause citations, the state machine's legal
and illegal transitions, and the API including the one-click approval and its replay.

They run against a throwaway SQLite file and the in-memory vector store, so they need
no network and leave nothing behind.

---

## Deliberate scope

One complete slice, not a platform. No auth, no queue, no mail server, no extra
services. Known simplifications: the offline provider's SQL comes from an intent
catalogue rather than free generation (a hosted model does generate it, through the
same guardrails); the offline embeddings are lexical rather than neural, which suits a
small clause corpus but would want a real encoder at scale; and "sending" an email means
marking the draft sent.
