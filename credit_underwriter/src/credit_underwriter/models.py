"""Domain models for credit applications, financial statements, and analysis output.

Every model here is a plain Pydantic value object. Nothing in this module talks to
an LLM, a vector store, or the filesystem, which keeps the underwriting record
serialisable and therefore auditable.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class Severity(StrEnum):
    """Ordered severity scale shared by financial and external risk findings."""

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @classmethod
    def from_rank(cls, rank: int) -> Severity:
        clamped = max(0, min(rank, len(_SEVERITY_ORDER) - 1))
        return _SEVERITY_ORDER[clamped]

    def escalate(self, steps: int = 1) -> Severity:
        return Severity.from_rank(self.rank + steps)

    def de_escalate(self, steps: int = 1) -> Severity:
        return Severity.from_rank(self.rank - steps)


def max_severity(severities: Iterable[Severity], default: Severity = Severity.NONE) -> Severity:
    """Highest severity by rank.

    ``Severity`` is a ``StrEnum``, so the builtin ``max`` would compare the
    member *strings* and rank "none" above "critical". Always use this instead.
    """
    return max(severities, key=lambda s: s.rank, default=default)


_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.NONE,
    Severity.LOW,
    Severity.MODERATE,
    Severity.HIGH,
    Severity.CRITICAL,
)
_SEVERITY_RANK: dict[Severity, int] = {s: i for i, s in enumerate(_SEVERITY_ORDER)}


class RiskCategory(StrEnum):
    """Buckets the risk searcher classifies retrieved documents into."""

    INSOLVENCY = "insolvency"
    PAYMENT_DEFAULT = "payment_default"
    LITIGATION = "litigation"
    GOVERNANCE = "governance"
    REGULATORY = "regulatory"
    COVENANT = "covenant"
    SECURITY_AND_LIENS = "security_and_liens"
    COUNTRY = "country"
    INDUSTRY = "industry"
    CONCENTRATION = "concentration"
    OPERATIONS = "operations"
    TRADE_PAYMENT = "trade_payment"
    POSITIVE_MOMENTUM = "positive_momentum"


#: Categories that can single-handedly veto an approval regardless of the spread.
BLOCKING_CATEGORIES: frozenset[RiskCategory] = frozenset(
    {
        RiskCategory.INSOLVENCY,
        RiskCategory.PAYMENT_DEFAULT,
        RiskCategory.GOVERNANCE,
    }
)


class Direction(StrEnum):
    ADVERSE = "adverse"
    SUPPORTIVE = "supportive"
    NEUTRAL = "neutral"


class TrendDirection(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    DETERIORATING = "deteriorating"


class Recommendation(StrEnum):
    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    DECLINE = "decline"

    @property
    def label(self) -> str:
        return {
            Recommendation.APPROVE: "Approve",
            Recommendation.APPROVE_WITH_CONDITIONS: "Approve with conditions",
            Recommendation.DECLINE: "Decline",
        }[self]


class AuditOpinion(StrEnum):
    AUDITED = "audited"
    REVIEWED = "reviewed"
    MANAGEMENT_PREPARED = "management_prepared"


class EvidenceKind(StrEnum):
    STATEMENT_LINE = "statement_line"
    RATIO = "ratio"
    TREND = "trend"
    RATING = "rating"
    DOCUMENT = "document"
    APPLICATION_FIELD = "application_field"
    COMPUTED_SIGNAL = "computed_signal"
    POLICY_RULE = "policy_rule"


# --------------------------------------------------------------------------------------
# Application intake
# --------------------------------------------------------------------------------------


class IncomeStatement(BaseModel):
    """Normalised income statement. Derived lines are recomputed, never trusted."""

    model_config = ConfigDict(extra="forbid")

    revenue: float
    cost_of_goods_sold: float
    operating_expenses: float
    depreciation_amortization: float
    interest_expense: float
    income_tax_expense: float = 0.0
    other_income: float = 0.0

    @computed_field
    @property
    def gross_profit(self) -> float:
        return self.revenue - self.cost_of_goods_sold

    @computed_field
    @property
    def ebitda(self) -> float:
        return self.gross_profit - self.operating_expenses + self.other_income

    @computed_field
    @property
    def ebit(self) -> float:
        return self.ebitda - self.depreciation_amortization

    @computed_field
    @property
    def pretax_income(self) -> float:
        return self.ebit - self.interest_expense

    @computed_field
    @property
    def net_income(self) -> float:
        return self.pretax_income - self.income_tax_expense


class BalanceSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_and_equivalents: float
    accounts_receivable: float
    inventory: float
    other_current_assets: float
    net_property_plant_equipment: float
    intangible_assets: float
    other_non_current_assets: float

    accounts_payable: float
    short_term_debt: float
    other_current_liabilities: float
    long_term_debt: float
    other_non_current_liabilities: float
    total_equity: float

    @computed_field
    @property
    def total_current_assets(self) -> float:
        return (
            self.cash_and_equivalents
            + self.accounts_receivable
            + self.inventory
            + self.other_current_assets
        )

    @computed_field
    @property
    def total_assets(self) -> float:
        return (
            self.total_current_assets
            + self.net_property_plant_equipment
            + self.intangible_assets
            + self.other_non_current_assets
        )

    @computed_field
    @property
    def total_current_liabilities(self) -> float:
        return self.accounts_payable + self.short_term_debt + self.other_current_liabilities

    @computed_field
    @property
    def total_liabilities(self) -> float:
        return (
            self.total_current_liabilities
            + self.long_term_debt
            + self.other_non_current_liabilities
        )

    @computed_field
    @property
    def total_debt(self) -> float:
        return self.short_term_debt + self.long_term_debt

    @computed_field
    @property
    def net_debt(self) -> float:
        return self.total_debt - self.cash_and_equivalents

    @computed_field
    @property
    def working_capital(self) -> float:
        return self.total_current_assets - self.total_current_liabilities

    @computed_field
    @property
    def tangible_net_worth(self) -> float:
        return self.total_equity - self.intangible_assets


class CashFlowStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_from_operations: float
    capital_expenditures: float
    dividends_and_distributions: float = 0.0

    @computed_field
    @property
    def free_cash_flow(self) -> float:
        return self.cash_from_operations - self.capital_expenditures


class FinancialStatement(BaseModel):
    """One fiscal year of submitted financials."""

    model_config = ConfigDict(extra="forbid")

    fiscal_year: int
    period_end: str
    currency: str = "USD"
    opinion: AuditOpinion
    months_covered: int = 12
    income_statement: IncomeStatement
    balance_sheet: BalanceSheet
    cash_flow: CashFlowStatement

    @property
    def period_label(self) -> str:
        return f"FY{self.fiscal_year}"


class TradeReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str
    relationship_years: float
    high_credit: float
    terms: str
    days_beyond_terms: float
    comment: str


class CustomerConcentration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str
    percent_of_revenue: float = Field(ge=0, le=100)
    contract_expiry: str | None = None


class CreditEnhancement(BaseModel):
    """A structural mitigant the applicant offers or already has in place."""

    model_config = ConfigDict(extra="forbid")

    key: str
    kind: Literal[
        "parent_guarantee",
        "personal_guarantee",
        "credit_insurance",
        "security_deposit",
        "letter_of_credit",
        "payment_history",
        "collateral",
    ]
    description: str
    coverage_amount: float | None = None
    coverage_percent: float | None = None
    #: Risk categories this enhancement is capable of offsetting.
    offsets_categories: list[RiskCategory] = Field(default_factory=list)


class Disclosures(BaseModel):
    """Self-declared answers from the application form.

    These matter because the reconciler cross-checks them against what the risk
    searcher actually finds; a contradiction is itself an underwriting signal.
    """

    model_config = ConfigDict(extra="forbid")

    material_litigation: bool
    prior_insolvency_or_bankruptcy: bool
    covenant_breach_last_24m: bool
    tax_or_regulatory_penalties: bool
    change_of_control_pending: bool = False
    notes: str = ""


class CreditApplication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    legal_name: str
    trading_name: str | None = None
    entity_type: str
    country: str
    country_code: str
    industry: str
    industry_code: str
    years_in_business: float
    employees: int
    currency: str = "USD"
    requested_limit: float
    requested_terms_days: int
    purpose: str
    submitted_at: str
    #: Most recent fiscal year first is *not* assumed; the engine sorts.
    statements: list[FinancialStatement]
    trade_references: list[TradeReference] = Field(default_factory=list)
    customer_concentration: list[CustomerConcentration] = Field(default_factory=list)
    credit_enhancements: list[CreditEnhancement] = Field(default_factory=list)
    disclosures: Disclosures
    prior_relationship_months: int = 0
    prior_worst_days_beyond_terms: float | None = None

    @property
    def ordered_statements(self) -> list[FinancialStatement]:
        return sorted(self.statements, key=lambda s: s.fiscal_year)

    @property
    def latest_statement(self) -> FinancialStatement:
        return self.ordered_statements[-1]


# --------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    """A single citable fact.

    ``numeric_values`` is what makes the citation check possible: the critic pulls
    every number out of a claim's prose and requires it to appear in the numeric
    values of at least one cited evidence item.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    kind: EvidenceKind
    label: str
    display_value: str | None = None
    source: str
    detail: str | None = None
    numeric_values: list[float] = Field(default_factory=list)

    def citation(self) -> str:
        if self.display_value:
            return f"{self.label}: {self.display_value} ({self.source})"
        return f"{self.label} ({self.source})"


# --------------------------------------------------------------------------------------
# Financial engine output
# --------------------------------------------------------------------------------------


class RatioCategory(StrEnum):
    LIQUIDITY = "liquidity"
    LEVERAGE = "leverage"
    COVERAGE = "coverage"
    PROFITABILITY = "profitability"
    EFFICIENCY = "efficiency"


class Ratio(BaseModel):
    """A computed ratio plus everything needed to defend it in an audit."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    category: RatioCategory
    period: str
    value: float | None
    unit: Literal["x", "%", "days", "currency"]
    formula: str
    inputs: list[str] = Field(default_factory=list)
    #: Populated when the ratio is mathematically defined but not interpretable,
    #: e.g. debt/equity against negative equity.
    not_meaningful_reason: str | None = None

    @property
    def display(self) -> str:
        return format_ratio_value(self.value, self.unit)


class SpreadException(BaseModel):
    """An internal-consistency failure found while spreading the statements."""

    model_config = ConfigDict(extra="forbid")

    period: str
    check: str
    detail: str
    severity: Severity
    delta: float


class Trend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    unit: Literal["x", "%", "days", "currency"]
    first_period: str
    last_period: str
    first_value: float
    last_value: float
    change: float
    percent_change: float | None
    cagr: float | None
    direction: TrendDirection
    higher_is_better: bool
    inputs: list[str] = Field(default_factory=list)


class ScorecardFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    weight: float
    raw_value: float | None
    unit: Literal["x", "%", "days", "currency", "years"]
    score: float
    band_label: str
    inputs: list[str] = Field(default_factory=list)

    @property
    def weighted_score(self) -> float:
        return self.weight * self.score


class InternalRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: int
    band_label: str
    composite_score: float
    estimated_pd_percent: float
    factors: list[ScorecardFactor]


class LimitGuidance(BaseModel):
    """Deterministic credit-limit capacity test, before risk adjustment."""

    model_config = ConfigDict(extra="forbid")

    requested_limit: float
    tangible_net_worth_capacity: float
    cash_flow_capacity: float
    working_capital_capacity: float
    binding_constraint: str
    rating_multiplier: float
    indicative_limit: float
    indicative_terms_days: int
    inputs: list[str] = Field(default_factory=list)


class FinancialFinding(BaseModel):
    """A rule-triggered observation about the spread. Never LLM-authored."""

    model_config = ConfigDict(extra="forbid")

    key: str
    category: RatioCategory | Literal["structure", "data_quality", "cash_flow"]
    severity: Severity
    direction: Direction
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)


class FinancialAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    periods: list[str]
    currency: str
    ratios: list[Ratio]
    trends: list[Trend]
    rating: InternalRating
    limit_guidance: LimitGuidance
    findings: list[FinancialFinding]
    spread_exceptions: list[SpreadException]
    #: Narrative written by the analyst agent over the facts above.
    narrative: list[NarrativePoint] = Field(default_factory=list)

    def ratio(self, key: str, period: str | None = None) -> Ratio | None:
        target = period or (self.periods[-1] if self.periods else None)
        for r in self.ratios:
            if r.key == key and (target is None or r.period == target):
                return r
        return None

    def trend(self, key: str) -> Trend | None:
        return next((t for t in self.trends if t.key == key), None)


class NarrativePoint(BaseModel):
    """A sentence of interpretation bound to the evidence it came from."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Risk searcher output
# --------------------------------------------------------------------------------------


class RiskDocument(BaseModel):
    """A corpus document. The seeded corpus is entirely synthetic."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    title: str
    source: str
    doc_type: Literal[
        "news",
        "filing",
        "litigation",
        "industry_report",
        "country_report",
        "trade_reference",
        "regulatory",
    ]
    published_date: str
    text: str
    applicant_id: str | None = None
    country_code: str | None = None
    industry_code: str | None = None

    def excerpt(self, limit: int = 260) -> str:
        if len(self.text) <= limit:
            return self.text
        return self.text[: limit - 1].rstrip() + "…"


class RetrievedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: RiskDocument
    query: str
    score: float
    rank: int


class RiskFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    category: RiskCategory
    severity: Severity
    direction: Direction
    summary: str
    rationale: str
    published_date: str
    months_old: float
    #: Severity before the recency adjustment, kept for auditability.
    raw_severity: Severity
    evidence_ids: list[str] = Field(default_factory=list)


class ConcentrationSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    largest_customer: str | None
    largest_share_percent: float
    top3_share_percent: float
    herfindahl_index: float
    severity: Severity
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    queries: list[str]
    retrieved: list[RetrievedDocument]
    findings: list[RiskFinding]
    concentration: ConcentrationSignal
    country_risk_tier: int
    industry_risk_tier: int
    overall_severity: Severity
    #: Fractional so quarter-notch contributions from tiering are not lost.
    proposed_notches: float
    blockers: list[str] = Field(default_factory=list)
    narrative: list[NarrativePoint] = Field(default_factory=list)
    retrieval_backend: str = "unknown"
    live_search_used: bool = False

    @property
    def adverse_findings(self) -> list[RiskFinding]:
        return [f for f in self.findings if f.direction is Direction.ADVERSE]

    @property
    def supportive_findings(self) -> list[RiskFinding]:
        return [f for f in self.findings if f.direction is Direction.SUPPORTIVE]


# --------------------------------------------------------------------------------------
# Reconciliation output
# --------------------------------------------------------------------------------------


class ConflictResolution(BaseModel):
    """A recorded disagreement and the rule that settled it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    rule: str
    financial_position: str
    risk_position: str
    resolution: str
    prevailing_side: Literal["financial", "risk", "both", "policy"]
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class MitigantOffset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enhancement_key: str
    description: str
    offsets_finding: str
    notch_credit: float
    evidence_ids: list[str] = Field(default_factory=list)


class DecisionPoint(BaseModel):
    """A strength, risk factor, or condition, each carrying its citations."""

    model_config = ConfigDict(extra="forbid")

    key: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class CreditDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    recommendation: Recommendation
    standalone_grade: int
    final_grade: int
    final_band_label: str
    applied_notches: float
    approved_limit: float
    approved_terms_days: int
    requested_limit: float
    requested_terms_days: int
    limit_haircut_percent: float
    security_required: bool
    review_frequency_months: int
    conflicts: list[ConflictResolution] = Field(default_factory=list)
    mitigant_offsets: list[MitigantOffset] = Field(default_factory=list)
    strengths: list[DecisionPoint] = Field(default_factory=list)
    risk_factors: list[DecisionPoint] = Field(default_factory=list)
    conditions: list[DecisionPoint] = Field(default_factory=list)
    policy_notes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Memo + critique
# --------------------------------------------------------------------------------------


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class MemoSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    heading: str
    claims: list[Claim] = Field(default_factory=list)


class UnderwritingMemo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    legal_name: str
    recommendation: Recommendation
    approved_limit: float
    approved_terms_days: int
    final_grade: int
    final_band_label: str
    currency: str
    as_of_date: str
    sections: list[MemoSection] = Field(default_factory=list)
    revision: int = 0

    def section(self, key: str) -> MemoSection | None:
        return next((s for s in self.sections if s.key == key), None)

    @property
    def claims(self) -> list[Claim]:
        return [c for s in self.sections for c in s.claims]


class CritiqueIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "missing_section",
        "empty_section",
        "uncited_claim",
        "unresolvable_evidence",
        "unsupported_number",
        "decision_mismatch",
        "insufficient_coverage",
    ]
    detail: str
    section_key: str | None = None
    claim_id: str | None = None


class Critique(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    revision: int
    issues: list[CritiqueIssue] = Field(default_factory=list)
    claims_checked: int = 0
    citations_checked: int = 0
    document_citations: int = 0
    ratio_citations: int = 0


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def format_ratio_value(value: float | None, unit: str) -> str:
    """Render a ratio for prose. Kept next to the models so the memo, the API and
    the citation checker all format numbers identically."""
    if value is None:
        return "n.m."
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "days":
        return f"{value:.0f} days"
    if unit == "years":
        return f"{value:.0f} years"
    return format_currency(value)


def format_currency(value: float, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, f"{currency} ")
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{sign}{symbol}{magnitude / 1_000_000:.2f}m"
    if magnitude >= 1_000:
        return f"{sign}{symbol}{magnitude / 1_000:.0f}k"
    return f"{sign}{symbol}{magnitude:,.0f}"


FinancialAnalysis.model_rebuild()
