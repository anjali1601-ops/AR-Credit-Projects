"""Runtime settings. The default path is offline and local."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "workbench.sqlite"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    llm_provider: str
    openai_api_key: str
    openai_model: str
    host: str
    port: int


def get_settings() -> Settings:
    raw_db = os.environ.get("DEDUCTION_DB_PATH", "").strip()
    provider = os.environ.get("DEDUCTION_LLM_PROVIDER", "offline").strip().lower() or "offline"
    return Settings(
        db_path=Path(raw_db) if raw_db else DEFAULT_DB,
        llm_provider=provider,
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        host=os.environ.get("DEDUCTION_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=int(os.environ.get("DEDUCTION_PORT", "47241")),
    )
