"""Hosted providers. Opt in with ``DRA_LLM_PROVIDER=openai|anthropic``.

The SDKs are imported lazily so the offline default never needs them installed.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dra.llm.base import LLMProvider, LLMRequest


def _to_role_dicts(request: LLMRequest) -> list[dict[str, str]]:
    roles = {SystemMessage: "system", HumanMessage: "user", AIMessage: "assistant"}
    out: list[dict[str, str]] = []
    for message in request.messages:
        role = roles.get(type(message), "user")
        out.append({"role": role, "content": str(message.content)})
    return out


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, temperature: float, timeout: float) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "DRA_LLM_PROVIDER=openai requires the openai package: pip install openai"
            ) from exc
        if not api_key:
            raise RuntimeError("DRA_OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=api_key, timeout=timeout)
        self.model = model
        self._temperature = temperature

    def _generate(self, request: LLMRequest) -> str:  # pragma: no cover - network
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self._temperature,
            max_tokens=request.max_tokens,
            response_format={"type": "json_object"},
            messages=_to_role_dicts(request),
        )
        return response.choices[0].message.content or ""


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, temperature: float, timeout: float) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "DRA_LLM_PROVIDER=anthropic requires the anthropic package: "
                "pip install anthropic"
            ) from exc
        if not api_key:
            raise RuntimeError("DRA_ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model
        self._temperature = temperature

    def _generate(self, request: LLMRequest) -> str:  # pragma: no cover - network
        messages = _to_role_dicts(request)
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        response = self._client.messages.create(
            model=self.model,
            system=system,
            temperature=self._temperature,
            max_tokens=request.max_tokens,
            messages=turns,
        )
        return "".join(block.text for block in response.content if block.type == "text")
