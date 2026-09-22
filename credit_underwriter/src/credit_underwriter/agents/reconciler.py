"""Reconciliation: the supervisor's fan-in step.

This is where the two specialist views are resolved into one decision, not
concatenated. The rules below run in a fixed precedence order and each one that
fires records a :class:`ConflictResolution` naming the rule, both positions, the
outcome, and which side prevailed.

The rules, in order:

1. ``hard_blocker_override`` -- a critical insolvency, payment-default, or
   governance finding declines the application regardless of how the spread
   reads. Risk prevails over the financial view outright.
2. ``disclosure_integrity`` -- the applicant declared no matter of a kind the
   searcher then found. The contradiction is itself a signal, so severity is
   escalated and an explanation becomes a condition.
3. ``trend_vs_external_signal`` -- the spread and the external evidence point in
   opposite directions. Resolved on recency and specificity: a dated,
   obligor-specific document published after the last statement date beats a
   statement trend, and a purely systemic signal does not.
4. ``mitigant_offsets`` -- structural enhancements are credited back against the
   findings they actually address, bounded so they cannot rescue an obligor more
   than two grades.
5. ``limit_reconciliation`` -- the analyst's capacity-based limit against the
   risk-adjusted limit.
6. ``appetite_policy`` -- the final grade against the credit appetite matrix.
"""

from __future__ import annotations

import math

from ..evidence import EvidenceRegistry, extract_numbers
from ..finance.rating import (
    BAND_LABELS,
    RATING_LIMIT_MULTIPLIER,
    RATING_MAX_TERMS_DAYS,
    REVIEW_FREQUENCY_MONTHS,
    round_limit,
)
from ..llm import LLMRequest
from ..models import (
    ConflictResolution,
    CreditApplication,
    CreditDecision,
    CreditEnhancement,
    DecisionPoint,
    Direction,
    EvidenceKind,
    FinancialAnalysis,
    MitigantOffset,
    Recommendation,
    RiskAssessment,
    RiskCategory,
    RiskFinding,
    Severity,
    TrendDirection,
    format_currency,
)
from ..risk import CONCENTRATION_HAIRCUT
from ..state import AgentMessage, UnderwritingState
from .context import GraphContext

AGENT = "supervisor"

SYSTEM = (
    "You are the credit committee chair reconciling a financial analysis against an "
    "external risk review for an enterprise B2B credit application."
)

RATIONALE_SCHEMA = {
    "rationales": "array of objects with keys 'key' (string) and 'rationale' (string)",
}

# --- mitigant policy ------------------------------------------------------------------

#: Enhancements that substitute for the obligor's own credit rather than
#: addressing one specific finding.
CREDIT_SUBSTITUTES = frozenset({"parent_guarantee", "letter_of_credit", "credit_insurance"})

#: Maximum notch credit each kind of enhancement can contribute.
MITIGANT_NOTCH_CREDIT: dict[str, float] = {
    "parent_guarantee": 1.5,
    "letter_of_credit": 1.5,
    "credit_insurance": 1.0,
    "security_deposit": 0.5,
    "payment_history": 0.5,
    "personal_guarantee": 0.25,
    "collateral": 0.0,
}

#: Total credit available from all enhancements together.
MAX_MITIGANT_CREDIT = 3.5

#: An enhancement stack cannot improve an obligor by more than this many grades.
MAX_MITIGANT_GRADE_UPLIFT = 2

#: A guarantee must cover at least this share of the request to count.
MIN_GUARANTEE_COVERAGE_RATIO = 0.5

#: Direct trading history counts as a mitigant only when it is long and clean.
MIN_HISTORY_MONTHS = 24
MAX_HISTORY_DAYS_BEYOND_TERMS = 15.0

#: Notch penalty for contradicting a declaration on the application form.
DISCLOSURE_INTEGRITY_NOTCHES = 0.5

#: Relief when the only adverse signals are systemic rather than obligor-specific.
SYSTEMIC_ONLY_RELIEF_NOTCHES = 0.5

#: Which declaration each risk category would contradict.
DISCLOSURE_CATEGORY_MAP: dict[str, tuple[RiskCategory, ...]] = {
    "material_litigation": (RiskCategory.LITIGATION,),
    "prior_insolvency_or_bankruptcy": (RiskCategory.INSOLVENCY,),
    "covenant_breach_last_24m": (RiskCategory.COVENANT,),
    "tax_or_regulatory_penalties": (RiskCategory.REGULATORY,),
}

DISCLOSURE_LABELS: dict[str, str] = {
    "material_litigation": "material litigation",
    "prior_insolvency_or_bankruptcy": "prior insolvency or bankruptcy",
    "covenant_breach_last_24m": "a covenant breach in the last 24 months",
    "tax_or_regulatory_penalties": "tax or regulatory penalties",
}

#: Categories that describe a sector or jurisdiction rather than the obligor.
SYSTEMIC_CATEGORIES = frozenset({RiskCategory.INDUSTRY, RiskCategory.COUNTRY})

#: Grades at or beyond which open account is outside appetite.
DECLINE_GRADE = 8
CONDITIONS_GRADE = 6


def reconcile(state: UnderwritingState, context: GraphContext) -> dict:
    application = state["application"]
    analysis = state["financial_analysis"]
    risk = state["risk_assessment"]
    registry = EvidenceRegistry(state.get("evidence", []))

    conflicts: list[ConflictResolution] = []
    policy_notes: list[str] = []
    notches = risk.proposed_notches
    escalated: set[str] = set()

    # -- rule 1: hard blocker ------------------------------------------------
    blocking_findings = [f for f in risk.findings if f.key in risk.blockers]
    hard_blocked = bool(blocking_findings)
    if hard_blocked:
        conflicts.append(
            ConflictResolution(
                key="hard_blocker_override",
                rule="hard_blocker_override",
                financial_position=(
                    f"The scorecard placed the applicant at standalone grade "
                    f"{analysis.rating.grade} ({analysis.rating.band_label}) with an indicative "
                    f"limit of "
                    f"{format_currency(analysis.limit_guidance.indicative_limit, analysis.currency)}"
                ),
                risk_position=(
                    f"External research returned {len(blocking_findings)} critical finding(s) in "
                    f"a category policy treats as a decline trigger: "
                    + "; ".join(f"{f.category.value} — {f.summary}" for f in blocking_findings)
                ),
                resolution=(
                    "the application is declined and no open-account limit is offered, because a "
                    "critical insolvency, payment-default, or governance finding cannot be priced "
                    "or structured around"
                ),
                prevailing_side="risk",
                evidence_ids=[
                    eid
                    for f in blocking_findings
                    for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                ],
            )
        )
        policy_notes.append(
            "Hard blocker override applied: critical adverse findings decline the application "
            "independently of the financial scorecard."
        )

    # -- rule 2: disclosure integrity ---------------------------------------
    for field, categories in DISCLOSURE_CATEGORY_MAP.items():
        declared = bool(getattr(application.disclosures, field))
        matches = [
            f
            for f in risk.adverse_findings
            if f.category in categories and f.severity.rank >= Severity.MODERATE.rank
        ]
        immaterial = [
            f
            for f in risk.adverse_findings
            if f.category in categories and f.severity.rank < Severity.MODERATE.rank
        ]
        if not declared and matches:
            notches += DISCLOSURE_INTEGRITY_NOTCHES
            escalated.update(f.key for f in matches)
            conflicts.append(
                ConflictResolution(
                    key=f"disclosure_integrity_{field}",
                    rule="disclosure_integrity",
                    financial_position=(
                        f"The application form declares no {DISCLOSURE_LABELS[field]}"
                    ),
                    risk_position=(
                        f"External research found {len(matches)} matching item(s) at moderate "
                        f"severity or above: "
                        + "; ".join(f.summary for f in matches)
                    ),
                    resolution=(
                        f"the searcher's evidence is accepted, severity is escalated one level, "
                        f"{DISCLOSURE_INTEGRITY_NOTCHES:.2f} additional notches are applied, and a "
                        "written explanation of the omission becomes a condition"
                    ),
                    prevailing_side="risk",
                    evidence_ids=[
                        "app:disclosures",
                        *[
                            eid
                            for f in matches
                            for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                        ],
                    ],
                )
            )
            policy_notes.append(
                f"Disclosure integrity: declaration of no {DISCLOSURE_LABELS[field]} contradicted "
                "by external evidence."
            )
        elif not declared and immaterial:
            conflicts.append(
                ConflictResolution(
                    key=f"disclosure_immaterial_{field}",
                    rule="disclosure_integrity",
                    financial_position=(
                        f"The application form declares no {DISCLOSURE_LABELS[field]}"
                    ),
                    risk_position=(
                        "External research surfaced a matter in that category, but below the "
                        "materiality threshold: "
                        + "; ".join(f.summary for f in immaterial)
                    ),
                    resolution=(
                        "no contradiction is recorded, because the matter is immaterial and stale "
                        "enough that policy would not expect it to be declared"
                    ),
                    prevailing_side="both",
                    evidence_ids=[
                        "app:disclosures",
                        *[
                            eid
                            for f in immaterial
                            for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                        ],
                    ],
                )
            )

    # -- rule 3: trend against external signal -------------------------------
    conflict, notch_delta, note = _resolve_trend_conflict(application, analysis, risk)
    if conflict is not None:
        conflicts.append(conflict)
        notches += notch_delta
        if note:
            policy_notes.append(note)

    notches = max(0.0, round(notches * 4) / 4)

    # -- rule 4: mitigant offsets -------------------------------------------
    offsets, mitigant_credit = _apply_mitigants(application, risk, hard_blocked)
    if offsets:
        conflicts.append(
            ConflictResolution(
                key="mitigant_offsets",
                rule="mitigant_offsets",
                financial_position=(
                    f"The standalone spread supports grade {analysis.rating.grade} before any "
                    "structural support is considered"
                ),
                risk_position=(
                    f"External research proposed {risk.proposed_notches:.2f} notches of downward "
                    "adjustment"
                ),
                resolution=(
                    f"{len(offsets)} structural enhancement(s) are credited back for "
                    f"{mitigant_credit:.2f} notches against the specific findings they address, "
                    f"subject to the {MAX_MITIGANT_GRADE_UPLIFT}-grade cap on enhancement uplift"
                ),
                prevailing_side="both",
                evidence_ids=[eid for o in offsets for eid in o.evidence_ids],
            )
        )
        policy_notes.append(
            f"Mitigant offsets credited {mitigant_credit:.2f} notches across "
            f"{len(offsets)} enhancement(s)."
        )
    elif hard_blocked and application.credit_enhancements:
        policy_notes.append(
            "Credit enhancements were not credited because a hard blocker cannot be mitigated by "
            "structure."
        )

    # -- grade arithmetic ----------------------------------------------------
    standalone = analysis.rating.grade
    raw_grade = standalone + notches - mitigant_credit
    final_grade = _clamp_grade(
        int(math.floor(raw_grade + 0.5)),
        floor=standalone - MAX_MITIGANT_GRADE_UPLIFT,
    )
    if hard_blocked:
        final_grade = max(final_grade, DECLINE_GRADE)
    applied_notches = round(final_grade - standalone, 2)
    if raw_grade > 10:
        policy_notes.append(
            f"The unadjusted arithmetic gave {raw_grade:.2f} notches of grade, which is beyond the "
            "bottom of the 10-point scale; the grade is reported at 10."
        )

    # -- rule 6: appetite ----------------------------------------------------
    recommendation, appetite_note = _appetite(final_grade, hard_blocked, risk)

    # -- limit and terms -----------------------------------------------------
    guidance = analysis.limit_guidance
    base_capacity = min(
        guidance.tangible_net_worth_capacity,
        guidance.cash_flow_capacity,
        guidance.working_capital_capacity,
    )
    graded_capacity = base_capacity * RATING_LIMIT_MULTIPLIER[final_grade]
    enhancement_capacity = _enhancement_capacity(application, hard_blocked)
    haircut = CONCENTRATION_HAIRCUT[risk.concentration.severity]

    if recommendation is Recommendation.DECLINE:
        approved_limit = 0.0
        approved_terms = 0
    else:
        approved_limit = round_limit(
            min(
                application.requested_limit,
                (graded_capacity + enhancement_capacity) * (1 - haircut),
            )
        )
        approved_terms = min(
            application.requested_terms_days, RATING_MAX_TERMS_DAYS[final_grade]
        )

    conflicts.append(
        ConflictResolution(
            key="limit_reconciliation",
            rule="limit_reconciliation",
            financial_position=(
                f"The capacity test supported "
                f"{format_currency(guidance.indicative_limit, analysis.currency)} at standalone "
                f"grade {standalone}, with {guidance.binding_constraint} the binding constraint"
            ),
            risk_position=(
                f"Concentration at {risk.concentration.severity.value} severity implies a "
                f"{haircut:.0%} haircut, and the risk-adjusted grade of {final_grade} carries a "
                f"{RATING_LIMIT_MULTIPLIER[final_grade]:.2f} limit multiplier"
            ),
            resolution=(
                f"the approved limit is set at "
                f"{format_currency(approved_limit, application.currency)} on net "
                f"{approved_terms} day terms"
                + (
                    f", including {format_currency(enhancement_capacity, application.currency)} of "
                    "enhancement-backed capacity"
                    if enhancement_capacity > 0
                    else ""
                )
            ),
            prevailing_side="policy",
            evidence_ids=[
                "policy:limit_capacity",
                "policy:terms_cap",
                "signal:concentration",
                "app:requested_limit",
                "app:requested_terms",
            ],
        )
    )
    policy_notes.append(appetite_note)

    # -- concurrence ---------------------------------------------------------
    if not any(c.prevailing_side == "risk" for c in conflicts):
        conflicts.insert(
            0,
            ConflictResolution(
                key="concurrence_check",
                rule="concurrence_check",
                financial_position=(
                    f"Standalone grade {standalone} ({analysis.rating.band_label}) on a composite "
                    f"score of {analysis.rating.composite_score:.1f}"
                ),
                risk_position=(
                    f"Worst external severity {risk.overall_severity.value} across "
                    f"{len(risk.retrieved)} documents reviewed"
                ),
                resolution=(
                    "the two views agree in direction, so no override was required and the "
                    "financial rating carries through to the decision"
                ),
                prevailing_side="both",
                evidence_ids=["rating:standalone", "policy:country_tier"],
            ),
        )

    conflicts = _author_rationales(conflicts, context)

    decision = CreditDecision(
        applicant_id=application.applicant_id,
        recommendation=recommendation,
        standalone_grade=standalone,
        final_grade=final_grade,
        final_band_label=BAND_LABELS[final_grade],
        applied_notches=applied_notches,
        approved_limit=approved_limit,
        approved_terms_days=approved_terms,
        requested_limit=application.requested_limit,
        requested_terms_days=application.requested_terms_days,
        limit_haircut_percent=round(haircut * 100, 1),
        security_required=final_grade >= CONDITIONS_GRADE or hard_blocked,
        review_frequency_months=REVIEW_FREQUENCY_MONTHS[final_grade],
        conflicts=conflicts,
        mitigant_offsets=offsets,
        strengths=_strengths(analysis, risk),
        risk_factors=_risk_factors(analysis, risk, escalated),
        conditions=_conditions(
            application, analysis, risk, recommendation, final_grade, escalated
        ),
        policy_notes=policy_notes,
        evidence_ids=["rating:standalone", "policy:limit_capacity", "signal:concentration"],
    )

    _register_decision_evidence(decision, registry)

    return {
        "decision": decision,
        "evidence": registry.items(),
        "trace": [
            AgentMessage(
                agent=AGENT,
                action="reconcile",
                detail=(
                    f"Reconciled standalone grade {standalone} with {notches:.2f} risk notches and "
                    f"{mitigant_credit:.2f} notches of mitigant credit to reach final grade "
                    f"{final_grade} ({decision.final_band_label}). Recommendation: "
                    f"{recommendation.label} "
                    f"{format_currency(approved_limit, application.currency)} on net "
                    f"{approved_terms} day terms. Applied "
                    f"{len(conflicts)} resolution rule(s)."
                ),
                metrics={
                    "standalone_grade": standalone,
                    "risk_notches": notches,
                    "mitigant_credit": mitigant_credit,
                    "final_grade": final_grade,
                    "recommendation": recommendation.value,
                    "approved_limit": approved_limit,
                    "approved_terms_days": approved_terms,
                    "conflicts_resolved": [c.rule for c in conflicts],
                    "hard_blocked": hard_blocked,
                },
            )
        ],
    }


# --------------------------------------------------------------------------------------
# Rule helpers
# --------------------------------------------------------------------------------------


def _resolve_trend_conflict(
    application: CreditApplication, analysis: FinancialAnalysis, risk: RiskAssessment
) -> tuple[ConflictResolution | None, float, str]:
    """Resolve the spread's direction against the external evidence.

    Recency and specificity decide it: an obligor-specific document published
    after the last statement date supersedes a statement trend, while a signal
    that only describes the sector or the jurisdiction does not.
    """
    tracked = [
        t
        for t in analysis.trends
        if t.key in {"revenue", "ebitda_margin", "net_debt_to_ebitda", "free_cash_flow"}
    ]
    if not tracked:
        return None, 0.0, ""

    improving = [t for t in tracked if t.direction is TrendDirection.IMPROVING]
    deteriorating = [t for t in tracked if t.direction is TrendDirection.DETERIORATING]
    spread_improving = len(improving) > len(deteriorating)

    last_period_end = application.latest_statement.period_end
    specific = [
        f
        for f in risk.adverse_findings
        if f.severity.rank >= Severity.MODERATE.rank
        and f.category not in SYSTEMIC_CATEGORIES
    ]
    newer_than_statements = [f for f in specific if f.published_date > last_period_end]
    systemic = [
        f
        for f in risk.adverse_findings
        if f.severity.rank >= Severity.MODERATE.rank and f.category in SYSTEMIC_CATEGORIES
    ]

    if spread_improving and newer_than_statements:
        return (
            ConflictResolution(
                key="trend_vs_external_signal",
                rule="trend_vs_external_signal",
                financial_position=(
                    f"{len(improving)} of {len(tracked)} core metrics improved across "
                    f"{tracked[0].first_period}–{tracked[0].last_period}, so the spread reads as "
                    "a strengthening credit"
                ),
                risk_position=(
                    f"{len(newer_than_statements)} obligor-specific adverse finding(s) are dated "
                    f"after the {application.latest_statement.period_label} balance sheet date of "
                    f"{last_period_end}: "
                    + "; ".join(
                        f"{f.summary} ({f.published_date})" for f in newer_than_statements
                    )
                ),
                resolution=(
                    "the external evidence prevails on recency and specificity, and the proposed "
                    "notching stands in full despite the improving trend"
                ),
                prevailing_side="risk",
                evidence_ids=[
                    eid
                    for f in newer_than_statements
                    for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                ]
                + [f"trend:{t.key}" for t in improving],
            ),
            0.0,
            "Trend conflict resolved in favour of the external evidence on recency and specificity.",
        )

    if not spread_improving and not specific and systemic:
        return (
            ConflictResolution(
                key="trend_vs_external_signal",
                rule="trend_vs_external_signal",
                financial_position=(
                    f"{len(deteriorating)} of {len(tracked)} core metrics deteriorated across "
                    f"{tracked[0].first_period}–{tracked[0].last_period}"
                ),
                risk_position=(
                    f"the only material external signals are systemic — "
                    + "; ".join(f"{f.category.value}: {f.summary}" for f in systemic)
                    + " — with no obligor-specific adverse finding"
                ),
                resolution=(
                    f"the audited spread governs the rating and the systemic findings are "
                    f"downgraded to monitoring, releasing "
                    f"{SYSTEMIC_ONLY_RELIEF_NOTCHES:.2f} notches"
                ),
                prevailing_side="financial",
                evidence_ids=[
                    eid for f in systemic for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                ]
                + [f"trend:{t.key}" for t in deteriorating],
            ),
            -SYSTEMIC_ONLY_RELIEF_NOTCHES,
            "Systemic-only external signals downgraded to monitoring; the spread governs.",
        )

    return None, 0.0, ""


def _apply_mitigants(
    application: CreditApplication, risk: RiskAssessment, hard_blocked: bool
) -> tuple[list[MitigantOffset], float]:
    """Credit enhancements back against the findings they address."""
    if hard_blocked:
        return [], 0.0

    adverse = sorted(
        risk.adverse_findings, key=lambda f: f.severity.rank, reverse=True
    )
    offsets: list[MitigantOffset] = []

    for enhancement in application.credit_enhancements:
        credit = MITIGANT_NOTCH_CREDIT.get(enhancement.kind, 0.0)
        if credit <= 0:
            continue

        target, credit = _match_enhancement(enhancement, adverse, application, credit)
        if target is None or credit <= 0:
            continue

        offsets.append(
            MitigantOffset(
                enhancement_key=enhancement.key,
                description=enhancement.description,
                offsets_finding=target,
                notch_credit=round(credit, 2),
                evidence_ids=[f"app:enhancement:{enhancement.key}"],
            )
        )

    total = min(sum(o.notch_credit for o in offsets), MAX_MITIGANT_CREDIT)
    return offsets, round(total, 2)


def _match_enhancement(
    enhancement: CreditEnhancement,
    adverse: list[RiskFinding],
    application: CreditApplication,
    credit: float,
) -> tuple[str | None, float]:
    requested = max(application.requested_limit, 1.0)

    if enhancement.kind in CREDIT_SUBSTITUTES:
        if enhancement.kind == "credit_insurance":
            coverage = (enhancement.coverage_percent or 0.0) / 100.0
            if coverage <= 0:
                return None, 0.0
            return "aggregate obligor default risk", credit * coverage
        coverage_amount = enhancement.coverage_amount or 0.0
        ratio = coverage_amount / requested
        if ratio < MIN_GUARANTEE_COVERAGE_RATIO:
            return None, 0.0
        return "aggregate obligor default risk", credit * min(1.0, ratio)

    if enhancement.kind == "security_deposit":
        coverage_amount = enhancement.coverage_amount or 0.0
        if coverage_amount <= 0:
            return None, 0.0
        share = coverage_amount / requested
        return (
            "first-loss exposure on the approved limit",
            credit if share >= 0.10 else credit / 2,
        )

    if enhancement.kind == "payment_history":
        if (
            application.prior_relationship_months < MIN_HISTORY_MONTHS
            or application.prior_worst_days_beyond_terms is None
            or application.prior_worst_days_beyond_terms > MAX_HISTORY_DAYS_BEYOND_TERMS
        ):
            return None, 0.0
        match = _first_matching(enhancement, adverse)
        return (
            match or "willingness to pay on the existing trading relationship",
            credit,
        )

    if enhancement.kind == "personal_guarantee":
        # Unsupported by a verified statement of net worth, a personal guarantee
        # carries no measurable capacity, so it earns no notch credit.
        if enhancement.coverage_amount is None:
            return None, 0.0
        return "aggregate obligor default risk", credit

    match = _first_matching(enhancement, adverse)
    return (match, credit) if match else (None, 0.0)


def _first_matching(
    enhancement: CreditEnhancement, adverse: list[RiskFinding]
) -> str | None:
    for finding in adverse:
        if finding.category in enhancement.offsets_categories:
            return f"{finding.category.value}: {finding.summary}"
    return None


def _enhancement_capacity(application: CreditApplication, hard_blocked: bool) -> float:
    """Limit capacity contributed by enhancements, over and above the obligor's own.

    Credit insurance is excluded because it was already credited as notches, and
    counting it twice would inflate the limit.
    """
    if hard_blocked:
        return 0.0
    capacity = 0.0
    for enhancement in application.credit_enhancements:
        amount = enhancement.coverage_amount or 0.0
        if enhancement.kind == "letter_of_credit":
            capacity += min(amount, application.requested_limit)
        elif enhancement.kind == "parent_guarantee":
            # An unsecured parent guarantee is credited at half its face amount.
            capacity += 0.5 * min(amount, application.requested_limit)
        elif enhancement.kind == "security_deposit":
            capacity += amount
    return capacity


def _clamp_grade(grade: int, floor: int) -> int:
    return max(1, min(10, max(grade, floor)))


def _appetite(
    final_grade: int, hard_blocked: bool, risk: RiskAssessment
) -> tuple[Recommendation, str]:
    if hard_blocked:
        return (
            Recommendation.DECLINE,
            "Appetite: declined on the hard blocker rule.",
        )
    if final_grade >= DECLINE_GRADE:
        return (
            Recommendation.DECLINE,
            f"Appetite: grade {final_grade} is at or beyond the grade {DECLINE_GRADE} decline "
            "threshold for unsecured open account.",
        )
    if final_grade >= CONDITIONS_GRADE:
        return (
            Recommendation.APPROVE_WITH_CONDITIONS,
            f"Appetite: grade {final_grade} is inside appetite only on a conditioned, secured "
            "basis.",
        )
    material_adverse = [
        f for f in risk.adverse_findings if f.severity.rank >= Severity.MODERATE.rank
    ]
    if material_adverse:
        return (
            Recommendation.APPROVE_WITH_CONDITIONS,
            f"Appetite: grade {final_grade} is inside appetite, conditioned on the "
            f"{len(material_adverse)} material external finding(s).",
        )
    return (
        Recommendation.APPROVE,
        f"Appetite: grade {final_grade} is comfortably inside appetite with no material adverse "
        "external findings.",
    )


# --------------------------------------------------------------------------------------
# Decision points
# --------------------------------------------------------------------------------------


def _strengths(analysis: FinancialAnalysis, risk: RiskAssessment) -> list[DecisionPoint]:
    points: list[DecisionPoint] = [
        DecisionPoint(
            key=f"financial_{f.key}",
            text=f.statement,
            evidence_ids=[f"finding:financial:{f.key}", *f.evidence_ids],
        )
        for f in analysis.findings
        if f.direction is Direction.SUPPORTIVE
    ]
    points += [
        DecisionPoint(
            key=f"external_{f.key}",
            text=f.summary,
            evidence_ids=[f"finding:risk:{f.key}", *f.evidence_ids],
        )
        for f in risk.supportive_findings
    ]
    return points


def _risk_factors(
    analysis: FinancialAnalysis, risk: RiskAssessment, escalated: set[str]
) -> list[DecisionPoint]:
    points: list[DecisionPoint] = [
        DecisionPoint(
            key=f"financial_{f.key}",
            text=f.statement,
            evidence_ids=[f"finding:financial:{f.key}", *f.evidence_ids],
        )
        for f in analysis.findings
        if f.direction is Direction.ADVERSE and f.severity.rank >= Severity.MODERATE.rank
    ]
    for finding in risk.adverse_findings:
        if finding.severity.rank < Severity.MODERATE.rank:
            continue
        suffix = (
            " This matter was not declared on the application form."
            if finding.key in escalated
            else ""
        )
        points.append(
            DecisionPoint(
                key=f"external_{finding.key}",
                text=f"{finding.summary}{suffix}",
                evidence_ids=[f"finding:risk:{finding.key}", *finding.evidence_ids]
                + (["app:disclosures"] if finding.key in escalated else []),
            )
        )
    points.append(
        DecisionPoint(
            key="concentration",
            text=risk.concentration.statement,
            evidence_ids=["signal:concentration"],
        )
    )
    return points


def _conditions(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    recommendation: Recommendation,
    final_grade: int,
    escalated: set[str],
) -> list[DecisionPoint]:
    conditions: list[DecisionPoint] = []

    def add(key: str, text: str, evidence_ids: list[str]) -> None:
        conditions.append(DecisionPoint(key=key, text=text, evidence_ids=evidence_ids))

    if recommendation is Recommendation.DECLINE:
        add(
            "decline_reconsideration",
            "Reconsider only on a prepayment or fully secured basis, and only once the critical "
            "findings are resolved with documentary evidence.",
            ["rating:standalone", "signal:concentration"],
        )
        if risk.blockers:
            add(
                "decline_blocker_resolution",
                "Any future application must be supported by court or registry confirmation that "
                "the critical matters identified have been discharged.",
                [f"finding:risk:{key}" for key in risk.blockers],
            )
        return conditions

    by_kind = {e.kind: e for e in application.credit_enhancements}

    if "parent_guarantee" in by_kind:
        add(
            "guarantee_executed",
            "Obtain the executed parent guarantee, with a certified copy of the guarantor's "
            "latest audited accounts, before the first shipment on open account.",
            ["app:enhancement:" + by_kind["parent_guarantee"].key],
        )
    if "credit_insurance" in by_kind:
        add(
            "insurance_bound",
            "Confirm the trade credit insurance binder is in force for the approved limit and "
            "name the account on the policy schedule.",
            ["app:enhancement:" + by_kind["credit_insurance"].key],
        )
    if "security_deposit" in by_kind:
        add(
            "deposit_held",
            "Take and hold the offered cash security deposit before the limit is released.",
            ["app:enhancement:" + by_kind["security_deposit"].key],
        )

    if any(f.category is RiskCategory.COVENANT for f in risk.adverse_findings):
        add(
            "covenant_reporting",
            "Require a quarterly covenant compliance certificate from the applicant's lender, "
            "together with notice of any further waiver or amendment.",
            [
                f"finding:risk:{f.key}"
                for f in risk.adverse_findings
                if f.category is RiskCategory.COVENANT
            ],
        )
    if risk.concentration.severity.rank >= Severity.HIGH.rank:
        add(
            "concentration_notice",
            "Require immediate notice if the largest customer relationship is lost, reduced, or "
            "not renewed, and treat that as a limit review trigger.",
            ["signal:concentration"],
        )
    if any(f.key == "unaudited_statements" for f in analysis.findings):
        add(
            "audited_statements",
            "Require audited or independently reviewed statements for the next fiscal year before "
            "the limit is renewed.",
            ["finding:financial:unaudited_statements"],
        )
    if escalated:
        add(
            "disclosure_explanation",
            "Obtain a written explanation from the applicant for the matters found in external "
            "research that were not declared on the application form.",
            ["app:disclosures"],
        )
    if final_grade >= CONDITIONS_GRADE:
        add(
            "interim_reporting",
            "Require quarterly interim management accounts and a monthly aged trial balance while "
            "the account remains at this grade.",
            ["rating:standalone"],
        )

    add(
        "periodic_review",
        f"Review the limit every {REVIEW_FREQUENCY_MONTHS[final_grade]} months, and re-underwrite "
        "on any material adverse change.",
        ["rating:standalone"],
    )
    return conditions


def _author_rationales(
    conflicts: list[ConflictResolution], context: GraphContext
) -> list[ConflictResolution]:
    """Ask the model to write the rationale prose for each resolution.

    The rule, the positions, and the outcome are already settled in code; only
    the explanation is generated.
    """
    if not conflicts:
        return conflicts

    response = context.provider.generate(
        LLMRequest(
            task="conflict_rationale",
            system=SYSTEM,
            instructions=(
                "For each conflict in FACTS, write one or two sentences explaining why the stated "
                "resolution is the right one, referring to the rule that was applied. Do not "
                "change the resolution and do not introduce new facts."
            ),
            facts={
                "conflicts": [
                    {
                        "key": c.key,
                        "rule": c.rule,
                        "financial_position": c.financial_position,
                        "risk_position": c.risk_position,
                        "resolution": c.resolution,
                        "prevailing_side": c.prevailing_side,
                    }
                    for c in conflicts
                ]
            },
            output_schema=RATIONALE_SCHEMA,
            temperature=context.settings.llm_temperature,
        )
    )
    rationales = {
        str(r.get("key")): str(r.get("rationale", "")).strip()
        for r in response.output.get("rationales", [])
    }
    return [c.model_copy(update={"rationale": rationales.get(c.key, c.rationale)}) for c in conflicts]


def _register_decision_evidence(
    decision: CreditDecision, registry: EvidenceRegistry
) -> None:
    registry.register(
        "decision:recommendation",
        EvidenceKind.POLICY_RULE,
        "Reconciled credit decision",
        source="Supervisor reconciliation",
        display_value=(
            f"{decision.recommendation.label}: "
            f"{format_currency(decision.approved_limit)} on net "
            f"{decision.approved_terms_days} day terms at grade {decision.final_grade} "
            f"({decision.final_band_label})"
        ),
        detail="; ".join(decision.policy_notes),
        numeric_values=[
            decision.approved_limit,
            float(decision.approved_terms_days),
            float(decision.final_grade),
            float(decision.standalone_grade),
            decision.applied_notches,
            decision.limit_haircut_percent,
        ],
    )
    for conflict in decision.conflicts:
        registry.register(
            f"resolution:{conflict.key}",
            EvidenceKind.POLICY_RULE,
            f"Conflict resolution — {conflict.rule}",
            source="Supervisor reconciliation",
            display_value=conflict.resolution,
            detail=(
                f"Financial view: {conflict.financial_position}. Risk view: "
                f"{conflict.risk_position}. Prevailing side: {conflict.prevailing_side}. "
                f"{conflict.rationale}"
            ),
            numeric_values=[
                n.value
                for text in (
                    conflict.financial_position,
                    conflict.risk_position,
                    conflict.resolution,
                )
                for n in extract_numbers(text)
            ],
        )
    for offset in decision.mitigant_offsets:
        registry.register(
            f"mitigant:{offset.enhancement_key}",
            EvidenceKind.POLICY_RULE,
            f"Mitigant credit — {offset.enhancement_key}",
            source="Supervisor reconciliation",
            display_value=(
                f"{offset.notch_credit:.2f} notches credited against {offset.offsets_finding}"
            ),
            detail=offset.description,
            numeric_values=[offset.notch_credit],
        )
