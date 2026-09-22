"""Memo narration.

The deterministic narrator is the default. It only restates the headline the
policy built from cited figures. A hosted model is used when
``CREDIT_SURVEILLANCE_LLM_PROVIDER=openai``. Either way, ``compose_memo``
attaches the figure block in code so the stored memo cites the engine's numbers.
"""

import json
import os
import urllib.request
from collections.abc import Mapping
from typing import Protocol

from credit_surveillance.errors import NarratorError
from credit_surveillance.models import NarrativeRequest

ACTION_LABEL = {
    "affirm": "AFFIRM the credit limit.",
    "reduce": "REDUCE the credit limit.",
    "conditions": "ADD CONDITIONS and hold the current limit.",
    "suspend": "SUSPEND the open account.",
    "increase": "INCREASE the credit limit.",
}

_OPENAI_SYSTEM = (
    "You write the narrative paragraph of a periodic credit review for an existing "
    "B2B account. Use only the headline and cited figures you are given. Do not "
    "calculate exposure, ratios, drift, or a new limit. Do not introduce any amount "
    "that is not already written as key=value in the cited figures. If you mention a "
    "figure, copy it exactly as key=value."
)


class Narrator(Protocol):
    def narrate(self, request: NarrativeRequest) -> str:
        """Return prose that narrates the request. Do not invent figures."""


class DeterministicNarrator:
    """Offline narrator. The same facts always produce the same paragraph."""

    def narrate(self, request: NarrativeRequest) -> str:
        return (
            f"{request.headline} "
            "This paragraph only narrates figures the surveillance engine already computed."
        )


class OpenAINarrator:
    """Hosted narrator. The request carries the computed citations and nothing else."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        transport=None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._transport = transport or urllib.request.urlopen

    def narrate(self, request: NarrativeRequest) -> str:
        user = {
            "account_name": request.account_name,
            "account_id": request.account_id,
            "as_of": request.as_of.isoformat(),
            "action": request.action,
            "rule_codes": list(request.rule_codes),
            "headline": request.headline,
            "cited_figures": request.cited_figures,
            "conditions": list(request.conditions),
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _OPENAI_SYSTEM},
                {"role": "user", "content": json.dumps(user)},
            ],
        }
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._transport(http_request, timeout=30) as response:
                body = json.loads(response.read().decode())
        except Exception as exc:
            raise NarratorError(f"OpenAI narration failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NarratorError("OpenAI response did not include a memo.") from exc
        if not isinstance(content, str) or not content.strip():
            raise NarratorError("OpenAI response did not include a memo.")
        return content.strip()


def build_narrator(environ: Mapping[str, str] | None = None) -> Narrator:
    """Return the offline narrator unless a hosted provider is configured."""
    env = os.environ if environ is None else environ
    provider = env.get("CREDIT_SURVEILLANCE_LLM_PROVIDER", "deterministic").strip().lower()
    if provider in {"", "deterministic", "offline"}:
        return DeterministicNarrator()
    if provider == "openai":
        api_key = env.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise NarratorError(
                "CREDIT_SURVEILLANCE_LLM_PROVIDER=openai requires OPENAI_API_KEY."
            )
        model = env.get("CREDIT_SURVEILLANCE_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        base_url = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        return OpenAINarrator(api_key=api_key, model=model, base_url=base_url)
    raise NarratorError(
        f"Unknown CREDIT_SURVEILLANCE_LLM_PROVIDER={provider!r}. "
        "Use deterministic or openai."
    )


def compose_memo(
    prose: str,
    request: NarrativeRequest,
    authority_reason: str,
) -> str:
    """Assemble the stored memo. The figure block is written by code."""
    lines = [
        f"PERIODIC CREDIT REVIEW — {request.account_name} ({request.account_id})",
        f"As of {request.as_of.isoformat()}.",
        f"Recommendation: {ACTION_LABEL[request.action]}",
        "Rules: " + ", ".join(request.rule_codes) + ".",
        "Watch signals: " + (", ".join(request.signals) if request.signals else "none") + ".",
        "Figures used:",
    ]
    lines.extend(f"{key}={value}" for key, value in request.cited_figures.items())
    if request.conditions:
        lines.append("Conditions:")
        lines.extend(request.conditions)
    lines.append(f"Authority: {authority_reason}")
    lines.append("Narrative:")
    lines.append(prose.strip())
    lines.append("The figures above were computed by the surveillance engine.")
    return "\n".join(lines)
