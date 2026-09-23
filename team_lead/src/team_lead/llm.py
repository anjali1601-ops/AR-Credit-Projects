"""Narrative provider. Offline and deterministic unless TEAM_LEAD_LLM_PROVIDER=openai.

Ratings, relationship labels, and flight-risk flags are decided before this is called.
The model only writes prose from figures and quotes it is given.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import llm_provider_name

SYSTEM = (
    "You draft manager-facing documents for an accounts-receivable and credit team lead. "
    "Use only the figures and quotes in the request. Every evaluative sentence must cite a "
    "number you were given. Do not invent emails, chats, or private messages. Do not change "
    "the rating or the flight-risk flag. The stay conversation is for the manager, not a "
    "message to the employee."
)

STAY_MARKER = "This stay conversation is for the manager only."


@dataclass
class LLMRequest:
    task: str
    prompt: str
    facts: dict[str, Any] = field(default_factory=dict)
    system: str = SYSTEM


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> str: ...


class OfflineLLM:
    """Composes the draft from the structured facts. Same inputs, same sentences."""

    name = "offline"

    def complete(self, request: LLMRequest) -> str:
        facts = request.facts
        if request.task == "review_opening":
            return (
                f"Draft review for {facts['name']}, {facts['role']}, {facts['period']}. "
                f"The system rating is {facts['rating']}. "
                f"Cash applied was {facts['cash']}, promises kept were {facts['promises']} "
                f"({facts['promise_pct']}), dispute cycle time averaged {facts['cycle']}, "
                f"quality averaged {facts['quality']}, and workload was {facts['cases']} cases."
            )
        if request.task == "coaching":
            return _coaching(facts)
        if request.task == "stay_conversation":
            return _stay(facts)
        raise ValueError(f"Unknown narrative task: {request.task}")


def _coaching(facts: dict[str, Any]) -> str:
    kind = facts["kind"]
    name = facts["name"]
    if kind == "load":
        return (
            f"Coaching for {name} is about the book, not a performance plan. "
            f"They closed {facts['cases']} cases against a {facts['expectation']}-case expectation "
            f"and averaged {facts['ot_avg']} overtime hours a week across {facts['ot_weeks']} weeks "
            f"at or above 10 hours. Quality held at {facts['quality']}. "
            f"Agree a smaller book before the next 1:1. Do not add a stretch goal this month."
        )
    if kind == "stretch":
        return (
            f"Coaching for {name} is stretch, not correction. "
            f"Cash applied was {facts['cash']} and quality was {facts['quality']}. "
            f"Ask them to spend thirty minutes a week teaching one ramp teammate the deduction-code "
            f"checks they already use. No corrective plan."
        )
    if kind == "fair":
        return (
            f"Coaching for {name} should stay specific and fair. "
            f"Promises kept were {facts['promises']} ({facts['promise_pct']}), and dispute cycle "
            f"time averaged {facts['cycle']}. The book was {facts['cases']} cases against "
            f"{facts['expectation']}, so do not frame this as a capacity failure. "
            f"For the next four weeks: log every promise the day it is made, and touch any dispute "
            f"that reaches 7 days with no customer contact. Review those two numbers in the weekly 1:1."
        )
    if kind == "ramp":
        return (
            f"Coaching for {name} is ramp support. Cash applied was {facts['cash']} against the "
            f"ramp bar of {facts['cash_target']}. Quality is {facts['quality']}. "
            f"Tenure is {facts['tenure_months']} months and overtime averaged {facts['ot_avg']} hours "
            f"a week. Keep the 1:1 on deduction codes. A meets rating this early is not a finished "
            f"collector, and it is not a flight-risk case."
        )
    return (
        f"Coaching for {name} holds the meets result. Promises kept were {facts['promises']}. "
        f"Pick the weakest meets measure and set one number to move next quarter."
    )


def _stay(facts: dict[str, Any]) -> str:
    return (
        f"Stay conversation for you to have with {facts['name']}. "
        f"Do not send this note to them or to HR.\n\n"
        f"Open with the work you want to keep: they applied {facts['cash']} this quarter, "
        f"kept {facts['promises']} promises ({facts['promise_pct']}), and quality averaged "
        f"{facts['quality']}. Say plainly that this is not a performance conversation.\n\n"
        f"Name the load: {facts['ot_weeks']} weeks at or above 10 overtime hours, averaging "
        f"{facts['ot_avg']} hours, on {facts['cases']} cases closed against a "
        f"{facts['expectation']}-case expectation.\n\n"
        f"Use their own words from the 1:1, and do not add any: \"{facts['quote']}\"\n\n"
        f"Offer two concrete changes: move 40 cases off the book this month, and no weekend "
        f"coverage for six weeks. Ask what would make the next two quarters sustainable, then stop talking.\n\n"
        f"{STAY_MARKER}"
    )


class OpenAILLM:
    """Hosted prose. Decisions stay in the agents; this only returns text."""

    name = "openai"

    def __init__(self) -> None:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "TEAM_LEAD_LLM_PROVIDER=openai requires OPENAI_API_KEY. "
                "Unset TEAM_LEAD_LLM_PROVIDER to use the offline writer."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError("Install the openai extra: pip install -e '.[openai]'") from exc
        self._client = OpenAI(api_key=key)
        self._model = os.getenv("TEAM_LEAD_LLM_MODEL", "gpt-4o-mini")

    def complete(self, request: LLMRequest) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()


def get_llm() -> LLMProvider:
    name = llm_provider_name()
    if name in ("offline", "deterministic", "local"):
        return OfflineLLM()
    if name in ("openai", "hosted"):
        return OpenAILLM()
    raise RuntimeError(f"Unknown TEAM_LEAD_LLM_PROVIDER={name}")


def ensure_contains(text: str, required: list[str]) -> str:
    missing = [item for item in required if item and item not in text]
    if not missing:
        return text
    return text.rstrip() + "\n\n" + "\n".join(missing)
