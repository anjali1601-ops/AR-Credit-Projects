"""Shared case for one teammate. The three agents read and write this object."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Rating = Literal["exceeds", "meets", "below"]
Relationship = Literal["engaged", "strained", "withdrawing"]
Destination = Literal["hr", "employee"]
NoteSource = Literal["one_on_one", "qa"]


class CashApplication(BaseModel):
    amount: int
    applied_on: date
    reference: str


class Promise(BaseModel):
    amount: int
    kept: bool
    customer: str
    due_on: date


class Dispute(BaseModel):
    customer: str
    opened_on: date
    closed_on: date | None = None


class QAReview(BaseModel):
    score: float
    comment: str
    reviewed_on: date


class OneOnOne(BaseModel):
    met_on: date
    note: str


class SourceNote(BaseModel):
    source: NoteSource
    observed_on: date
    text: str


class Teammate(BaseModel):
    id: str
    name: str
    role: str
    tenure_months: int
    scenario: str
    scenario_label: str


class MetricSnapshot(BaseModel):
    cash_applied: int
    cash_target: int
    cash_ratio: float
    ramp: bool
    promises_made: int
    promises_kept: int
    promise_kept_rate: float
    dispute_cycle_days: float
    disputes_closed: int
    quality_score: float
    quality_reviews: int
    cases_closed: int
    workload_expectation: int
    overtime_hours: list[float]
    average_weekly_overtime: float
    overtime_weeks_high: int
    sustained_overtime: bool
    bands: dict[str, Rating]


class Evidence(BaseModel):
    source: NoteSource
    observed_on: str
    text: str
    matched_phrases: list[str] = Field(default_factory=list)


class SentimentFinding(BaseModel):
    label: Relationship
    summary: str
    evidence: list[Evidence]


class Judgment(BaseModel):
    metric: str
    band: Rating
    sentence: str


class PerformanceReview(BaseModel):
    rating: Rating
    reason: str
    judgments: list[Judgment]
    narrative: str
    provider: str


class AttritionFinding(BaseModel):
    flight_risk: bool
    gates: dict[str, bool]
    reasons: list[str]
    stay_conversation: str | None = None
    provider: str


class Transmission(BaseModel):
    destination: Destination
    rating: Rating
    sent_at: str
    body: str


class PeopleCase(BaseModel):
    teammate_id: str
    name: str
    role: str
    tenure_months: int
    scenario: str
    scenario_label: str
    period: str
    metrics: MetricSnapshot
    sentiment: SentimentFinding
    performance: PerformanceReview
    attrition: AttritionFinding
    coaching: str
    coaching_provider: str
    confirmed_rating: Rating | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    transmissions: list[Transmission] = Field(default_factory=list)


class TeamCard(BaseModel):
    teammate_id: str
    name: str
    role: str
    tenure_months: int
    scenario: str
    scenario_label: str
    sentiment: Relationship | None = None
    rating: Rating | None = None
    flight_risk: bool | None = None
    confirmed_rating: Rating | None = None
    sent: list[Destination] = Field(default_factory=list)
