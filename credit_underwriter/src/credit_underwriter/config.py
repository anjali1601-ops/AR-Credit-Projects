"""Runtime configuration.

Everything that can change the outcome of an underwriting run lives here and is
folded into :meth:`Settings.fingerprint`, which is stored on each persisted run.
Two runs with the same fingerprint and the same application must produce the same
memo -- that is what ``credit-underwriter verify`` asserts.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Deliberately uncommon so it does not collide with other dev servers.
DEFAULT_API_PORT = 47113

#: Frozen so a change to the scorecard or the reconciliation rules invalidates
#: previously persisted run hashes instead of silently comparing across versions.
ENGINE_VERSION = "1.0.0"

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot. Build with :meth:`from_env`."""

    # --- LLM -------------------------------------------------------------------
    llm_provider: str = "offline"
    llm_model: str = "offline-underwriter-v1"
    llm_temperature: float = 0.0

    # --- Retrieval -------------------------------------------------------------
    retrieval_backend: str = "chroma"
    retrieval_top_k: int = 4
    retrieval_embedding_dim: int = 192
    enable_live_search: bool = False
    live_search_provider: str = "tavily"
    live_search_max_results: int = 3

    # --- Underwriting policy ---------------------------------------------------
    as_of_date: str = "2026-09-22"
    max_memo_revisions: int = 2

    # --- Paths (excluded from the fingerprint) ---------------------------------
    data_dir: Path = field(default=PROJECT_ROOT / "data")
    runs_dir: Path = field(default=PROJECT_ROOT / "runs")
    memo_dir: Path = field(default=PROJECT_ROOT / "memos")
    chroma_dir: Path = field(default=PROJECT_ROOT / ".chroma")

    # --- API -------------------------------------------------------------------
    api_port: int = DEFAULT_API_PORT

    @classmethod
    def from_env(cls, **overrides: Any) -> Settings:
        base_dir = Path(os.environ.get("CREDIT_UNDERWRITER_HOME", str(PROJECT_ROOT)))
        values: dict[str, Any] = {
            "llm_provider": os.environ.get("CREDIT_UNDERWRITER_LLM_PROVIDER", "offline"),
            "llm_model": os.environ.get(
                "CREDIT_UNDERWRITER_LLM_MODEL", "offline-underwriter-v1"
            ),
            "retrieval_backend": os.environ.get(
                "CREDIT_UNDERWRITER_RETRIEVAL_BACKEND", "chroma"
            ),
            "retrieval_top_k": _env_int("CREDIT_UNDERWRITER_RETRIEVAL_TOP_K", 4),
            "enable_live_search": _env_flag("CREDIT_UNDERWRITER_ENABLE_LIVE_SEARCH", False),
            "live_search_provider": os.environ.get(
                "CREDIT_UNDERWRITER_LIVE_SEARCH_PROVIDER", "tavily"
            ),
            "as_of_date": os.environ.get("CREDIT_UNDERWRITER_AS_OF_DATE", "2026-09-22"),
            "max_memo_revisions": _env_int("CREDIT_UNDERWRITER_MAX_MEMO_REVISIONS", 2),
            "api_port": _env_int("CREDIT_UNDERWRITER_API_PORT", DEFAULT_API_PORT),
            "data_dir": base_dir / "data",
            "runs_dir": base_dir / "runs",
            "memo_dir": base_dir / "memos",
            "chroma_dir": base_dir / ".chroma",
        }
        if os.environ.get("CREDIT_UNDERWRITER_DATA_DIR"):
            values["data_dir"] = Path(os.environ["CREDIT_UNDERWRITER_DATA_DIR"])
        values.update(overrides)
        return cls(**values)

    # ------------------------------------------------------------------
    @property
    def applicants_dir(self) -> Path:
        return self.data_dir / "applicants"

    @property
    def corpus_path(self) -> Path:
        return self.data_dir / "corpus" / "risk_corpus.json"

    def fingerprint_payload(self) -> dict[str, Any]:
        """The subset of settings that can change an underwriting outcome."""
        exclude = {"data_dir", "runs_dir", "memo_dir", "chroma_dir", "api_port"}
        payload = {k: v for k, v in asdict(self).items() if k not in exclude}
        payload["engine_version"] = ENGINE_VERSION
        return payload

    def fingerprint(self) -> str:
        blob = json.dumps(self.fingerprint_payload(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def with_overrides(self, **overrides: Any) -> Settings:
        merged = {**{k: getattr(self, k) for k in self.__dataclass_fields__}, **overrides}
        return Settings(**merged)


def ensure_dirs(settings: Settings) -> None:
    for path in (settings.runs_dir, settings.memo_dir):
        path.mkdir(parents=True, exist_ok=True)
