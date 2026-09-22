"""Typed records shared by every agent in the graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

Archetype = Literal[
    "reliable_but_late",
    "deteriorating_avoidant",
    "high_risk_delinquent",
    "unclassified",
]

RelationshipLabel = Literal["cooperative", "neutral", "frustrated", "avoidant", "unresponsive"]

Tone = Literal["warm", "neutral_professional", "firm", "formal_strict"]

Channel = Literal["email", "phone", "sms", "certified_letter"]

EscalationStage = Literal[
    "courtesy_reminder",
    "firm_follow_up",
    "escalation_notice",
    "final_demand",
    "pre_legal_notice",
]

STAGE_ORDER: list[str] = [
    "courtesy_reminder",
    "firm_follow_up",
    "escalation_notice",
    "final_demand",
    "pre_legal_notice",
]


def stage_index(stage: str) -> int:
    return STAGE_ORDER.index(stage)


class Customer(BaseModel):
    account_id: str
    name: str
    industry: str
    segment: str
    relationship_start: date
    annual_contract_value: float
    credit_limit: float
    payment_terms_days: int
    late_fee_pct: float
    contract_clause: str
    ar_owner: str
    contact_name: str
    contact_email: str
    contact_phone: str
    # Ground-truth label, used by the seed generator and the eval harness only.
    archetype: Archetype = "unclassified"


class PaymentMetrics(BaseModel):
    invoices_on_record: int = 0
    invoices_paid: int = 0
    open_invoices: int = 0
    open_balance: float = 0.0
    past_due_balance: float = 0.0
    largest_open_invoice: float = 0.0
    oldest_days_past_due: int = 0
    weighted_days_past_due: float = 0.0
    aging_0_30: float = 0.0
    aging_31_60: float = 0.0
    aging_61_90: float = 0.0
    aging_90_plus: float = 0.0
    avg_days_late: float = 0.0
    median_days_late: float = 0.0
    days_late_stddev: float = 0.0
    worst_days_late: int = 0
    pct_invoices_paid_late: float = 0.0
    lateness_trend: float = 0.0
    days_since_last_payment: int = 0
    promises_made: int = 0
    promises_broken: int = 0
    partial_payment_ratio: float = 0.0
    disputed_open_invoices: int = 0
    days_since_last_inbound_email: int = 0
    unanswered_outbound_emails: int = 0
    exposure_vs_credit_limit: float = 0.0
    tenure_months: int = 0


class RiskProfile(BaseModel):
    account_id: str
    archetype: Archetype
    risk_score: float = Field(ge=0, le=100)
    risk_band: Literal["low", "moderate", "elevated", "severe"]
    predictability: Literal["metronomic", "variable", "erratic"]
    expected_days_late: float
    recovery_outlook: Literal["self_correcting", "needs_pressure", "at_risk_of_write_off"]
    metrics: PaymentMetrics
    signals: list[str] = Field(default_factory=list)
    narrative: str = ""


class MessageSentiment(BaseModel):
    message_id: str
    sent_at: date
    polarity: Literal["positive", "neutral", "negative"]
    score: float
    cues: list[str] = Field(default_factory=list)


class SentimentAssessment(BaseModel):
    account_id: str
    relationship_label: RelationshipLabel
    relationship_health: float = Field(ge=0, le=100)
    polarity_score: float
    responsiveness_score: float = Field(ge=0, le=100)
    engagement_trend: Literal["improving", "stable", "declining", "silent"]
    backend: str
    per_message: list[MessageSentiment] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    summary: str = ""


class PlannedStep(BaseModel):
    step_number: int
    day_offset: int
    send_on: date
    channel: Channel
    intent: str
    tone: Tone


class StrategyDecision(BaseModel):
    account_id: str
    stage: EscalationStage
    tone: Tone
    channels: list[Channel]
    urgency: Literal["low", "medium", "high", "critical"]
    offer_payment_plan: bool = False
    cite_contract_terms: bool = False
    late_fee_warning: bool = False
    service_hold_warning: bool = False
    legal_referral: bool = False
    human_approval_required: bool = False
    escalate_to_owner: bool = False
    plan: list[PlannedStep] = Field(default_factory=list)
    policy_trace: list[str] = Field(default_factory=list)
    rationale: str = ""


class DraftedStep(BaseModel):
    step_number: int
    day_offset: int
    send_on: date
    channel: Channel
    intent: str
    tone: Tone
    subject: Optional[str] = None
    body: str
    talking_points: list[str] = Field(default_factory=list)


class CommunicationSequence(BaseModel):
    account_id: str
    stage: EscalationStage
    tone: Tone
    steps: list[DraftedStep] = Field(default_factory=list)
    generated_by: str = "mock"


class ReviewResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    checks_run: int = 0


class RunResult(BaseModel):
    account_id: str
    run_id: str
    as_of: date
    customer: Customer
    profile: RiskProfile
    sentiment: SentimentAssessment
    strategy: StrategyDecision
    sequence: CommunicationSequence
    review: ReviewResult
    revisions: int = 0
    agent_log: list[str] = Field(default_factory=list)
    trace_reference: Optional[str] = None

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass
class AccountSnapshot:
    """Pandas view of one account, assembled once and shared across agents."""

    customer: Customer
    invoices: pd.DataFrame
    payments: pd.DataFrame
    promises: pd.DataFrame
    emails: pd.DataFrame
    as_of: date

    @property
    def open_invoices(self) -> pd.DataFrame:
        return self.invoices[self.invoices["status"] != "paid"].copy()

    @property
    def inbound_emails(self) -> pd.DataFrame:
        return self.emails[self.emails["direction"] == "inbound"].copy()
