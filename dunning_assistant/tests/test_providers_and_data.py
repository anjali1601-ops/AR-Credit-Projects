from __future__ import annotations

import pytest

from dunning.config import Settings
from dunning.data.seed import build_dataset, write_dataset
from dunning.providers.llm import LLMRequest, MockLLM, get_llm_provider
from dunning.providers.tracing import BaseTracer, LocalFileTracer, get_tracer, read_trace


def _email_request(**overrides):
    variables = {
        "account_id": "ACC-1001",
        "customer_name": "Harbor Point Logistics",
        "vendor": "Meridian Systems",
        "contact_name": "Dana Whitfield",
        "first": "Dana",
        "contact_email": "dana@example.com",
        "contact_phone": "+1-555-0100",
        "rep_name": "Priya Raman",
        "rep_email": "priya.ar@example.com",
        "clause": "MSA §7.3",
        "payment_terms_days": 30,
        "late_fee_pct": "1.5",
        "tenure_months": 34,
        "as_of": "2026-09-18",
        "send_on": "2026-09-18",
        "response_by": "2026-09-25",
        "invoices": [{"invoice_id": "INV-1", "amount": "$10,000", "due_date": "2026-08-20", "days_past_due": 29}],
        "disputed_invoices": [],
        "oldest_invoice": "INV-1",
        "oldest_days_past_due": 29,
        "past_due_balance": "$10,000",
        "outbound_attempts": 2,
        "contact_window": 20,
        "payment_link": "https://pay.example/acc-1001",
        "plan_installments": 3,
        "relationship_label": "cooperative",
        "stage": "courtesy_reminder",
        "tone": "warm",
        "step_number": 1,
        "intent": "Soft reminder",
        "flags": {"offer_payment_plan": False, "cite_contract_terms": False, "late_fee_warning": False,
                  "service_hold_warning": False, "legal_referral": False},
    }
    variables.update(overrides)
    return LLMRequest(task="email_draft", prompt="draft it", variables=variables)


def test_mock_llm_is_deterministic():
    llm = MockLLM()
    assert llm.complete(_email_request()).text == llm.complete(_email_request()).text


def test_mock_llm_copy_changes_with_tone_and_policy():
    llm = MockLLM()
    warm = llm.complete(_email_request()).text
    strict = llm.complete(
        _email_request(
            tone="formal_strict",
            stage="pre_legal_notice",
            flags={"offer_payment_plan": True, "cite_contract_terms": True, "late_fee_warning": True,
                   "service_hold_warning": True, "legal_referral": True},
        )
    ).text

    assert warm != strict
    assert "Hi Dana" in warm and "Dear Dana Whitfield" in strict
    assert "collections counsel" in strict and "collections counsel" not in warm
    assert "instalments" in strict


def test_mock_llm_varies_copy_between_consecutive_steps():
    llm = MockLLM()
    first = llm.complete(_email_request(step_number=1)).text
    second = llm.complete(_email_request(step_number=2, previous_send_on="2026-09-18")).text

    assert first != second
    assert second.startswith("Subject: RE:")
    assert "Following up on my message of 2026-09-18" in second


def test_llm_factory_rejects_unknown_providers():
    assert get_llm_provider(Settings(llm_provider="mock")).name == "mock"
    with pytest.raises(ValueError):
        get_llm_provider(Settings(llm_provider="definitely-not-a-provider"))


def test_tracing_backends(tmp_path):
    assert get_tracer(Settings(tracing_backend="none")).name == "noop"
    assert get_tracer(Settings(tracing_backend="local", trace_dir=tmp_path)).name == "local"
    # Without credentials configured, `auto` must degrade to local files rather than fail.
    assert get_tracer(Settings(tracing_backend="auto", trace_dir=tmp_path)).name == "local"


def test_local_tracer_writes_readable_spans(tmp_path):
    tracer = LocalFileTracer(tmp_path)
    with tracer.run("test_run", run_id="abc123", metadata={"account_id": "ACC-1"}) as handle:
        with handle.span("profiler_agent", input={"account_id": "ACC-1"}) as span:
            span.update(output={"risk_score": 42}, metadata={"backend": "mock"})

    records = read_trace(tmp_path / "abc123.jsonl")
    assert records[0]["metadata"]["account_id"] == "ACC-1"
    assert records[1]["span"] == "profiler_agent"
    assert records[1]["output"]["risk_score"] == 42
    assert records[1]["metadata"]["backend"] == "mock"


def test_noop_tracer_is_a_silent_fallback(tmp_path):
    with BaseTracer().run("test_run") as handle:
        with handle.span("x") as span:
            span.update(output={"ok": True})
    assert handle.reference is None
    assert not list(tmp_path.iterdir())


def test_seed_dataset_is_deterministic_and_covers_three_archetypes():
    first, second = build_dataset(Settings()), build_dataset(Settings())

    for table in first:
        assert first[table].equals(second[table])
    archetypes = first["customers"]["archetype"].value_counts()
    assert set(archetypes.index) == {"reliable_but_late", "deteriorating_avoidant", "high_risk_delinquent"}
    assert archetypes.min() >= 3


def test_seed_invoices_and_threads_are_internally_consistent():
    frames = build_dataset(Settings())
    invoices, emails = frames["invoices"], frames["emails"]
    paid = invoices[invoices["status"] == "paid"]
    open_invoices = invoices[invoices["status"] != "paid"]
    as_of = Settings().as_of

    assert paid["paid_date"].notna().all() and (paid["days_late"] >= 0).all()
    assert open_invoices["paid_date"].isna().all()
    assert all((as_of - due).days > 0 for due in open_invoices["due_date"])
    assert (invoices["due_date"] > invoices["issue_date"]).all()
    for account_id, thread in emails.groupby("account_id"):
        assert (thread["direction"] == "outbound").any(), account_id
        assert thread["sent_at"].max() <= as_of


def test_write_dataset_emits_csv_and_sqlite(tmp_path):
    settings = Settings(data_dir=tmp_path)
    path = write_dataset(settings)

    assert {p.name for p in path.glob("*.csv")} == {
        "customers.csv", "invoices.csv", "payments.csv", "promises.csv", "emails.csv"
    }
    assert settings.sqlite_path.exists()

    import sqlite3

    with sqlite3.connect(settings.sqlite_path) as conn:
        assert conn.execute("select count(*) from customers").fetchone()[0] == 9
