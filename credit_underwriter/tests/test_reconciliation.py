"""Reconciliation.

The point of the reconciler is that it *resolves* disagreement rather than
concatenating the two specialists' views. These tests drive synthetic conflicts
through it and assert which side prevailed and why, including the cases where
the risk searcher overrides a healthy spread and where structural mitigants pull
a weak grade back up.
"""

from __future__ import annotations

import pytest

from credit_underwriter.agents import reconciler
from credit_underwriter.agents.reconciler import (
    DECLINE_GRADE,
    MAX_MITIGANT_GRADE_UPLIFT,
    _clamp_grade,
)
from credit_underwriter.evidence import EvidenceRegistry
from credit_underwriter.models import (
    Direction,
    Recommendation,
    RiskCategory,
    RiskFinding,
    Severity,
)


def reconcile(state_parts: dict, context) -> dict:
    return reconciler.reconcile(state_parts, context)


def build_state(run, **overrides) -> dict:
    """A reconciler input state assembled from a completed run."""
    state = {
        "run_id": run.run_id,
        "application": run.application,
        "financial_analysis": run.financial_analysis,
        "risk_assessment": run.risk_assessment,
        "evidence": list(run.evidence),
        "trace": [],
    }
    state.update(overrides)
    return state


def finding(
    key: str,
    category: RiskCategory,
    severity: Severity,
    *,
    summary: str = "A synthetic adverse finding for testing.",
    published_date: str = "2026-08-01",
) -> RiskFinding:
    return RiskFinding(
        key=key,
        category=category,
        severity=severity,
        direction=Direction.ADVERSE,
        summary=summary,
        rationale="Injected by the test suite.",
        published_date=published_date,
        months_old=1.7,
        raw_severity=severity,
        evidence_ids=["signal:concentration"],
    )


# --------------------------------------------------------------------------------------
# The seeded outcomes
# --------------------------------------------------------------------------------------


def test_the_three_applicants_reach_three_different_recommendations(runs):
    recommendations = {
        applicant_id: record.decision.recommendation for applicant_id, record in runs.items()
    }
    assert recommendations["atlas-precision-works"] is Recommendation.APPROVE
    assert recommendations["northwind-logistics"] is Recommendation.APPROVE_WITH_CONDITIONS
    assert recommendations["veritas-metal-trading"] is Recommendation.DECLINE
    assert len(set(recommendations.values())) == 3


def test_approved_limits_and_terms_separate_the_three_applicants(runs):
    atlas = runs["atlas-precision-works"].decision
    northwind = runs["northwind-logistics"].decision
    veritas = runs["veritas-metal-trading"].decision

    assert atlas.approved_limit > northwind.approved_limit > veritas.approved_limit
    assert atlas.approved_terms_days > northwind.approved_terms_days
    assert veritas.approved_limit == 0.0
    assert veritas.approved_terms_days == 0


def test_no_decision_ever_exceeds_what_was_requested(runs):
    for applicant_id, record in runs.items():
        decision = record.decision
        assert decision.approved_limit <= decision.requested_limit, applicant_id
        assert decision.approved_terms_days <= decision.requested_terms_days, applicant_id


def test_every_decision_records_the_rules_it_applied(runs):
    for applicant_id, record in runs.items():
        decision = record.decision
        assert decision.conflicts, f"{applicant_id} recorded no resolution rules"
        for conflict in decision.conflicts:
            assert conflict.financial_position.strip()
            assert conflict.risk_position.strip()
            assert conflict.resolution.strip()
            assert conflict.prevailing_side in ("financial", "risk", "policy", "both")
            assert conflict.evidence_ids, f"{conflict.key} resolved without evidence"


def test_reconciliation_is_not_a_concatenation_of_the_two_inputs(runs):
    """The decision must differ from both specialists where they disagree."""
    record = runs["northwind-logistics"]
    decision = record.decision

    assert decision.standalone_grade == record.financial_analysis.rating.grade
    assert record.risk_assessment.proposed_notches > 0
    naive_risk_grade = decision.standalone_grade + record.risk_assessment.proposed_notches
    assert decision.final_grade != decision.standalone_grade
    assert decision.final_grade != pytest.approx(naive_risk_grade)


# --------------------------------------------------------------------------------------
# Hard blockers
# --------------------------------------------------------------------------------------


def test_a_hard_blocker_overrides_a_healthy_spread(runs, context):
    """The clean applicant must be declined if the searcher finds an insolvency."""
    record = runs["atlas-precision-works"]
    assert record.decision.recommendation is Recommendation.APPROVE

    blocked = record.risk_assessment.model_copy(
        update={
            "findings": [
                *record.risk_assessment.findings,
                finding(
                    "injected_insolvency",
                    RiskCategory.INSOLVENCY,
                    Severity.CRITICAL,
                    summary="A winding-up petition has been filed against the applicant.",
                ),
            ],
            "blockers": ["injected_insolvency"],
            "overall_severity": Severity.CRITICAL,
        }
    )
    result = reconcile(build_state(record, risk_assessment=blocked), context)
    decision = result["decision"]
    assert decision.recommendation is Recommendation.DECLINE
    assert decision.approved_limit == 0.0
    assert decision.approved_terms_days == 0
    assert decision.final_grade >= DECLINE_GRADE
    assert decision.final_grade > decision.standalone_grade
    override = next(c for c in decision.conflicts if c.rule == "hard_blocker_override")
    assert override.prevailing_side == "risk"


def test_a_hard_blocker_cannot_be_mitigated_away(runs, context):
    """Structural support must not rescue an obligor in insolvency proceedings."""
    record = runs["northwind-logistics"]
    assert record.decision.mitigant_offsets, "this test needs an applicant with enhancements"

    blocked = record.risk_assessment.model_copy(
        update={
            "findings": [
                *record.risk_assessment.findings,
                finding(
                    "injected_insolvency",
                    RiskCategory.INSOLVENCY,
                    Severity.CRITICAL,
                ),
            ],
            "blockers": ["injected_insolvency"],
            "overall_severity": Severity.CRITICAL,
        }
    )
    decision = reconcile(build_state(record, risk_assessment=blocked), context)["decision"]

    assert decision.recommendation is Recommendation.DECLINE
    assert decision.mitigant_offsets == []
    assert decision.approved_limit == 0.0


def test_veritas_is_declined_on_a_blocker_not_merely_on_its_grade(runs):
    record = runs["veritas-metal-trading"]
    assert record.risk_assessment.blockers
    rules = {c.rule for c in record.decision.conflicts}
    assert "hard_blocker_override" in rules


# --------------------------------------------------------------------------------------
# Disclosure integrity
# --------------------------------------------------------------------------------------


def test_an_undeclared_finding_escalates_and_the_risk_side_prevails(runs, context):
    """Silence on the form against an external finding is a governance problem."""
    record = runs["atlas-precision-works"]
    assert record.application.disclosures.material_litigation is False

    undeclared = record.risk_assessment.model_copy(
        update={
            "findings": [
                *record.risk_assessment.findings,
                finding(
                    "injected_litigation",
                    RiskCategory.LITIGATION,
                    Severity.HIGH,
                    summary="A $4m breach of contract claim has been filed against the applicant.",
                ),
            ],
            "overall_severity": Severity.HIGH,
        }
    )
    decision = reconcile(build_state(record, risk_assessment=undeclared), context)["decision"]

    integrity = [c for c in decision.conflicts if c.rule == "disclosure_integrity"]
    assert integrity, "an undeclared external finding must trigger the integrity rule"
    assert any(c.prevailing_side == "risk" for c in integrity)
    assert decision.final_grade > record.decision.final_grade


def test_a_declared_finding_is_not_treated_as_a_disclosure_failure(runs):
    """Northwind declared its litigation and covenant miss, so integrity holds."""
    record = runs["northwind-logistics"]
    assert record.application.disclosures.material_litigation is True

    integrity = [c for c in record.decision.conflicts if c.rule == "disclosure_integrity"]
    assert all(c.prevailing_side != "risk" for c in integrity)


def test_veritas_disclosure_failures_are_recorded_individually(runs):
    """Two undeclared findings must produce two records, not one merged note."""
    record = runs["veritas-metal-trading"]
    integrity = [c for c in record.decision.conflicts if c.rule == "disclosure_integrity"]
    assert len(integrity) >= 2
    assert all(c.prevailing_side == "risk" for c in integrity)
    assert len({c.key for c in integrity}) == len(integrity)


# --------------------------------------------------------------------------------------
# Trend against external signal
# --------------------------------------------------------------------------------------


def test_a_recent_specific_finding_prevails_over_a_stale_improving_trend(runs, context):
    """The searcher wins when its evidence is newer than the last fiscal year."""
    record = runs["atlas-precision-works"]
    escalating = record.risk_assessment.model_copy(
        update={
            "findings": [
                *record.risk_assessment.findings,
                finding(
                    "injected_recent_default",
                    RiskCategory.PAYMENT_DEFAULT,
                    Severity.HIGH,
                    summary="A major supplier has placed the applicant on cash-in-advance terms.",
                ),
            ],
            "overall_severity": Severity.HIGH,
            "proposed_notches": max(1.0, record.risk_assessment.proposed_notches),
        }
    )
    decision = reconcile(build_state(record, risk_assessment=escalating), context)["decision"]

    resolved = next(c for c in decision.conflicts if c.rule == "trend_vs_external_signal")
    assert resolved.prevailing_side == "risk"
    assert "recency" in resolved.resolution or "recency" in resolved.rationale
    assert decision.final_grade > record.decision.final_grade
    assert decision.recommendation is not Recommendation.APPROVE


def test_a_systemic_signal_alone_does_not_override_an_improving_spread(runs, context):
    """A sector or country report is not obligor-specific evidence."""
    record = runs["atlas-precision-works"]
    systemic_only = record.risk_assessment.model_copy(
        update={
            "findings": [
                *record.risk_assessment.findings,
                finding(
                    "injected_sector_stress",
                    RiskCategory.INDUSTRY,
                    Severity.HIGH,
                    summary="Sector order books are contracting across the peer group.",
                ),
            ],
            "overall_severity": Severity.HIGH,
        }
    )
    decision = reconcile(build_state(record, risk_assessment=systemic_only), context)["decision"]
    trend_rules = [c for c in decision.conflicts if c.rule == "trend_vs_external_signal"]
    assert all(c.prevailing_side != "risk" for c in trend_rules)


def test_agreement_between_the_specialists_is_recorded_as_concurrence(runs):
    """When both sides point the same way, no override should be claimed."""
    record = runs["atlas-precision-works"]
    concurrence = [c for c in record.decision.conflicts if c.rule == "concurrence_check"]
    assert concurrence
    assert concurrence[0].prevailing_side == "both"


# --------------------------------------------------------------------------------------
# Mitigants
# --------------------------------------------------------------------------------------


def test_mitigants_pull_the_marginal_applicant_back_above_the_decline_line(runs):
    record = runs["northwind-logistics"]
    decision = record.decision

    assert decision.mitigant_offsets, "Northwind's enhancements must be credited"
    assert decision.final_grade < decision.standalone_grade + record.risk_assessment.proposed_notches
    for offset in decision.mitigant_offsets:
        assert offset.notch_credit > 0
        assert offset.offsets_finding, f"{offset.enhancement_key} offsets nothing in particular"
        assert offset.evidence_ids


def test_mitigant_uplift_is_capped(runs):
    """No stack of enhancements may lift the grade without limit."""
    record = runs["northwind-logistics"]
    decision = record.decision
    assert decision.final_grade >= decision.standalone_grade - MAX_MITIGANT_GRADE_UPLIFT


def test_an_applicant_with_no_usable_enhancements_gets_no_mitigant_credit(runs):
    """Atlas's only enhancement is collateral, which policy scores at zero notches."""
    record = runs["atlas-precision-works"]
    assert record.decision.mitigant_offsets == []


def test_clamp_keeps_the_grade_on_the_scale():
    assert _clamp_grade(0) == 1
    assert _clamp_grade(15) == 10
    assert _clamp_grade(5) == 5
    assert _clamp_grade(3, floor=5) == 5


# --------------------------------------------------------------------------------------
# Limits, terms, and conditions
# --------------------------------------------------------------------------------------


def test_a_concentration_haircut_reduces_the_limit(runs):
    """The concentrated applicant must not get its full capacity."""
    northwind = runs["northwind-logistics"].decision
    atlas = runs["atlas-precision-works"].decision
    assert northwind.limit_haircut_percent > 0
    assert atlas.limit_haircut_percent == 0


def test_a_weaker_grade_gets_shorter_terms(runs):
    atlas = runs["atlas-precision-works"].decision
    northwind = runs["northwind-logistics"].decision
    assert atlas.final_grade < northwind.final_grade
    assert atlas.approved_terms_days > northwind.approved_terms_days


def test_a_weaker_grade_gets_more_frequent_review(runs):
    atlas = runs["atlas-precision-works"].decision
    northwind = runs["northwind-logistics"].decision
    assert atlas.review_frequency_months > northwind.review_frequency_months


def test_conditions_are_attached_where_the_grade_requires_them(runs):
    atlas = runs["atlas-precision-works"].decision
    northwind = runs["northwind-logistics"].decision

    assert len(northwind.conditions) > len(atlas.conditions)
    assert northwind.security_required is True
    assert atlas.security_required is False
    for condition in northwind.conditions:
        assert condition.text.strip()
        assert condition.evidence_ids, f"condition {condition.key} cites nothing"


def test_declining_carries_no_limit_and_no_terms(runs):
    decision = runs["veritas-metal-trading"].decision
    assert decision.approved_limit == 0.0
    assert decision.approved_terms_days == 0
    assert decision.security_required is True


def test_strengths_and_risk_factors_are_cited(runs):
    for applicant_id, record in runs.items():
        for point in (*record.decision.strengths, *record.decision.risk_factors):
            assert point.text.strip(), applicant_id
            assert point.evidence_ids, f"{applicant_id} point {point.key} cites nothing"


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_reconciliation_is_deterministic(runs, context):
    record = runs["northwind-logistics"]
    first = reconcile(build_state(record), context)["decision"]
    second = reconcile(build_state(record), context)["decision"]
    assert first.model_dump() == second.model_dump()


def test_reconciliation_registers_its_own_evidence(runs, context):
    record = runs["northwind-logistics"]
    result = reconcile(build_state(record), context)
    registry = EvidenceRegistry(result["evidence"])

    assert registry.get("decision:recommendation") is not None
    for conflict in result["decision"].conflicts:
        assert registry.get(f"resolution:{conflict.key}") is not None, conflict.key


def test_reconciliation_reports_what_it_did_on_the_trace(runs, context):
    record = runs["northwind-logistics"]
    result = reconcile(build_state(record), context)
    message = result["trace"][0]
    assert message.agent == "supervisor"
    assert message.action == "reconcile"
    assert message.metrics["final_grade"] == result["decision"].final_grade
    assert message.metrics["conflicts_resolved"]
