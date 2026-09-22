"""The LLM boundary.

Agents never call a vendor SDK directly. They build an :class:`LLMRequest` that
carries a task name, instructions, and a bundle of *grounded facts*, and they get
back structured JSON. Two consequences fall out of that shape:

* the offline provider can answer every task deterministically, so the whole
  system runs with no API key and no network, and
* a real provider only ever sees facts the engine already computed, which keeps
  the numbers in the memo attributable.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    system: str
    instructions: str
    facts: dict[str, Any] = Field(default_factory=dict)
    #: Human-readable description of the expected JSON shape. Sent to real
    #: providers; used by the offline provider only for error messages.
    output_schema: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.0


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    provider: str
    model: str
    output: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


class LLMError(RuntimeError):
    """Raised when a provider cannot answer a request."""


class UnsupportedTaskError(LLMError):
    def __init__(self, task: str, provider: str) -> None:
        super().__init__(f"provider {provider!r} has no handler for task {task!r}")
        self.task = task


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal contract every provider satisfies."""

    name: str
    model: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...
