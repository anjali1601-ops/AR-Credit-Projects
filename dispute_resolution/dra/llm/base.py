"""Provider-agnostic LLM interface.

Every agent talks to this interface and nothing else. The default provider is
``MockLLM``: fully deterministic, offline, no API key. Swapping in OpenAI or
Anthropic is a one-line environment change and does not touch agent code.

Each call carries a ``LLMTask`` plus a structured ``context`` payload. Hosted
providers use the rendered ``messages``; the offline provider uses ``context``
to produce the same JSON contract. That shared contract is what keeps the two
interchangeable.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage


class LLMTask(str, Enum):
    EXTRACT_DISPUTE = "extract_dispute"
    GENERATE_SQL = "generate_sql"
    DRAFT_REBUTTAL = "draft_rebuttal"
    DRAFT_CREDIT_MEMO = "draft_credit_memo"
    DRAFT_SUPERVISOR_EMAIL = "draft_supervisor_email"
    DRAFT_INFO_REQUEST = "draft_info_request"


@dataclass
class LLMRequest:
    task: LLMTask
    messages: list[BaseMessage]
    context: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 1200

    @property
    def system_text(self) -> str:
        return "\n\n".join(
            str(m.content) for m in self.messages if isinstance(m, SystemMessage)
        )

    @property
    def user_text(self) -> str:
        return "\n\n".join(
            str(m.content) for m in self.messages if isinstance(m, HumanMessage)
        )


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: float = 0.0

    def json(self) -> dict[str, Any]:
        return parse_json_object(self.text)


class LLMProvider(ABC):
    """Minimal surface every provider implements."""

    name: str = "base"
    model: str = "unknown"

    @abstractmethod
    def _generate(self, request: LLMRequest) -> str: ...

    def generate(self, request: LLMRequest) -> LLMResult:
        started = time.perf_counter()
        text = self._generate(request)
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_json_object(text: str) -> dict[str, Any]:
    """Tolerantly pull a JSON object out of a model response."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object found in model response: {text[:200]!r}")
    return json.loads(cleaned[start : end + 1])
