"""Compliance reviewer: the QA gate that can send drafts back to the communications agent."""

from __future__ import annotations

from ..domain import CommunicationSequence, ReviewResult, StrategyDecision

LEGAL_PHRASES = ("collections counsel", "notice of default", "formal notice of default", "recoverable costs")
HARSH_IN_WARM_TONE = ("demand", "default", "legal", "counsel", "immediately")
TEXT_CHANNELS = ("email", "certified_letter")


def review_sequence(sequence: CommunicationSequence, decision: StrategyDecision) -> ReviewResult:
    issues: list[str] = []
    checks = 0

    checks += 1
    if len(sequence.steps) < 2:
        issues.append("sequence must contain at least two steps")

    checks += 1
    offsets = [step.day_offset for step in sequence.steps]
    if offsets != sorted(offsets) or len(set(offsets)) != len(offsets):
        issues.append("step timings must be strictly increasing")
    elif offsets and offsets[0] != 0:
        issues.append("first step must be scheduled immediately (day 0)")

    body_blob = "\n".join(step.body.lower() for step in sequence.steps)

    for step in sequence.steps:
        label = f"step {step.step_number} ({step.channel})"
        checks += 4
        if "{" in step.body or "}" in step.body:
            issues.append(f"{label}: unresolved template placeholder")
        if step.channel in TEXT_CHANNELS and not step.subject:
            issues.append(f"{label}: missing subject line")
        if step.channel == "sms":
            if "stop" not in step.body.lower():
                issues.append(f"{label}: SMS must carry an opt-out instruction")
        elif len(step.body) < 150:
            issues.append(f"{label}: draft is too short to be usable")
        if step.tone == "warm" and any(word in step.body.lower() for word in HARSH_IN_WARM_TONE):
            issues.append(f"{label}: escalation language is not allowed in a warm-tone message")

    first_text_step = next((s for s in sequence.steps if s.channel in TEXT_CHANNELS), None)
    if first_text_step:
        checks += 1
        if "$" not in first_text_step.body:
            issues.append("opening message must state the outstanding amount")

    checks += 1
    if any(phrase in body_blob for phrase in LEGAL_PHRASES) and not decision.legal_referral:
        issues.append("legal referral language used without a legal_referral decision")

    checks += 1
    if decision.legal_referral and not any(phrase in body_blob for phrase in LEGAL_PHRASES):
        issues.append("legal referral was approved but no message states the consequence")

    checks += 1
    if decision.cite_contract_terms and decision.stage != "courtesy_reminder" and "§" not in "\n".join(
        step.body for step in sequence.steps
    ):
        issues.append("contract terms were required but no clause is cited")

    checks += 1
    if decision.offer_payment_plan and "instalment" not in body_blob and "plan" not in body_blob:
        issues.append("payment plan was approved but never offered in the sequence")

    return ReviewResult(passed=not issues, issues=issues, checks_run=checks)
