"""Typed payloads handed between agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class DisputeExtraction:
    """Ingestion agent output."""

    is_dispute: bool
    invoice_number: str | None = None
    po_number: str | None = None
    reason_code: str = "other"
    reason_text: str = ""
    disputed_amount: Decimal | None = None
    currency: str = "USD"
    customer_email: str = ""
    confidence: float = 0.0
    triage_note: str = ""
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["disputed_amount"] = (
            float(self.disputed_amount) if self.disputed_amount is not None else None
        )
        return data


@dataclass
class PolicyCheck:
    name: str
    passed: bool
    detail: str
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClauseCitation:
    contract_number: str
    clause_ref: str
    clause_title: str
    text: str
    source_pdf: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditDecision:
    """Auditor agent output."""

    decision: str  # valid | invalid | needs_more_info
    confidence: float
    rationale: str
    recommended_credit: Decimal = Decimal("0")
    evidence: list[str] = field(default_factory=list)
    checks: list[PolicyCheck] = field(default_factory=list)
    citation: ClauseCitation | None = None
    retrieved_clauses: list[ClauseCitation] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    queries: list[dict[str, Any]] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.decision == "valid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "recommended_credit": float(self.recommended_credit),
            "evidence": list(self.evidence),
            "checks": [c.to_dict() for c in self.checks],
            "citation": self.citation.to_dict() if self.citation else None,
            "retrieved_clauses": [c.to_dict() for c in self.retrieved_clauses],
            "facts": self.facts,
            "queries": self.queries,
            "missing_information": list(self.missing_information),
        }


@dataclass
class DraftedMessage:
    kind: str  # customer_email | supervisor_email | credit_memo
    recipient: str
    subject: str
    body: str


@dataclass
class NegotiationOutput:
    """Negotiation agent output."""

    drafts: list[DraftedMessage] = field(default_factory=list)
    credit_memo_number: str | None = None
    credit_amount: Decimal | None = None
    requires_approval: bool = True
    summary: str = ""
