"""LLM abstraction: one interface, a deterministic default, hosted opt-ins."""

from __future__ import annotations

from functools import lru_cache

from dra.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResult,
    LLMTask,
    parse_json_object,
)
from dra.llm.mock import MockLLM
from dra.settings import get_settings


@lru_cache(maxsize=4)
def _build(provider: str) -> LLMProvider:
    settings = get_settings()
    choice = provider.strip().lower()
    if choice in {"mock", "offline", "deterministic", ""}:
        return MockLLM()
    if choice == "openai":
        from dra.llm.hosted import OpenAILLM

        return OpenAILLM(
            api_key=settings.openai_api_key or "",
            model=settings.openai_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
        )
    if choice == "anthropic":
        from dra.llm.hosted import AnthropicLLM

        return AnthropicLLM(
            api_key=settings.anthropic_api_key or "",
            model=settings.anthropic_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
        )
    raise ValueError(
        f"unknown DRA_LLM_PROVIDER '{provider}'; expected mock, openai or anthropic"
    )


def get_llm(provider: str | None = None) -> LLMProvider:
    return _build(provider or get_settings().llm_provider)


def reset_llm_cache() -> None:
    _build.cache_clear()


__all__ = [
    "LLMProvider",
    "LLMRequest",
    "LLMResult",
    "LLMTask",
    "MockLLM",
    "get_llm",
    "parse_json_object",
    "reset_llm_cache",
]
