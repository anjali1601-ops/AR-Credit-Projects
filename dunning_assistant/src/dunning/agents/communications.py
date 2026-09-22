"""Communications Agent: turns the strategy decision into channel-ready drafts."""

from __future__ import annotations

from datetime import timedelta

from ..domain import (
    AccountSnapshot,
    CommunicationSequence,
    DraftedStep,
    PlannedStep,
    RiskProfile,
    SentimentAssessment,
    StrategyDecision,
)
from ..providers.llm import VENDOR_NAME, LLMProvider, LLMRequest

TASK_BY_CHANNEL = {
    "email": "email_draft",
    "sms": "sms_draft",
    "phone": "call_script",
    "certified_letter": "letter_draft",
}

RESPONSE_WINDOW = {"low": 7, "medium": 5, "high": 3, "critical": 2}

LISTEN_FOR = {
    "cooperative": "which AP run the payment lands in, and whether anything is missing from our invoice",
    "neutral": "the approval step that is actually blocking payment, and who owns it",
    "frustrated": "the specific service or billing grievance behind the hold, and what would resolve it",
    "avoidant": "whether the contact still owns AP, and whether cash timing is the real issue",
    "unresponsive": "whether the business is still operating normally and who now controls payments",
}


def _fmt_money(value: float) -> str:
    return f"${value:,.0f}"


def _base_variables(
    snapshot: AccountSnapshot,
    profile: RiskProfile,
    sentiment: SentimentAssessment,
    decision: StrategyDecision,
) -> dict:
    customer = snapshot.customer
    open_invoices = snapshot.open_invoices
    open_invoices = open_invoices.assign(
        outstanding=open_invoices["amount"] - open_invoices["amount_paid"],
        days_past_due=open_invoices["due_date"].map(lambda d: (snapshot.as_of - d).days),
    ).sort_values("days_past_due", ascending=False)

    invoice_rows = [
        {
            "invoice_id": row["invoice_id"],
            "amount": _fmt_money(row["outstanding"]),
            "due_date": row["due_date"].isoformat(),
            "days_past_due": int(row["days_past_due"]),
            "disputed": bool(row["disputed"]),
        }
        for _, row in open_invoices.iterrows()
    ]
    outbound = snapshot.emails[snapshot.emails["direction"] == "outbound"]
    contact_window = (
        (outbound["sent_at"].max() - outbound["sent_at"].min()).days if len(outbound) > 1 else 0
    )

    return {
        "account_id": customer.account_id,
        "customer_name": customer.name,
        "vendor": VENDOR_NAME,
        "contact_name": customer.contact_name,
        "first": customer.contact_name.split()[0],
        "contact_email": customer.contact_email,
        "contact_phone": customer.contact_phone,
        "rep_name": customer.ar_owner,
        "rep_email": f"{customer.ar_owner.split()[0].lower()}.ar@meridiansystems.example",
        "clause": customer.contract_clause,
        "payment_terms_days": customer.payment_terms_days,
        "late_fee_pct": f"{customer.late_fee_pct:g}",
        "tenure_months": profile.metrics.tenure_months,
        "as_of": snapshot.as_of.isoformat(),
        "invoices": invoice_rows,
        "disputed_invoices": [row["invoice_id"] for row in invoice_rows if row["disputed"]],
        "oldest_invoice": invoice_rows[0]["invoice_id"] if invoice_rows else "n/a",
        "oldest_days_past_due": profile.metrics.oldest_days_past_due,
        "past_due_balance": _fmt_money(profile.metrics.past_due_balance or profile.metrics.open_balance),
        "outbound_attempts": max(1, int(len(outbound))),
        "contact_window": contact_window,
        "payment_link": f"https://pay.meridiansystems.example/{customer.account_id.lower()}",
        "plan_installments": 4 if profile.metrics.past_due_balance >= 50_000 else 3,
        "relationship_label": sentiment.relationship_label,
        "listen_for": LISTEN_FOR.get(sentiment.relationship_label, LISTEN_FOR["neutral"]),
        "stage": decision.stage,
        "flags": {
            "offer_payment_plan": decision.offer_payment_plan,
            "cite_contract_terms": decision.cite_contract_terms,
            "late_fee_warning": decision.late_fee_warning,
            "service_hold_warning": decision.service_hold_warning,
            "legal_referral": decision.legal_referral,
        },
    }


def _prompt_for(step: PlannedStep, variables: dict, feedback: list[str]) -> str:
    lines = [
        f"Write the {step.channel.replace('_', ' ')} for step {step.step_number} of a collections sequence.",
        f"Account {variables['account_id']} ({variables['customer_name']}), contact {variables['contact_name']}.",
        f"Escalation stage: {step.tone} tone, {variables['stage'].replace('_', ' ')}.",
        f"Objective: {step.intent}.",
        f"Past due {variables['past_due_balance']} across {len(variables['invoices'])} invoice(s); "
        f"oldest {variables['oldest_invoice']} at {variables['oldest_days_past_due']} days past due.",
        f"Relationship reads as {variables['relationship_label']}.",
        "Active policies: "
        + (", ".join(name for name, on in variables["flags"].items() if on) or "none")
        + ".",
        f"The customer must respond by {variables['response_by']}.",
        "Return the email with a 'Subject:' first line." if step.channel in ("email", "certified_letter") else "",
    ]
    if feedback:
        lines.append("Revise to fix these compliance issues: " + "; ".join(feedback))
    return "\n".join(line for line in lines if line)


def draft_sequence(
    snapshot: AccountSnapshot,
    profile: RiskProfile,
    sentiment: SentimentAssessment,
    decision: StrategyDecision,
    llm: LLMProvider,
    feedback: list[str] | None = None,
) -> CommunicationSequence:
    feedback = feedback or []
    base = _base_variables(snapshot, profile, sentiment, decision)
    window = RESPONSE_WINDOW[decision.urgency]
    steps: list[DraftedStep] = []

    previous_send_on: str | None = None
    for planned in decision.plan:
        variables = {
            **base,
            "step_number": planned.step_number,
            "previous_send_on": previous_send_on,
            "tone": planned.tone,
            "intent": planned.intent,
            "intent_line": planned.intent if planned.channel == "phone" else "",
            "send_on": planned.send_on.isoformat(),
            "response_by": (planned.send_on + timedelta(days=window)).isoformat(),
            "revision_notes": feedback,
        }
        response = llm.complete(
            LLMRequest(
                task=TASK_BY_CHANNEL[planned.channel],
                system=(
                    "You are a senior accounts-receivable collections specialist. Write clear, compliant, "
                    "factually grounded outreach. Never invent amounts, dates or legal threats."
                ),
                prompt=_prompt_for(planned, variables, feedback),
                variables=variables,
            )
        )
        subject, body = _split_subject(response.text)
        steps.append(
            DraftedStep(
                step_number=planned.step_number,
                day_offset=planned.day_offset,
                send_on=planned.send_on,
                channel=planned.channel,
                intent=planned.intent,
                tone=planned.tone,
                subject=subject,
                body=body,
                talking_points=_talking_points(planned.channel, body, variables),
            )
        )
        previous_send_on = planned.send_on.isoformat()

    return CommunicationSequence(
        account_id=snapshot.customer.account_id,
        stage=decision.stage,
        tone=decision.tone,
        steps=steps,
        generated_by=f"{getattr(llm, 'name', 'unknown')}:{getattr(llm, 'model', '')}",
    )


def _split_subject(text: str) -> tuple[str | None, str]:
    first, _, rest = text.partition("\n")
    if first.lower().startswith("subject:"):
        return first.split(":", 1)[1].strip(), rest.strip()
    return None, text.strip()


def _talking_points(channel: str, body: str, variables: dict) -> list[str]:
    if channel == "phone":
        return [line.strip() for line in body.splitlines() if line.strip()[:2].rstrip(".").isdigit()]
    points = [
        f"Past due {variables['past_due_balance']} across {len(variables['invoices'])} invoice(s)",
        f"Response required by {variables['response_by']}",
    ]
    points += [f"Policy: {name.replace('_', ' ')}" for name, on in variables["flags"].items() if on]
    return points
