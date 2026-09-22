"""Strategy Agent: combines the risk profile and relationship sentiment into an outreach plan.

This is a transparent policy engine rather than a template lookup: aging, risk
band, archetype, broken promises, exposure and relationship signals each move the
escalation stage, tone, channel mix and cadence independently, and every rule
that fired is recorded in `policy_trace`.
"""

from __future__ import annotations

from datetime import timedelta

from ..domain import (
    STAGE_ORDER,
    AccountSnapshot,
    PlannedStep,
    RiskProfile,
    SentimentAssessment,
    StrategyDecision,
)

TONE_LADDER = ["warm", "neutral_professional", "firm", "formal_strict"]
STAGE_BASE_TONE = {
    "courtesy_reminder": "warm",
    "firm_follow_up": "neutral_professional",
    "escalation_notice": "firm",
    "final_demand": "firm",
    "pre_legal_notice": "formal_strict",
}
URGENCY_BY_STAGE = {
    "courtesy_reminder": "low",
    "firm_follow_up": "medium",
    "escalation_notice": "medium",
    "final_demand": "high",
    "pre_legal_notice": "critical",
}
SPACING_BY_URGENCY = {"low": 7, "medium": 5, "high": 3, "critical": 2}

STAGE_PLAYBOOK: dict[str, list[tuple[str, str]]] = {
    "courtesy_reminder": [
        ("email", "Soft reminder with the invoice copy and a payment link"),
        ("email", "Friendly nudge to confirm which AP run will carry the payment"),
        ("phone", "Courtesy call to confirm the payment date and clear any blocker"),
    ],
    "firm_follow_up": [
        ("email", "Request a specific payment date for the full past-due balance"),
        ("phone", "Call to diagnose the real blocker and secure a dated commitment"),
        ("email", "Written confirmation of whatever was agreed, with terms restated"),
    ],
    "escalation_notice": [
        ("email", "Escalation notice citing contractual payment terms"),
        ("phone", "Call the billing contact and the AP manager to force a decision"),
        ("sms", "Short nudge pointing back to the escalation notice"),
        ("email", "Final written warning before formal demand"),
    ],
    "final_demand": [
        ("email", "Formal demand for payment in full with a hard response deadline"),
        ("phone", "Direct call to confirm receipt of the demand and record the response"),
        ("certified_letter", "Demand letter sent by certified mail to the registered address"),
        ("email", "Closing notice that the file is moving to pre-legal review"),
    ],
    "pre_legal_notice": [
        ("email", "Formal notice of default under the agreement"),
        ("phone", "Final call offering a signed payment plan as the alternative to referral"),
        ("certified_letter", "Notice of default by certified mail, copied to the account owner"),
        ("email", "Confirmation of referral to collections counsel if no response"),
    ],
}


def _shift(ladder: list[str], value: str, delta: int) -> str:
    index = max(0, min(len(ladder) - 1, ladder.index(value) + delta))
    return ladder[index]


def _base_stage_index(profile: RiskProfile) -> int:
    dpd = profile.metrics.oldest_days_past_due
    if dpd <= 20:
        return 0
    if dpd <= 40:
        return 0 if profile.risk_band == "low" else 1
    if dpd <= 70:
        return 2
    if dpd <= 100:
        return 3
    return 4


def decide_strategy(
    snapshot: AccountSnapshot,
    profile: RiskProfile,
    sentiment: SentimentAssessment,
) -> StrategyDecision:
    customer = snapshot.customer
    metrics = profile.metrics
    trace: list[str] = []

    # Stage is anchored on aging: behaviour can move it by one notch, never more.
    base_idx = _base_stage_index(profile)
    stage_idx = base_idx
    trace.append(f"aging: oldest invoice {metrics.oldest_days_past_due} dpd -> base stage {STAGE_ORDER[base_idx]}")

    if profile.archetype == "high_risk_delinquent" and profile.risk_band == "severe":
        stage_idx += 1
        trace.append("profiler: high-risk delinquent at severe risk -> escalate one stage")
    if metrics.promises_broken >= 2:
        stage_idx += 1
        trace.append(f"profiler: {metrics.promises_broken} broken payment promises -> escalate one stage")
    if metrics.exposure_vs_credit_limit >= 1.0:
        stage_idx += 1
        trace.append(f"profiler: exposure {metrics.exposure_vs_credit_limit:.0%} of credit limit -> escalate one stage")
    if profile.archetype == "reliable_but_late" and profile.risk_band == "low":
        stage_idx -= 1
        trace.append("profiler: reliable payer at low risk -> hold back one stage")
    if sentiment.relationship_label == "cooperative" and profile.recovery_outlook == "self_correcting":
        stage_idx -= 1
        trace.append("sentiment: cooperative and self-correcting -> hold back one stage")

    stage_idx = max(0, min(base_idx + 1, stage_idx))
    if stage_idx >= 4 and not (metrics.oldest_days_past_due >= 90 or metrics.promises_broken >= 3):
        stage_idx = 3
        trace.append("guardrail: pre-legal notice requires 90+ day aging or three broken promises -> capped")
    stage_idx = max(0, min(len(STAGE_ORDER) - 1, stage_idx))
    stage = STAGE_ORDER[stage_idx]

    tone = STAGE_BASE_TONE[stage]
    if profile.archetype == "reliable_but_late":
        tone = _shift(TONE_LADDER, tone, -1)
        trace.append("tone: reliable payer -> soften one notch")
    if sentiment.relationship_label == "frustrated":
        softened = _shift(TONE_LADDER, tone, -1)
        floor = "firm" if stage_idx >= 3 else "warm"
        tone = softened if TONE_LADDER.index(softened) >= TONE_LADDER.index(floor) else floor
        trace.append(f"sentiment: frustrated relationship -> soften tone toward repair (floor {floor})")
    if sentiment.relationship_label == "unresponsive" and stage_idx >= 2:
        tone = _shift(TONE_LADDER, tone, +1)
        trace.append("sentiment: unresponsive -> harden one notch")
    if profile.recovery_outlook == "at_risk_of_write_off" and TONE_LADDER.index(tone) < TONE_LADDER.index("firm"):
        tone = "firm"
        trace.append("profiler: write-off risk -> floor tone at firm")

    urgency = URGENCY_BY_STAGE[stage]
    if metrics.exposure_vs_credit_limit >= 1.0 and urgency in ("low", "medium"):
        urgency = "high"
        trace.append("urgency: exposure over credit limit -> raise urgency to high")
    if (
        sentiment.relationship_label in ("avoidant", "unresponsive")
        and metrics.unanswered_outbound_emails >= 3
        and urgency in ("low", "medium")
    ):
        urgency = "high"
        trace.append(
            f"sentiment: {sentiment.relationship_label} with {metrics.unanswered_outbound_emails} unanswered emails "
            "-> tighten cadence instead of jumping a stage"
        )

    channels: list[str] = ["email"]
    if sentiment.relationship_label == "frustrated":
        channels.append("phone")
        trace.append("channel: frustrated relationship -> add a call to repair before escalating further")
    elif metrics.promises_broken >= 1 or stage_idx >= 1 or metrics.past_due_balance >= 25_000:
        channels.append("phone")
        trace.append("channel: broken promises / material balance -> add a call")
    if metrics.unanswered_outbound_emails >= 3 and stage_idx >= 2:
        channels.append("sms")
        trace.append("channel: email channel is not landing -> add SMS")
    if stage_idx >= 3:
        channels.append("certified_letter")
        trace.append("channel: final demand or later -> add certified letter")

    legal_referral = stage_idx >= 4 or (stage_idx >= 3 and metrics.promises_broken >= 3)
    cash_stress = any("cash_stress" in m.cues for m in sentiment.per_message[-3:])
    offer_plan = (
        cash_stress
        or (profile.archetype != "reliable_but_late" and metrics.past_due_balance >= 20_000 and stage_idx >= 2)
        or metrics.partial_payment_ratio > 0.05
    )
    human_approval_required = bool(
        legal_referral
        or (customer.annual_contract_value >= 200_000 and stage_idx >= 3)
        or (sentiment.relationship_label == "frustrated" and stage_idx >= 2)
    )
    if offer_plan:
        trace.append("offer: structured payment plan is on the table")
    if legal_referral:
        trace.append("escalation: legal referral is the stated consequence in this sequence")
    if human_approval_required:
        trace.append("governance: sequence requires human approval before send")

    decision = StrategyDecision(
        account_id=customer.account_id,
        stage=stage,
        tone=tone,
        channels=list(dict.fromkeys(channels)),
        urgency=urgency,
        offer_payment_plan=bool(offer_plan),
        cite_contract_terms=stage_idx >= 2,
        late_fee_warning=stage_idx >= 2 or metrics.oldest_days_past_due >= 45,
        service_hold_warning=stage_idx >= 3 or metrics.exposure_vs_credit_limit >= 0.9,
        legal_referral=bool(legal_referral),
        human_approval_required=human_approval_required,
        escalate_to_owner=bool(profile.risk_band == "severe" or (customer.annual_contract_value >= 200_000 and stage_idx >= 2)),
        policy_trace=trace,
    )

    decision.plan = build_plan(decision, snapshot, profile)
    decision.rationale = _rationale(decision, profile, sentiment)
    return decision


def build_plan(
    decision: StrategyDecision,
    snapshot: AccountSnapshot,
    profile: RiskProfile,
) -> list[PlannedStep]:
    spacing = SPACING_BY_URGENCY[decision.urgency]
    steps: list[PlannedStep] = []
    offset = 0
    stage_idx = STAGE_ORDER.index(decision.stage)

    for channel, intent in STAGE_PLAYBOOK[decision.stage]:
        if channel not in decision.channels:
            continue
        if steps:
            offset += max(1, round(spacing * (0.6 if channel == "phone" else 1.0)))
        steps.append(
            PlannedStep(
                step_number=len(steps) + 1,
                day_offset=offset,
                send_on=snapshot.as_of + timedelta(days=offset),
                channel=channel,
                intent=intent,
                tone=decision.tone,
            )
        )

    if len(steps) < 2:  # always leave a follow-up on the calendar
        offset += spacing
        steps.append(
            PlannedStep(
                step_number=len(steps) + 1,
                day_offset=offset,
                send_on=snapshot.as_of + timedelta(days=offset),
                channel="email",
                intent="Follow-up if no response to the first message",
                tone=decision.tone,
            )
        )

    if stage_idx >= 2 and len(steps) > 1:
        # The closing message in an escalated sequence hardens one notch.
        hardened = _shift(TONE_LADDER, decision.tone, +1)
        steps[-1] = steps[-1].model_copy(update={"tone": hardened})
    return steps


def _rationale(decision: StrategyDecision, profile: RiskProfile, sentiment: SentimentAssessment) -> str:
    return (
        f"{profile.archetype.replace('_', ' ')} at risk {profile.risk_score:.0f}/100 ({profile.risk_band}) with a "
        f"{sentiment.relationship_label} relationship (health {sentiment.relationship_health:.0f}/100) -> "
        f"{decision.stage.replace('_', ' ')} in a {decision.tone.replace('_', ' ')} tone across "
        f"{', '.join(decision.channels)} over {len(decision.plan)} steps"
        + (" ; payment plan offered" if decision.offer_payment_plan else "")
        + (" ; human approval required" if decision.human_approval_required else "")
        + "."
    )
