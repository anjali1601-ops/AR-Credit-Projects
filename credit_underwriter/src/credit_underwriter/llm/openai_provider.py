"""Real-model provider, enabled with ``CREDIT_UNDERWRITER_LLM_PROVIDER=openai``.

The prompt is assembled from the same :class:`LLMRequest` the offline provider
receives: a system message, task instructions, and a JSON block of grounded facts.
The model is told not to introduce figures that are absent from those facts, and
the memo critic independently verifies that constraint afterwards -- so a model
that ignores the instruction produces a failed completeness check and a revision
pass rather than a plausible-looking wrong number.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import LLMError, LLMRequest, LLMResponse

PROVIDER_NAME = "openai"
DEFAULT_MODEL = "gpt-4o-mini"

_GUARDRAIL = (
    "You are part of an audited credit underwriting pipeline. Use only the facts supplied in the "
    "FACTS block. Never introduce a number, date, company name, or document reference that does "
    "not appear in FACTS. Return a single JSON object and nothing else."
)


class OpenAIProvider:
    """Thin wrapper over the OpenAI chat completions API."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise LLMError(
                "the openai package is not installed; install with "
                "`uv sync --extra openai` or set CREDIT_UNDERWRITER_LLM_PROVIDER=offline"
            ) from exc

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError(
                "OPENAI_API_KEY is not set; unset CREDIT_UNDERWRITER_LLM_PROVIDER to run offline"
            )

        self.name = PROVIDER_NAME
        self.model = model or os.environ.get("CREDIT_UNDERWRITER_LLM_MODEL") or DEFAULT_MODEL
        self._client = OpenAI(api_key=key)

    def generate(self, request: LLMRequest) -> LLMResponse:
        messages = [
            {"role": "system", "content": f"{_GUARDRAIL}\n\n{request.system}"},
            {"role": "user", "content": self._user_content(request)},
        ]
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=request.temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # pragma: no cover - network path
            raise LLMError(f"{self.model} call failed for task {request.task!r}: {exc}") from exc

        raw = completion.choices[0].message.content or ""
        try:
            output: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"{self.model} returned non-JSON output for task {request.task!r}"
            ) from exc

        missing = [key for key in request.output_schema if key not in output]
        if missing:
            raise LLMError(
                f"{self.model} response for task {request.task!r} is missing keys: "
                f"{', '.join(missing)}"
            )

        return LLMResponse(
            task=request.task,
            provider=self.name,
            model=self.model,
            output=output,
            notes=[f"live call to {self.model}"],
        )

    @staticmethod
    def _user_content(request: LLMRequest) -> str:
        schema_lines = "\n".join(f"- {k}: {v}" for k, v in request.output_schema.items())
        return (
            f"TASK: {request.task}\n\n"
            f"INSTRUCTIONS:\n{request.instructions}\n\n"
            f"REQUIRED JSON KEYS:\n{schema_lines or '- (see instructions)'}\n\n"
            f"FACTS:\n{json.dumps(request.facts, indent=2, sort_keys=True, default=str)}"
        )
