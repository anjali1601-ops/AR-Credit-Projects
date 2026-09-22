"""Explanation provider.

The matcher has already chosen the application. This module only writes
the note a clerk reads. The default provider fills a template offline.
Set CASH_APP_LLM_PROVIDER=openai to have a hosted model write that note
from the same facts. It is not given a way to change amounts or invoices.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from cash_application.money import format_money


@dataclass(frozen=True)
class ExplainFacts:
    channel: str
    external_ref: str
    payer_name: str | None
    payer_account: str | None
    payment_reference: str | None
    amount: str
    kind: str
    confidence: str
    recommended_action: str
    queue: str
    applied_amount: str
    unapplied_cash: str
    short_fall: str
    deduction_note: str | None
    invoices_cited: bool
    lines: tuple[dict[str, str], ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "external_ref": self.external_ref,
            "payer_name": self.payer_name,
            "payer_account": self.payer_account,
            "payment_reference": self.payment_reference,
            "amount": self.amount,
            "kind": self.kind,
            "confidence": self.confidence,
            "recommended_action": self.recommended_action,
            "queue": self.queue,
            "applied_amount": self.applied_amount,
            "unapplied_cash": self.unapplied_cash,
            "short_fall": self.short_fall,
            "deduction_note": self.deduction_note,
            "invoices_cited": self.invoices_cited,
            "lines": [dict(line) for line in self.lines],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class Explanation:
    text: str
    provider: str
    model: str


class OfflineExplainer:
    """Deterministic clerk note. No network, no API key, same text every run."""

    provider = "offline"
    model = "deterministic-template-v1"

    def explain(self, facts: ExplainFacts) -> Explanation:
        return Explanation(text=render_offline(facts), provider=self.provider, model=self.model)


class OpenAIExplainer:
    """Hosted wording for the same facts. Raises if the key or package is missing."""

    provider = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def explain(self, facts: ExplainFacts) -> Explanation:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise RuntimeError("CASH_APP_LLM_PROVIDER=openai requires OPENAI_API_KEY")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the cash application extra 'openai' to use the hosted explainer") from exc
        client = OpenAI()
        response = client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write a short note for an accounts-receivable clerk about a "
                        "cash application that has already been decided. Use only the figures "
                        "in the JSON. Do not invent invoices, do not change amounts, and do "
                        "not change the recommended action. Say that nothing posts until the "
                        "clerk confirms. Four to six sentences."
                    ),
                },
                {"role": "user", "content": json.dumps(facts.as_dict())},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("OpenAI explainer returned an empty note")
        return Explanation(text=text, provider=self.provider, model=self.model)


def get_explainer() -> OfflineExplainer | OpenAIExplainer:
    provider = os.environ.get("CASH_APP_LLM_PROVIDER", "offline").strip().lower()
    if provider in {"", "offline", "deterministic", "mock"}:
        return OfflineExplainer()
    if provider == "openai":
        return OpenAIExplainer()
    raise ValueError(f"unknown CASH_APP_LLM_PROVIDER={provider!r}")


def render_offline(facts: ExplainFacts) -> str:
    payer = facts.payer_name or "An unidentified payer"
    account = f" ({facts.payer_account})" if facts.payer_account else ""
    channel = {"lockbox": "lockbox check", "edi_820": "EDI 820", "email": "email"}.get(facts.channel, facts.channel)
    reference = f" Reference {facts.payment_reference}." if facts.payment_reference else ""
    percent = _percent(facts.confidence)
    lines = _line_sentence(facts)
    action = _action_sentence(facts)
    deduction = ""
    if facts.kind == "short_pay" and facts.deduction_note:
        deduction = f" Deduction note: {facts.deduction_note}."
    held = " The receipt will not post until a clerk confirms."
    if facts.queue == "exception" and facts.kind == "full" and not facts.invoices_cited:
        held = " Held for a clerk because the remittance did not cite the invoice number."
    return (
        f"{payer}{account} sent {format_money(facts.amount)} by {channel}.{reference} "
        f"{lines}{deduction} {action} Confidence {percent}.{held}"
    )


def _percent(confidence: str) -> str:
    points = int(round(float(confidence) * 100))
    return f"{points}%"


def _line_sentence(facts: ExplainFacts) -> str:
    if not facts.lines:
        return "No open invoice matched the payer, the amount, and the references on the remittance."
    parts = []
    for line in facts.lines:
        parts.append(
            f"{line['invoice_number']} ({line['customer_name']}, open {format_money(line['open_amount'])}, "
            f"apply {format_money(line['apply_amount'])})"
        )
    joined = "; ".join(parts)
    if facts.kind == "full" and facts.invoices_cited:
        return f"The remittance cites {joined}."
    if facts.kind == "full":
        return f"No invoice number was cited. The open item that ties is {joined}."
    if facts.kind == "short_pay":
        return f"The remittance points at {joined}."
    if facts.kind == "overpay":
        return f"The remittance cites {joined}."
    return f"Matched to {joined}."


def _action_sentence(facts: ExplainFacts) -> str:
    if facts.kind == "unapplied" or not facts.lines:
        return f"Proposed application: leave {format_money(facts.amount)} unapplied."
    if facts.kind == "full" and len(facts.lines) > 1:
        return (
            f"Proposed application: split {format_money(facts.applied_amount)} "
            "across those invoices in full."
        )
    if facts.kind == "full":
        return f"Proposed application: apply {format_money(facts.applied_amount)} in full."
    if facts.kind == "short_pay":
        return (
            f"Proposed application: apply {format_money(facts.applied_amount)} and leave "
            f"{format_money(facts.short_fall)} open on the invoice."
        )
    if facts.kind == "overpay":
        return (
            f"Proposed application: apply {format_money(facts.applied_amount)} and leave "
            f"{format_money(facts.unapplied_cash)} unapplied."
        )
    return f"Proposed application: {facts.recommended_action}."
