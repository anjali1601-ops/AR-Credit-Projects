"""Flight risk is a gate, not a vibe. The stable high performer is not flagged."""

from __future__ import annotations

from team_lead.agents.attrition import flight_risk_gates, is_flight_risk
from team_lead.llm import STAY_MARKER


def test_stable_high_performer_is_not_a_flight_risk(cases):
    maya = next(case for case in cases if case.teammate_id == "TL-MAYA")
    assert maya.performance.rating == "exceeds"
    assert maya.sentiment.label == "engaged"
    assert maya.metrics.sustained_overtime is False
    assert maya.attrition.flight_risk is False
    assert maya.attrition.stay_conversation is None
    assert maya.attrition.gates["adverse_sentiment"] is False
    assert "Flight risk is not raised." in maya.attrition.reasons


def test_only_the_burning_out_collector_is_flagged(cases):
    flagged = [case.teammate_id for case in cases if case.attrition.flight_risk]
    assert flagged == ["TL-ANDRE"]
    andre = next(case for case in cases if case.teammate_id == "TL-ANDRE")
    assert andre.performance.rating == "meets"
    assert andre.sentiment.label == "withdrawing"
    assert andre.tenure_months >= 12
    assert andre.metrics.sustained_overtime is True
    assert all(andre.attrition.gates.values())
    assert andre.attrition.stay_conversation is not None
    assert STAY_MARKER in andre.attrition.stay_conversation
    assert "$1,920,000" in andre.attrition.stay_conversation
    assert "withdrawing from the extra projects" in andre.attrition.stay_conversation


def test_new_hire_and_below_standard_are_not_stay_cases(cases):
    priya = next(case for case in cases if case.teammate_id == "TL-PRIYA")
    jordan = next(case for case in cases if case.teammate_id == "TL-JORDAN")
    assert priya.performance.rating == "meets"
    assert priya.attrition.flight_risk is False
    assert priya.attrition.stay_conversation is None
    assert priya.attrition.gates["tenured"] is False
    assert jordan.performance.rating == "below"
    assert jordan.attrition.flight_risk is False
    assert jordan.attrition.stay_conversation is None


def test_gates_require_every_signal():
    assert is_flight_risk(flight_risk_gates("engaged", "exceeds", 46, True)) is False
    assert is_flight_risk(flight_risk_gates("withdrawing", "meets", 38, True)) is True
    assert is_flight_risk(flight_risk_gates("strained", "meets", 3, True)) is False
    assert is_flight_risk(flight_risk_gates("strained", "below", 18, True)) is False
    assert is_flight_risk(flight_risk_gates("withdrawing", "exceeds", 40, False)) is False
