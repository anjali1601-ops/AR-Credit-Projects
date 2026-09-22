"""Request and response models for the HTTP API."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class NewEmail(BaseModel):
    sender: str = Field(examples=["ap@northwind-retail.example"])
    subject: str = Field(examples=["Short payment on INV-2025-0148 - damaged crates"])
    body: str
    sender_name: str = ""
    recipient: str = "ar@acme-supply.example"
    process: bool = Field(
        default=True,
        description="Run the pipeline immediately instead of waiting for the poller.",
    )


class InboxMessageOut(BaseModel):
    id: int
    message_id: str
    sender: str
    sender_name: str
    subject: str
    received_at: dt.datetime
    status: str
    note: str
    case_id: int | None


class SupervisorAction(BaseModel):
    actor: str = "supervisor"
    note: str = ""


class CaseSummary(BaseModel):
    id: int
    case_number: str
    state: str
    owner_agent: str
    decision: str | None
    decision_confidence: float | None
    customer_name: str | None
    invoice_number: str | None
    reason_code: str | None
    disputed_amount: float | None
    approved_credit_amount: float | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CaseEventOut(BaseModel):
    seq: int
    event_type: str
    from_agent: str | None
    to_agent: str | None
    from_state: str | None
    to_state: str | None
    summary: str
    created_at: dt.datetime


class DraftOut(BaseModel):
    id: int
    kind: str
    recipient: str
    subject: str
    body: str
    status: str
    created_at: dt.datetime


class QueryOut(BaseModel):
    question: str
    generated_sql: str
    params: dict[str, Any]
    status: str
    row_count: int
    duration_ms: float


class CreditMemoOut(BaseModel):
    memo_number: str
    amount: float
    status: str
    reason_code: str
    issued_at: dt.datetime | None
    issued_by: str | None


class CaseDetail(CaseSummary):
    customer_code: str | None
    reason_text: str | None
    decision_rationale: str | None
    cited_clause_ref: str | None
    cited_clause_text: str | None
    resolution: str | None
    resolved_by: str | None
    evidence: list[str] = []
    checks: list[dict[str, Any]] = []
    retrieved_clauses: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}
    events: list[CaseEventOut] = []
    drafts: list[DraftOut] = []
    queries: list[QueryOut] = []
    credit_memo: CreditMemoOut | None = None
    approval_links: dict[str, str] = {}


class ActionResult(BaseModel):
    case_id: int
    case_number: str
    state: str
    resolution: str | None = None
    credit_memo: str | None = None
    summary: str


class ClauseHit(BaseModel):
    contract_number: str
    clause_ref: str
    clause_title: str
    text: str
    source_pdf: str
    score: float
