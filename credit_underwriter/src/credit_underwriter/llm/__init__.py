"""LLM provider boundary and its implementations."""

from __future__ import annotations

from ..config import Settings
from .base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    UnsupportedTaskError,
)
from .offline import OfflineProvider

__all__ = [
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "OfflineProvider",
    "UnsupportedTaskError",
    "build_provider",
]


def build_provider(settings: Settings) -> LLMProvider:
    """Resolve the configured provider.

    ``offline`` is the default and needs nothing. ``openai`` is imported lazily so
    the dependency stays optional.
    """
    provider = settings.llm_provider.strip().lower()
    if provider in {"offline", "", "deterministic"}:
        return OfflineProvider(model=settings.llm_model)
    if provider == "openai":
        from .openai_provider import OpenAIProvider

        model = None if settings.llm_model.startswith("offline-") else settings.llm_model
        return OpenAIProvider(model=model)
    raise LLMError(
        f"unknown LLM provider {settings.llm_provider!r}; expected 'offline' or 'openai'"
    )
