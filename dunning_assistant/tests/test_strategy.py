from __future__ import annotations

import pytest

from dunning.agents.profiler import build_profile
from dunning.agents.sentiment_agent import assess_sentiment
from dunning.agents.strategy import decide_strategy
from dunning.domain import STAGE_ORDER


def decide(deps, account_id):
    snapshot = deps.repository.snapshot(account_id)
    profile = build_profile(snapshot, deps.llm)
    sentiment = assess_sentiment(snapshot, deps.sentiment, deps.llm)
    return decide_strategy(snapshot, profile, sentiment)


def test_reliable_payer_gets_a_soft_reminder(deps):
    decision = decide(deps, "ACC-1001")

    assert decision.stage == "courtesy_reminder"
    assert decision.tone == "warm"
    assert decision.channels == ["email"]
    assert not decision.legal_referral
    assert not decision.late_fee_warning
    assert not decision.human_approval_required
    assert len(decision.plan) >= 2


def test_deteriorating_account_gets_a_firm_multichannel_sequence(deps):
    decision = decide(deps, "ACC-2001")

    assert decision.stage == "escalation_notice"
    assert decision.tone == "firm"
    assert {"email", "phone", "sms"} <= set(decision.channels)
    assert decision.cite_contract_terms
    assert not decision.legal_referral
    assert decision.urgency == "high"  # avoidance tightens cadence instead of jumping a stage


def test_high_risk_account_gets_a_formal_pre_legal_sequence(deps):
    decision = decide(deps, "ACC-3001")

    assert decision.stage == "pre_legal_notice"
    assert decision.tone == "formal_strict"
    assert "certified_letter" in decision.channels
    assert decision.legal_referral
    assert decision.cite_contract_terms
    assert decision.human_approval_required
    assert decision.escalate_to_owner


def test_frustrated_relationship_adds_a_call_without_escalating_tone(deps):
    reliable = decide(deps, "ACC-1001")
    frustrated = decide(deps, "ACC-1003")  # same archetype, unhappy thread

    assert frustrated.stage == reliable.stage
    assert frustrated.tone == "warm"
    assert "phone" in frustrated.channels and "phone" not in reliable.channels


def test_frustration_softens_tone_on_a_high_risk_account(deps):
    silent = decide(deps, "ACC-3001")
    frustrated = decide(deps, "ACC-3002")

    assert frustrated.stage == silent.stage == "pre_legal_notice"
    assert frustrated.tone == "firm" and silent.tone == "formal_strict"
    assert frustrated.human_approval_required
    assert frustrated.legal_referral  # softer wording, same consequence


def test_pre_legal_stage_requires_deep_aging_or_repeated_broken_promises(deps):
    snapshot = deps.repository.snapshot("ACC-2003")
    profile = build_profile(snapshot, deps.llm)
    sentiment = assess_sentiment(snapshot, deps.sentiment, deps.llm)

    inflated = profile.model_copy(
        update={
            "archetype": "high_risk_delinquent",
            "risk_score": 90.0,
            "risk_band": "severe",
            "metrics": profile.metrics.model_copy(update={"exposure_vs_credit_limit": 1.4}),
        }
    )
    decision = decide_strategy(snapshot, inflated, sentiment)

    assert decision.stage != "pre_legal_notice"
    assert any("guardrail" in rule for rule in decision.policy_trace)


@pytest.mark.parametrize("account_id", ["ACC-1001", "ACC-2001", "ACC-3001"])
def test_plan_is_a_dated_sequence(deps, account_id):
    decision = decide(deps, account_id)
    offsets = [step.day_offset for step in decision.plan]

    assert offsets[0] == 0
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)
    assert [step.step_number for step in decision.plan] == list(range(1, len(decision.plan) + 1))
    assert all(step.send_on == decision.plan[0].send_on + __import__("datetime").timedelta(days=step.day_offset)
               for step in decision.plan)


def test_escalation_stage_orders_with_severity(deps):
    stages = [decide(deps, account).stage for account in ("ACC-1001", "ACC-2001", "ACC-3001")]
    assert [STAGE_ORDER.index(stage) for stage in stages] == sorted(STAGE_ORDER.index(s) for s in stages)
    assert len(set(stages)) == 3


def test_policy_trace_explains_every_decision(deps):
    decision = decide(deps, "ACC-3001")
    assert decision.policy_trace
    assert any("aging" in rule for rule in decision.policy_trace)
    assert any("governance" in rule for rule in decision.policy_trace)
    assert decision.rationale
