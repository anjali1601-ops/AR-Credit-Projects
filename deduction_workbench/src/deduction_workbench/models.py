"""Records the workbench passes between the classifier, the policy agent, and the router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseRecord:
    id: str
    customer_id: str
    customer_name: str
    debit_memo: str
    invoice_number: str
    claimed_amount: float
    claim_date: str
    backup_email: str
    debit_memo_text: str
    facts: dict[str, Any]
    agreement_ids: list[str]
    status: str


@dataclass
class Classification:
    reason_code: str
    reason_label: str
    confidence: float
    evidence: list[str]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class RetrievedClause:
    clause_id: str
    agreement_id: str
    heading: str
    kind: str
    score: int
    excerpt: str


@dataclass
class PolicyResult:
    outcome: str
    checks: list[Check]
    citation_id: str
    citation_heading: str
    citation_text: str
    agreement_id: str
    retrieved: list[RetrievedClause] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.outcome == "valid"


@dataclass
class RouteDecision:
    queue: str
    queue_label: str
    organization: str
    desk: str
    rationale: str
    auto_send: bool = False


@dataclass
class NarrativeBrief:
    customer_name: str
    debit_memo: str
    invoice_number: str
    claimed_amount: float
    reason_code: str
    reason_label: str
    outcome: str
    queue: str
    queue_label: str
    citation_id: str
    citation_text: str
    checks: list[Check]
