"""LLM provider interface with a deterministic offline writer as the default.

`MockLLM` is not a single hardcoded template: it composes copy from the
structured facts and the strategy decision handed to it by the communications
agent (tone, escalation stage, channel, intent, policy flags, QA feedback), so
different strategies produce materially different messages. Swap in a hosted
model with `DUNNING_LLM_PROVIDER=openai`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings, get_settings

VENDOR_NAME = "Meridian Systems"


@dataclass
class LLMRequest:
    task: str
    prompt: str
    system: str = "You are an accounts-receivable collections specialist."
    variables: dict[str, Any] = field(default_factory=dict)
    temperature: float = 0.2
    max_tokens: int = 800


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


def _pick(options: list[str], key: str, rotate: int = 0) -> str:
    """Stable choice so a given account always reads the same; `rotate` keeps
    consecutive steps in one sequence from repeating each other verbatim."""
    digest = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return options[(digest + rotate) % len(options)]


GREETINGS = {
    "warm": ["Hi {first},", "Hi {first}, hope you're well."],
    "neutral_professional": ["Hi {first},", "Hello {first},"],
    "firm": ["{first},", "Hello {first},"],
    "formal_strict": ["Dear {contact_name},", "Dear {contact_name},"],
}

OPENERS = {
    "warm": [
        "You've been a steady partner for {tenure_months} months, and your payments normally land on a predictable cycle, so this one looks like a simple slip rather than a problem.",
        "Thanks for the consistent partnership over the last {tenure_months} months - your team almost always settles within a couple of weeks of terms, so I assume this is just the AP calendar.",
    ],
    "neutral_professional": [
        "I'm following up on the outstanding balance on account {account_id}. Our records show the payment pattern has slipped compared with your usual cycle.",
        "I wanted to flag the current past-due position on account {account_id} before it ages further.",
    ],
    "firm": [
        "Despite {outbound_attempts} attempts to reach you over the past {contact_window} days, the balance below remains unpaid and no payment date has been confirmed.",
        "We have not had a substantive response to our recent messages, and the balance below continues to age.",
    ],
    "formal_strict": [
        "This is a formal notice regarding the past-due balance on account {account_id}, issued under {clause} of the agreement between {customer_name} and {vendor}.",
        "This letter serves as formal notice that the balance set out below is overdue under {clause} and must be settled without further delay.",
    ],
}

ASKS = {
    "warm": [
        "Could you confirm which AP run {oldest_invoice} will be included in?",
        "Could you let me know the date this is scheduled to go out so I can close it on my side?",
    ],
    "neutral_professional": [
        "Please confirm a specific payment date for the full {past_due_balance} by {response_by}.",
        "Please reply by {response_by} with the date the outstanding {past_due_balance} will be remitted.",
    ],
    "firm": [
        "We require a written payment commitment for {past_due_balance} by {response_by}.",
        "Please provide a firm remittance date for {past_due_balance} in writing by {response_by}, or let us know today if there is a dispute we are unaware of.",
    ],
    "formal_strict": [
        "Payment in full of {past_due_balance} is required by {response_by}.",
        "Remittance of {past_due_balance} in full is required no later than {response_by}.",
    ],
}

CLOSINGS = {
    "warm": ["Thanks so much,", "Appreciate your help,"],
    "neutral_professional": ["Best regards,", "Kind regards,"],
    "firm": ["Regards,", "Regards,"],
    "formal_strict": ["Sincerely,", "Sincerely,"],
}

SUBJECTS = {
    "courtesy_reminder": ["Quick reminder: {oldest_invoice} ({past_due_balance})", "{oldest_invoice} - friendly payment reminder"],
    "firm_follow_up": ["Follow-up required: {past_due_balance} past due", "Payment date needed for {past_due_balance}"],
    "escalation_notice": ["Escalation notice: {past_due_balance} past due on {account_id}", "Account {account_id} escalated - {past_due_balance} outstanding"],
    "final_demand": ["FINAL DEMAND: {past_due_balance} due on account {account_id}", "Final demand for payment - account {account_id}"],
    "pre_legal_notice": ["FORMAL NOTICE OF DEFAULT - account {account_id}", "Pre-legal notice: account {account_id} ({past_due_balance})"],
}

RELATIONSHIP_LINES = {
    "cooperative": "I appreciate how responsive you've been on previous invoices.",
    "neutral": "Let me know if anything on our side is holding this up.",
    "frustrated": "I know recent exchanges have been frustrating, and I'd like to resolve the open items with you directly rather than through more reminders.",
    "avoidant": "I haven't been able to reach you on my last few notes, so I want to make sure this is reaching the right person.",
    "unresponsive": "We have not had a reply to our recent correspondence, so this notice is also being sent to the billing contact on file.",
}


def _invoice_block(variables: dict[str, Any]) -> str:
    lines = []
    for inv in variables.get("invoices", []):
        lines.append(
            f"  - {inv['invoice_id']}  {inv['amount']}  due {inv['due_date']}  ({inv['days_past_due']} days past due)"
            + ("  [disputed]" if inv.get("disputed") else "")
        )
    return "\n".join(lines) if lines else "  - (no open invoices on file)"


def _policy_lines(variables: dict[str, Any], tone: str) -> list[str]:
    flags = variables.get("flags", {})
    lines: list[str] = []
    if flags.get("offer_payment_plan"):
        lines.append(
            "If cash flow timing is the blocker, we can structure this into "
            f"{variables.get('plan_installments', 3)} instalments - reply 'plan' and I'll send terms for signature."
        )
    if flags.get("late_fee_warning"):
        lines.append(
            f"Please note that under {variables.get('clause')} a late fee of {variables.get('late_fee_pct')}% per month "
            "applies to balances outstanding beyond terms."
        )
    if flags.get("service_hold_warning"):
        lines.append(
            "To avoid an interruption, note that new orders and service provisioning will be placed on hold while the account remains past due."
        )
    if flags.get("cite_contract_terms"):
        lines.append(
            f"For reference, payment terms of net {variables.get('payment_terms_days')} days are contractually agreed "
            f"under {variables.get('clause')}."
        )
    if flags.get("legal_referral"):
        lines.append(
            "If payment or a signed payment plan is not received by the date above, the account will be referred to our "
            f"collections counsel for recovery under {variables.get('clause')}, including accrued interest and recoverable costs."
        )
    if variables.get("disputed_invoices"):
        lines.append(
            "Invoice(s) "
            + ", ".join(variables["disputed_invoices"])
            + " are flagged as disputed. Undisputed amounts remain due while the dispute is reviewed."
        )
    return lines


class MockLLM:
    """Deterministic, key-free writer used for local demos, tests and evals."""

    name = "mock"
    model = "deterministic-writer-v1"

    def complete(self, request: LLMRequest) -> LLMResponse:
        handler = {
            "risk_narrative": self._risk_narrative,
            "sentiment_summary": self._sentiment_summary,
            "email_draft": self._email,
            "sms_draft": self._sms,
            "call_script": self._call_script,
            "letter_draft": self._letter,
        }.get(request.task, self._fallback)
        return LLMResponse(text=handler(request.variables).strip(), provider=self.name, model=self.model)

    # -- narratives -------------------------------------------------------
    def _risk_narrative(self, v: dict[str, Any]) -> str:
        return (
            f"{v['customer_name']} has {v['invoices_paid']} settled invoices on record with an average of "
            f"{v['avg_days_late']:.0f} days late ({v['predictability']} payer) and {v['open_invoices']} invoices open "
            f"for {v['past_due_balance']}, the oldest {v['oldest_days_past_due']} days past due. "
            f"Lateness is {v['trend_word']} and {v['promises_broken']} of {v['promises_made']} payment promises were broken. "
            f"Risk scores {v['risk_score']:.0f}/100 ({v['risk_band']}); outlook is {v['recovery_outlook'].replace('_', ' ')}."
        )

    def _sentiment_summary(self, v: dict[str, Any]) -> str:
        return (
            f"Across {v['message_count']} client messages the relationship reads as {v['relationship_label']} "
            f"(health {v['relationship_health']:.0f}/100, engagement {v['engagement_trend']}). "
            f"Last inbound reply was {v['days_since_last_inbound']} days ago with {v['unanswered']} unanswered outreach attempts since."
        )

    # -- channel drafts ---------------------------------------------------
    def _email(self, v: dict[str, Any]) -> str:
        tone = v["tone"]
        step = int(v.get("step_number", 1))
        key = f"{v['account_id']}::{tone}::{v['stage']}"
        subject = _pick(SUBJECTS[v["stage"]], key).format(**v)
        if step > 1:
            subject = f"RE: {subject}"
        greeting = _pick(GREETINGS[tone], key, step).format(**v)
        opener = _pick(OPENERS[tone], key, step).format(**v)
        ask = _pick(ASKS[tone], key, step).format(**v)
        closing = _pick(CLOSINGS[tone], key, step)

        parts = [f"Subject: {subject}", "", greeting, ""]
        if step > 1 and v.get("previous_send_on"):
            parts.append(
                f"Following up on my message of {v['previous_send_on']}, which I haven't yet had a reply to. "
                f"Purpose of this note: {v['intent'][0].lower() + v['intent'][1:]}."
            )
        parts.append(opener)
        relationship_line = RELATIONSHIP_LINES.get(v.get("relationship_label", "neutral"))
        if relationship_line and tone != "formal_strict" and step == 1:
            parts.append(relationship_line)
        parts += ["", f"Open items as of {v['as_of']} (total {v['past_due_balance']}):", _invoice_block(v), "", ask]
        parts += _policy_lines(v, tone)
        if v.get("revision_notes"):
            parts.append(
                f"Reference: {v['oldest_invoice']} / total {v['past_due_balance']} / response required by {v['response_by']}."
            )
        parts += [
            "",
            f"You can pay by ACH or card here: {v['payment_link']}",
            "",
            closing,
            f"{v['rep_name']}",
            f"Accounts Receivable, {VENDOR_NAME}",
            f"{v['rep_email']}",
        ]
        return "\n".join(parts)

    def _sms(self, v: dict[str, Any]) -> str:
        if v["tone"] in ("firm", "formal_strict"):
            return (
                f"{VENDOR_NAME} AR: account {v['account_id']} is {v['past_due_balance']} past due "
                f"({v['oldest_days_past_due']} days on {v['oldest_invoice']}). Please call {v['rep_name']} today "
                f"or pay at {v['payment_link']}. Reply STOP to opt out."
            )
        return (
            f"Hi {v['first']}, {v['rep_name']} from {VENDOR_NAME} AR. Just a reminder that {v['oldest_invoice']} "
            f"({v['past_due_balance']}) is past due. Pay or schedule here: {v['payment_link']}. Reply STOP to opt out."
        )

    def _call_script(self, v: dict[str, Any]) -> str:
        objective = v.get("intent_line") or "Secure a specific payment date."
        escalation = (
            "If they refuse or deflect: state that the account is scheduled for referral under "
            f"{v.get('clause')} and that today's call is the last step before that."
            if v["flags"].get("legal_referral")
            else "If they deflect: ask what specifically has to happen internally, and who owns that step."
        )
        plan_line = (
            f"Offer: up to {v.get('plan_installments', 3)} instalments, first payment within 7 days, signed plan required."
            if v["flags"].get("offer_payment_plan")
            else "Do not offer new terms on this call; the ask is payment in full."
        )
        return "\n".join(
            [
                f"CALL SCRIPT - {v['customer_name']} ({v['account_id']}) - {v['contact_name']} {v['contact_phone']}",
                f"Tone: {v['tone'].replace('_', ' ')} | Stage: {v['stage'].replace('_', ' ')} | Call on {v['send_on']}",
                "",
                f"1. Open: \"Hi {v['first']}, it's {v['rep_name']} from {VENDOR_NAME} accounts receivable. Do you have two minutes?\"",
                f"2. Position: {v['past_due_balance']} is past due across {len(v.get('invoices', []))} invoice(s); "
                f"the oldest, {v['oldest_invoice']}, is {v['oldest_days_past_due']} days past due.",
                f"3. Objective: {objective}",
                f"4. Listen for: {v.get('listen_for', 'the actual blocker - approval, cash timing, or an unraised dispute')}.",
                f"5. {plan_line}",
                f"6. {escalation}",
                f"7. Close: restate the agreed amount and date, and confirm you will send written confirmation to {v['contact_email']} the same day.",
            ]
        )

    def _letter(self, v: dict[str, Any]) -> str:
        subject_line, _, rest = self._email(v).partition("\n")
        subject = subject_line.split(":", 1)[1].strip()
        return "\n".join(
            [
                f"Subject: {subject} (certified mail and email)",
                "",
                f"[SEND BY CERTIFIED MAIL, RETURN RECEIPT REQUESTED - dispatch {v['send_on']}]",
                f"{v['customer_name']}",
                f"Attn: {v['contact_name']}",
                rest.rstrip(),
            ]
        )

    def _fallback(self, v: dict[str, Any]) -> str:
        return f"[{self.model}] no handler for task; variables: {sorted(v)}"


class OpenAIChatLLM:
    """Hosted provider, enabled with DUNNING_LLM_PROVIDER=openai and OPENAI_API_KEY."""

    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("openai package not installed; `pip install '.[openai]'`") from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        self._client = OpenAI()
        self.model = model

    def complete(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - needs network
        completion = self._client.chat.completions.create(
            model=self.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        )
        return LLMResponse(text=completion.choices[0].message.content or "", provider=self.name, model=self.model)


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    choice = settings.llm_provider.lower()
    if choice in ("mock", "offline", "deterministic"):
        return MockLLM()
    if choice == "openai":
        return OpenAIChatLLM(settings.llm_model)
    raise ValueError(f"Unknown LLM provider {settings.llm_provider!r} (expected 'mock' or 'openai')")
