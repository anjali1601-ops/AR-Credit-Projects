# Multi-Agent Predictive Dunning & Collections Assistant

A working slice of an accounts-receivable collections assistant. Given a past-due account it
profiles the customer's payment behaviour, reads the tone of the email thread with them, decides a
collections strategy from those two signals, and drafts the actual multi-step outreach sequence -
emails, call scripts, SMS and certified letters, each with a send date.

The whole thing runs offline. No API keys, no hosted services, no database to stand up.

```
$ dunning run ACC-1001     # reliable payer who slipped once -> warm 2-email reminder
$ dunning run ACC-2001     # lateness creeping up, replies drying out -> firm 4-step, multi-channel
$ dunning run ACC-3001     # 127 days past due, 3 broken promises -> formal pre-legal notice
```

## The agent graph

Built with LangGraph as a real `StateGraph` with shared state, a parallel fan-out, a conditional
branch and a revision cycle - not a linear template pipeline.

```
                        START
                          |
                       ingest                      load invoices, payments, promises,
                          |                        email threads into one pandas snapshot
             +------------+------------+
             |                         |           profiler and sentiment run in parallel
      profiler_agent            sentiment_agent    from the same shared state
             |                         |
             +------------+------------+
                          |                        fan-in: strategy needs both
                    strategy_agent
                          |
          human approval? +----------- no ---------+
                          |                        |
                    approval_gate ---------------->|
                                                   v
                                          communications_agent  <---+
                                                   |                |
                                          compliance_review --------+
                                                   |   issues found, revise (max 2)
                                                 clean
                                                   |
                                                finalize -> END
```

| Node | What it does |
| --- | --- |
| `ingest` | Loads one account into an `AccountSnapshot` (customer + four pandas frames) that every other node reads. |
| `profiler_agent` | Computes ~25 behavioural metrics (aging buckets, balance-weighted days past due, days-late mean/stddev/trend slope, broken promises, partial payments, credit exposure, email responsiveness), scores risk 0-100 from weighted components, and classifies the account into an archetype. |
| `sentiment_agent` | Scores each inbound client message, weights by recency (45-day half-life), and combines polarity with behavioural cues (hedging, hostility, frustration, cash stress) and reply latency into a relationship label and health score. |
| `strategy_agent` | The policy engine. Aging anchors the escalation stage; behaviour and sentiment adjust stage, tone, channel mix, cadence and policy flags. Every rule that fired is recorded in `policy_trace`. |
| `approval_gate` | Conditional. Sequences that carry a legal referral, touch a large account at final demand, or target a frustrated customer are held for a named human approver. |
| `communications_agent` | Renders each planned step through the LLM provider with the full fact set: invoice table, deadline, contract clause, active policies, relationship context. |
| `compliance_review` | 20+ checks (unresolved placeholders, missing amounts, legal language without a legal decision, escalation wording in a warm-tone message, SMS opt-out, clause citation, promised payment plan actually offered). Failures loop back to the drafting agent with feedback, bounded at 2 revisions. |
| `finalize` | Seals the run and records the summary span. |

### How the decision is actually made

Aging sets the baseline stage; behaviour and sentiment move it by at most one notch, and a
guardrail blocks pre-legal notices without 90+ day aging or three broken promises. Sentiment is
deliberately kept off the escalation ladder and routed into tone, channels and cadence instead:

- **Reliable payer who slipped once** (`ACC-1001`): courtesy reminder, warm, 2 emails 7 days apart, no late-fee or legal language.
- **Same payer, unhappy about a billing error** (`ACC-1003`): same stage and warm tone, but a repair phone call is added to the sequence.
- **Lateness trending up, replies stopped** (`ACC-2001`): escalation notice, firm, email + call + SMS + a hardened closing email. Avoidance tightens the cadence (the sequence runs over 8 days instead of 13) rather than jumping a stage; a payment plan is offered.
- **127 days past due, 3 broken promises, silent** (`ACC-3001`): pre-legal notice, formal strict, certified letter included, contract clause and late fees cited, legal referral stated, held for human approval.
- **Equally delinquent but actively angry** (`ACC-3002`): same stage and same legal consequence, but tone softens from formal-strict to firm and a call comes before the letter.

## Quickstart

Requires Python 3.10+.

```bash
cd dunning_assistant
python -m venv .venv && source .venv/bin/activate    # or: uv venv .venv && source .venv/bin/activate
pip install -e .                                     # add '.[dev]' for the tests, '.[hf]' for HuggingFace sentiment

dunning seed --force          # regenerate the synthetic dataset (CSV + SQLite)
dunning accounts              # the seeded past-due portfolio
dunning run ACC-2001          # profile + strategy + sequence
dunning run ACC-3001 --full   # include the full drafted message bodies
dunning run ACC-1001 --json   # machine-readable run
dunning eval                  # score every account against its archetype expectations
dunning trace <run_id>        # replay the spans of a recorded run
dunning serve                 # FastAPI on http://127.0.0.1:8642
```

The dataset is committed, so `dunning run` works immediately after install; `seed` only needs to be
run if you change the generator or delete `data/`.

### HTTP API

`dunning serve` binds to port **8642** (deliberately uncommon).

```bash
curl http://127.0.0.1:8642/health
curl http://127.0.0.1:8642/accounts
curl -X POST http://127.0.0.1:8642/runs -H 'content-type: application/json' -d '{"account_id":"ACC-3001"}'
curl http://127.0.0.1:8642/eval
```

Interactive docs at `http://127.0.0.1:8642/docs`.

## The seed data

`dunning seed` generates a deterministic synthetic portfolio (9 accounts, 139 invoices, 120
payments, 18 payment promises, 77 email messages) as CSVs plus a SQLite mirror in `data/`. Every
record is dated relative to a fixed `as_of` of **2026-09-18**, so aging buckets and "days since last
reply" never drift with the wall clock and results stay reproducible.

Three archetypes, three accounts each:

| Archetype | Payment behaviour | Email thread | Accounts |
| --- | --- | --- | --- |
| `reliable_but_late` | 15-20 invoices settled at a metronomic ~13-15 days past terms, low variance, one invoice currently 17-31 days past due | Cooperative and prompt (one account is annoyed about a billing error) | ACC-1001, ACC-1002, ACC-1003 |
| `deteriorating_avoidant` | Days late climbing from ~4 to ~38 across the history, 2-3 invoices open into the 61-90 bucket, one broken promise | Early cooperation decays into hedging ("let me check with finance and revert"), then silence for 30-45 days with 3 unanswered chases | ACC-2001, ACC-2002, ACC-2003 |
| `high_risk_delinquent` | Erratic history, 3-4 invoices open including 90+ and 120+ days, exposure at or above the credit limit, 3 broken promises, a token partial payment, a disputed invoice | Hostile ("we are not paying until this is sorted") followed by long silence, or a recent angry reply | ACC-3001, ACC-3002, ACC-3003 |

## Models and providers

Everything external sits behind an interface with an offline default.

### LLM

| Setting | Behaviour |
| --- | --- |
| `DUNNING_LLM_PROVIDER=mock` (default) | `MockLLM`, a deterministic writer. It composes from the structured facts the communications agent passes it - tone, stage, channel, intent, policy flags, invoice table, relationship label, QA feedback - so different strategies produce materially different copy, and identical inputs always produce identical output (which is what makes the eval and the tests meaningful). |
| `DUNNING_LLM_PROVIDER=openai` | Real chat completions. Needs `pip install '.[openai]'` and `OPENAI_API_KEY`; pick the model with `DUNNING_LLM_MODEL`. The agents already build a full natural-language prompt for every draft, so no other change is needed. |

### Sentiment

| Setting | Behaviour |
| --- | --- |
| `DUNNING_SENTIMENT_BACKEND=auto` (default) | Tries HuggingFace, silently falls back to the lexicon classifier if transformers/torch are missing, the weights are not cached, or there is no network. |
| `DUNNING_SENTIMENT_BACKEND=huggingface` | `distilbert-base-uncased-finetuned-sst-2-english` (~268 MB) via `transformers.pipeline`. Override with `DUNNING_HF_MODEL`. |
| `DUNNING_SENTIMENT_BACKEND=rules` | Lexicon classifier. No download, deterministic, and good enough on AR email copy. |

**What was actually done here:** the small HuggingFace model does download and run in this
environment, so `auto` resolves to `huggingface` once `pip install '.[hf]'` has been run and the
weights are cached. Both backends produce identical relationship labels and identical strategies
across all nine seeded accounts (there is a test asserting exactly that), so the demo, the eval and
the test suite all default to the rule-based path to stay fast and hermetic. Behavioural cue
extraction (hedging, hostility, frustration, cash stress) is shared by both backends - the
transformer contributes polarity, the cue layer contributes intent.

### Tracing

| Setting | Behaviour |
| --- | --- |
| `DUNNING_TRACING=auto` (default) | Langfuse if `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, else Phoenix if `PHOENIX_COLLECTOR_ENDPOINT` is set, else local files. |
| `DUNNING_TRACING=local` | One JSONL file per run in `.traces/<run_id>.jsonl`: a header with run metadata, then one record per agent node with its input, output and duration. |
| `DUNNING_TRACING=langfuse` | Langfuse spans (`pip install '.[langfuse]'`). |
| `DUNNING_TRACING=phoenix` | OpenTelemetry spans to a Phoenix collector (`pip install '.[phoenix]'`). |
| `DUNNING_TRACING=none` | In-memory only. |

Reading a local trace:

```bash
dunning run ACC-3001                 # prints "trace: .../.traces/<run_id>.jsonl"
dunning trace <run_id>               # span table: node, duration, output
cat .traces/<run_id>.jsonl | jq .    # or read the raw records
```

Each node emits a span carrying the decision it made - the profiler's archetype and signals, the
sentiment backend and label, the strategy's full policy trace, the drafted subjects, and the
compliance verdict - so a run can be audited without re-running it.

## Evaluation

`dunning eval` runs the graph over every seeded account and grades the result against the strategy
that archetype should have received. No judge model is involved; every dimension is a deterministic
assertion, so the eval works offline and is stable.

| Dimension | Weight | Checks |
| --- | --- | --- |
| `archetype_detection` | 2.0 | Profiler recovered the ground-truth archetype |
| `escalation_stage` | 2.0 | Stage within the band allowed for the archetype |
| `tone` | 1.5 | Tone within the allowed set |
| `channel_mix` | 1.0 | Required channels present, forbidden ones absent (no certified letters to a reliable payer) |
| `policy_flags` | 1.5 | Required flags set, forbidden flags clear (no legal referral on a deteriorating account) |
| `sequence_shape` | 1.0 | Step count, strictly increasing timings, sequence length within the archetype's window |
| `content_safety` | 1.0 | No archetype-forbidden phrasing in any body |
| `compliance_review` | 1.0 | The in-graph compliance gate passed |

```
$ dunning eval
Overall weighted score 100.0%, cases fully passing 100%
```

`dunning eval --out report.json` writes the full per-case breakdown; `--strict` exits non-zero on
any failure, which is what you would wire into CI. The eval is tested for sensitivity too: tests
mutate a high-risk run into a soft warm reminder, inject legal threats into a reliable payer's
emails, and mislabel an archetype, and assert the harness fails each one.

## Tests

```bash
pip install -e '.[dev]'
pytest -q        # 69 tests, ~6s (3 skip automatically without the '.[hf]' extra)
```

Coverage: profiling metric maths against hand-built fixtures, risk monotonicity, archetype
classification for all nine accounts, sentiment polarity/cues/recency-weighting/label mapping,
HuggingFace-vs-rules agreement (skipped automatically if the model is unavailable), per-archetype
strategy expectations, escalation guardrails, graph end-to-end runs, determinism, the compliance
revision cycle and its bound, trace contents, eval scoring and sensitivity, seed-data integrity and
determinism, and the HTTP endpoints.

## Layout

```
dunning_assistant/
  src/dunning/
    agents/          profiler, sentiment, strategy, communications, review
    data/            seed generator + pandas repository
    providers/       llm (mock | openai), sentiment (hf | rules), tracing (local | langfuse | phoenix)
    evaluation/      archetype expectations + scoring harness
    graph.py         LangGraph wiring
    state.py         shared graph state
    domain.py        typed records passed between agents
    cli.py  api.py   runnable surfaces
  tests/
  data/              committed synthetic dataset
```

## Scope

Deliberately not included: authentication, a real mail/telephony sender, a production database,
payment-processor integration, or a UI. Sequences are drafted and held - nothing is ever sent.
`MockLLM` is a stand-in for a hosted model, not an attempt to be one; swap in a real provider with a
single environment variable when you want genuinely generated prose.
