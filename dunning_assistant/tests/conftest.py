from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from dunning.config import Settings
from dunning.data.repository import AccountRepository
from dunning.domain import AccountSnapshot, Customer
from dunning.graph import GraphDependencies, run_account

ARCHETYPE_SAMPLES = {
    "reliable_but_late": "ACC-1001",
    "deteriorating_avoidant": "ACC-2001",
    "high_risk_delinquent": "ACC-3001",
}


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    """Offline defaults: deterministic writer, lexicon sentiment, local traces in a tmp dir."""
    root = tmp_path_factory.mktemp("dunning")
    return Settings(
        llm_provider="mock",
        sentiment_backend="rules",
        tracing_backend="local",
        data_dir=root / "data",
        trace_dir=root / "traces",
    )


@pytest.fixture(scope="session")
def repository(settings: Settings) -> AccountRepository:
    return AccountRepository(settings)


@pytest.fixture(scope="session")
def deps(settings: Settings, repository: AccountRepository) -> GraphDependencies:
    return GraphDependencies.build(settings, repository=repository)


@pytest.fixture(scope="session")
def runs(deps: GraphDependencies) -> dict:
    """One graph run per seeded account, shared by the assertions below."""
    return {account_id: run_account(account_id, deps) for account_id in deps.repository.account_ids()}


def make_snapshot(
    *,
    as_of: date = date(2026, 9, 18),
    paid_days_late: list[int] | None = None,
    open_dpd: list[int] | None = None,
    amount: float = 10_000.0,
    promises: list[bool] | None = None,
    emails: list[tuple[int, str, str]] | None = None,
    credit_limit: float = 100_000.0,
) -> AccountSnapshot:
    """Hand-built snapshot so metric maths can be asserted against known inputs."""
    paid_days_late = [15, 15, 15] if paid_days_late is None else paid_days_late
    open_dpd = [30] if open_dpd is None else open_dpd

    customer = Customer(
        account_id="ACC-TEST",
        name="Test Co",
        industry="Testing",
        segment="smb",
        relationship_start=as_of - timedelta(days=600),
        annual_contract_value=120_000,
        credit_limit=credit_limit,
        payment_terms_days=30,
        late_fee_pct=1.5,
        contract_clause="MSA §1.1 (Terms)",
        ar_owner="Test Rep",
        contact_name="Casey Doe",
        contact_email="casey@test.example",
        contact_phone="+1-555-0100",
        archetype="unclassified",
    )

    invoice_rows, payment_rows = [], []
    for index, days_late in enumerate(paid_days_late):
        due = as_of - timedelta(days=400 - 30 * index)
        invoice_rows.append(
            dict(
                invoice_id=f"INV-P{index}",
                account_id="ACC-TEST",
                issue_date=due - timedelta(days=30),
                due_date=due,
                amount=amount,
                amount_paid=amount,
                status="paid",
                paid_date=due + timedelta(days=days_late),
                days_late=days_late,
                disputed=False,
                po_number="PO-1",
                description="Subscription",
            )
        )
        payment_rows.append(
            dict(
                payment_id=f"PAY-{index}",
                account_id="ACC-TEST",
                invoice_id=f"INV-P{index}",
                payment_date=due + timedelta(days=days_late),
                amount=amount,
                method="ACH",
            )
        )
    for index, dpd in enumerate(open_dpd):
        due = as_of - timedelta(days=dpd)
        invoice_rows.append(
            dict(
                invoice_id=f"INV-O{index}",
                account_id="ACC-TEST",
                issue_date=due - timedelta(days=30),
                due_date=due,
                amount=amount,
                amount_paid=0.0,
                status="open",
                paid_date=None,
                days_late=None,
                disputed=False,
                po_number="PO-2",
                description="Subscription",
            )
        )

    promise_rows = [
        dict(
            promise_id=f"PTP-{index}",
            account_id="ACC-TEST",
            invoice_id="INV-O0",
            promised_on=as_of - timedelta(days=40 - index),
            promised_date=as_of - timedelta(days=30 - index),
            promised_amount=amount,
            kept=kept,
            source="email",
        )
        for index, kept in enumerate(promises or [])
    ]

    email_rows = [
        dict(
            message_id=f"MSG-{index}",
            account_id="ACC-TEST",
            thread_id="THR-1",
            sent_at=as_of - timedelta(days=days_ago),
            direction=direction,
            author="Casey Doe" if direction == "inbound" else "Test Rep",
            subject="Invoice",
            body=body,
        )
        for index, (days_ago, direction, body) in enumerate(emails or [])
    ]

    def frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)

    return AccountSnapshot(
        customer=customer,
        invoices=frame(invoice_rows, ["invoice_id", "status", "due_date", "amount", "amount_paid", "disputed"]),
        payments=frame(payment_rows, ["payment_id", "payment_date", "amount"]),
        promises=frame(promise_rows, ["promise_id", "kept"]),
        emails=frame(email_rows, ["message_id", "sent_at", "direction", "body"]),
        as_of=as_of,
    )
