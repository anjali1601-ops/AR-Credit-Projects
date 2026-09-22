"""Runtime configuration, resolved from environment variables with offline-safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]

# The seed dataset is generated relative to this date so that aging buckets,
# "days since last contact" and every derived metric stay stable over time.
AS_OF = date(2026, 9, 18)

DEFAULT_HF_MODEL = "distilbert-base-uncased-finetuned-sst-2-english"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class Settings:
    """Every knob the demo needs. Defaults require no network and no API keys."""

    # "mock" is a deterministic offline writer; "openai" hits a real endpoint.
    llm_provider: str = field(default_factory=lambda: _env("DUNNING_LLM_PROVIDER", "mock"))
    llm_model: str = field(default_factory=lambda: _env("DUNNING_LLM_MODEL", "gpt-4o-mini"))

    # "auto" tries HuggingFace and silently degrades to the lexicon classifier.
    sentiment_backend: str = field(default_factory=lambda: _env("DUNNING_SENTIMENT_BACKEND", "auto"))
    hf_sentiment_model: str = field(default_factory=lambda: _env("DUNNING_HF_MODEL", DEFAULT_HF_MODEL))

    # "auto" picks Langfuse or Phoenix when configured, else the local file tracer.
    tracing_backend: str = field(default_factory=lambda: _env("DUNNING_TRACING", "auto"))

    data_dir: Path = field(default_factory=lambda: Path(_env("DUNNING_DATA_DIR", str(PROJECT_ROOT / "data"))))
    trace_dir: Path = field(default_factory=lambda: Path(_env("DUNNING_TRACE_DIR", str(PROJECT_ROOT / ".traces"))))

    as_of: date = AS_OF
    seed: int = field(default_factory=lambda: int(_env("DUNNING_SEED", "20260918")))
    max_draft_revisions: int = 2

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "dunning.db"

    def langfuse_configured(self) -> bool:
        return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))

    def phoenix_configured(self) -> bool:
        return bool(os.environ.get("PHOENIX_COLLECTOR_ENDPOINT") or os.environ.get("PHOENIX_CLIENT_HEADERS"))


def get_settings(**overrides) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        if value is not None:
            setattr(settings, key, value)
    return settings
