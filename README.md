# Accounts Receivable GenAI Workbench

Two independent GenAI projects for the accounts-receivable side of order-to-cash. Each
lives in its own top-level directory with its own dependencies, tests and README.

| Project | What it does |
| --- | --- |
| [`dispute_resolution/`](dispute_resolution/) | **Dispute Resolution & Negotiation Agent.** Reads inbound short-payment emails, audits the claim against the ERP with guarded read-only Text-to-SQL and RAG over contract PDFs, then drafts either a firm rebuttal citing the exact clause or a credit memo plus a one-click supervisor approval. |
| [`dunning_assistant/`](dunning_assistant/) | **Dunning Assistant.** Collections-side counterpart: chasing invoices that are simply overdue. See its own README for details and run instructions. |

The two are developed separately and share no code.

## Running the dispute resolution agent

Python 3.11+. No API keys and no external services are needed — it defaults to SQLite,
a deterministic offline LLM behind a provider interface, and a local ChromaDB index.

```bash
cd dispute_resolution
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m dra seed      # ERP fixtures + contract PDFs + vector index + demo inbox
python -m dra serve     # http://127.0.0.1:47821
```

Open <http://127.0.0.1:47821>. The background poller picks up the seeded emails within
a few seconds and leaves four decided cases — two upheld, two declined — waiting on a
supervisor click. Click into a case to see the evidence, the policy checks, the
contract clause that was retrieved, the SQL the auditor ran, and the drafted reply.

Tests: `python -m pytest` from `dispute_resolution/`.

Full documentation, architecture and instructions for switching to a hosted LLM or
PostgreSQL are in [`dispute_resolution/README.md`](dispute_resolution/README.md).
