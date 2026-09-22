"""Select the narrator. Routing never reads the narrative."""

from __future__ import annotations

from deduction_workbench.config import Settings, get_settings
from deduction_workbench.models import NarrativeBrief


class Narrator:
    name = "offline"

    def narrate(self, brief: NarrativeBrief) -> str:  # pragma: no cover - interface
        raise NotImplementedError


def get_narrator(settings: Settings | None = None) -> Narrator:
    chosen = settings or get_settings()
    provider = chosen.llm_provider
    if provider in {"", "offline", "mock", "local"}:
        from deduction_workbench.llm.offline import OfflineNarrator

        return OfflineNarrator()
    if provider == "openai":
        if not chosen.openai_api_key:
            from deduction_workbench.llm.offline import OfflineNarrator

            return OfflineNarrator()
        from deduction_workbench.llm.openai_provider import OpenAINarrator

        return OpenAINarrator(api_key=chosen.openai_api_key, model=chosen.openai_model)
    raise ValueError(
        f"Unknown DEDUCTION_LLM_PROVIDER={provider!r}. Use 'offline' or 'openai'."
    )
