# Autonomous AI Credit Underwriter

A supervisor-led multi-agent credit underwriter for enterprise B2B onboarding. Given a
credit application and three years of statements, it spreads the numbers, searches a
local risk corpus, reconciles the two views under documented precedence rules, and
writes an underwriting memo in which every claim cites its source.

The default LLM provider is deterministic and offline. No API keys, no hosted models,
and no live web search unless you turn them on.

```
$ credit-underwriter seed
$ credit-underwriter underwrite-all
```

| Applicant | Recommendation | Limit | Terms | Grade |
| --- | --- | ---: | --- | --- |
| Atlas Precision Works | **Approve** | $750k of $750k | net 60 | 2 Strong |
| Northwind Logistics | **Approve with conditions** | $350k of $500k | net 30 of 45 | 6 Watch |
| Veritas Metal Trading | **Decline** | $0 of $1.20m | no open account | 10 Impaired |

Every memo in that run passes the citation check.

## The agent graph

Built with LangGraph as a real `StateGraph` with shared state, a concurrent fan-out,
a fan-in that *resolves* disagreement rather than concatenating it, and a bounded
revise loop. Not a linear template pipeline.

```
                         START
                           |
                    supervisor_plan              delegates the two specialist reviews
                           |
             +-------------+-------------+
             |                           |       financial analyst and risk searcher
     financial_analyst            risk_searcher  run in the same superstep
             |                           |
             +-------------+-------------+
                           |
                 supervisor_reconcile            precedence-ordered conflict rules
                           |
                     memo_writer  <---------+
                           |                |    critic fails → writer revises
                     memo_critic -----------+    bounded by max_memo_revisions
                           |
                          END
```

Shared state carries reducers on `evidence` and `trace`, so both specialists can
register facts without overwriting each other. Configuration (provider, index,
settings) lives on a `GraphContext` outside the state so the run remains serialisable.

| Node | Responsibility |
| --- | --- |
| `supervisor_plan` | Records the delegation plan. Does not pre-decide the outcome. |
| `financial_analyst` | Runs the deterministic finance engine (spread, ratios, trends, internal rating, limit capacity). The LLM only writes narrative over already-computed facts. |
| `risk_searcher` | Retrieves from the local corpus (Chroma or in-memory), classifies each document, applies recency decay and policy tables for country/industry/concentration, and proposes rating notches. Optional live search is off by default. |
| `supervisor_reconcile` | Resolves the two views under a fixed rule order. Each firing rule records both positions, the outcome, the prevailing side, and the evidence. |
| `memo_writer` | Chooses which facts belong in each section and binds citations *before* the model phrases the sentence. On a revision pass it repairs unevidenced figures rather than republishing them. |
| `memo_critic` | Not an LLM. Checks required sections, resolvable citations, numeric support, decision consistency, and that both a document and a ratio were cited. Failures loop back to the writer. |

### How disagreement is actually resolved

Rules run in this order. Later rules see the notches the earlier ones applied.

1. **`hard_blocker_override`** — a critical insolvency, payment-default, or governance finding declines the application regardless of the spread. Risk prevails outright. Structural mitigants are not credited.
2. **`disclosure_integrity`** — the form declared no matter of a kind the searcher then found. Severity is escalated and an explanation becomes a condition.
3. **`trend_vs_external_signal`** — an improving spread against an adverse external signal. An obligor-specific document published after the last statement date beats the trend; a purely systemic (sector/country) signal does not.
4. **`mitigant_offsets`** — parent guarantees, credit insurance, deposits, and similar are credited only against the findings they actually address, capped at two grades of uplift.
5. **`limit_reconciliation`** — capacity, risk-adjusted grade, and concentration haircut set the approved limit and terms.
6. **`appetite_policy`** — grades 1–5 approve, 6–7 approve with conditions, 8–10 decline. A hard blocker declines at any grade.

When neither specialist overrides the other, a `concurrence_check` is recorded so the file still shows that both views were read.

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd credit_underwriter
uv sync --extra chroma --group dev

uv run credit-underwriter applicants        # the three seeded obligors
uv run credit-underwriter seed              # build the retrieval index
uv run credit-underwriter underwrite atlas-precision-works --print-memo
uv run credit-underwriter underwrite-all
uv run credit-underwriter runs
uv run credit-underwriter show <run_id>
uv run credit-underwriter verify <run_id>
uv run credit-underwriter serve             # FastAPI on http://127.0.0.1:47113
```

`--retrieval-backend in_memory` skips Chroma entirely. The two backends are tested
to return the same documents for the same query, so the substitution is safe.

```bash
uv run pytest                               # the full suite
```

## The seed data

Three synthetic applicants live in `data/applicants/`. They are written so the
same three underwriting stories come out every time:

| Id | Persona | What the file contains |
| --- | --- | --- |
| `atlas-precision-works` | Clean, strong credit | Audited US precision machinist, improving coverage, no material adverse news. Requests $750k net 60. |
| `northwind-logistics` | Marginal, with mitigants | Deteriorating leverage and margins, customer concentration, a disclosed covenant waiver and a modest litigation item — plus a parent guarantee, credit insurance, and a cash deposit. Requests $500k net 45. |
| `veritas-metal-trading` | Decline on a hard blocker | Weak coverage, a winding-up petition, undeclared insolvency and payment-default findings. Requests $1.20m. |

The risk corpus in `data/corpus/risk_corpus.json` is entirely synthetic (every
document source is labelled as such). It covers news, filings, litigation,
industry and country reports, and trade references, scoped so one obligor cannot
retrieve another obligor's adverse news.

Document recency is computed against a frozen `as_of` date of **2026-09-22**, so
results do not drift with the wall clock.

## HTTP API

`credit-underwriter serve` binds to port **47113** (deliberately uncommon).
Interactive docs are at `http://127.0.0.1:47113/docs`.

```bash
curl http://127.0.0.1:47113/healthz
curl http://127.0.0.1:47113/applicants
curl -X POST http://127.0.0.1:47113/applications/atlas-precision-works/underwrite
curl http://127.0.0.1:47113/applications/atlas-precision-works/memo
curl http://127.0.0.1:47113/runs
```

`GET /applications/{id}/memo` returns markdown by default (`?format=json` for the
structured memo). `POST /applications` accepts a full `CreditApplication` body
for underwriting an applicant that is not in the seed set.

## Switching to a real LLM

Every agent call goes through an `LLMProvider`. Agents never import a vendor SDK.
The offline provider is the default and needs nothing.

```bash
export CREDIT_UNDERWRITER_LLM_PROVIDER=openai
export CREDIT_UNDERWRITER_LLM_MODEL=gpt-4o-mini   # optional; this is the default
export OPENAI_API_KEY=sk-...
uv sync --extra openai
uv run credit-underwriter underwrite atlas-precision-works
```

Or per invocation:

```bash
uv run credit-underwriter --provider openai --model gpt-4o-mini underwrite atlas-precision-works
```

The model only ever sees a JSON bundle of facts the engine already computed. The
critic still rejects any number that is not in the cited evidence, so a model that
embellishes produces a failed completeness check and a revision pass rather than a
plausible-looking wrong figure.

## Switching on live search

Live web search is **off by default**. Enable it only when you want the risk
searcher to append Tavily hits to the local corpus for that run. Hits flow through
the same classification, citation, and completeness checks as seeded documents.

```bash
export CREDIT_UNDERWRITER_ENABLE_LIVE_SEARCH=1
export TAVILY_API_KEY=tvly-...
uv run credit-underwriter underwrite atlas-precision-works
```

Without a key, the flag does nothing useful: `live_search_enabled` stays false and
a direct call raises `LiveSearchUnavailable`. The test suite never requires a key.

## Reproducibility and audit

Each completed run is written to `runs/<run_id>.json` with:

- the application as submitted
- the financial analysis, risk assessment, decision, memo, and critique history
- the full evidence registry and the per-agent trace
- a **fingerprint** of provider, model, retrieval backend, as-of date, engine version, and corpus hash
- a **state hash** of the decision-bearing content

`credit-underwriter verify <run_id>` checks two independent things:

- **content intact** — the file on disk still hashes to the recorded `state_hash` (detects a hand-edited limit or memo)
- **reproducible** — underwriting the stored application again produces the same hash (detects a changed engine, corpus, or provider)

Memos are also written to `memos/<applicant_id>.md`.

## Layout

```
credit_underwriter/
  data/applicants/          three seeded applications
  data/corpus/              synthetic risk corpus
  src/credit_underwriter/
    graph.py                LangGraph assembly
    agents/                 supervisor, analyst, searcher, reconciler, writer, critic
    finance/                deterministic spread / ratios / trends / scorecard
    retrieval/              Chroma + in-memory index, local embeddings, live search
    llm/                    provider protocol, offline default, OpenAI adapter
    risk/                   country / industry / concentration policy tables
    api.py                  FastAPI service
    cli.py                  credit-underwriter command
  tests/
```
