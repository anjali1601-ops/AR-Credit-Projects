"""Runtime configuration.

Every setting has an offline default so the whole system runs with no API keys
and no external services. Override with ``DRA_*`` environment variables or a
``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage locations -------------------------------------------------
    data_dir: Path = Field(default=PROJECT_ROOT / "var")

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-sonnet-latest"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 45.0

    # --- database ----------------------------------------------------------
    # Empty means "SQLite file under data_dir".
    database_url: str | None = None
    # Optional dedicated read-only connection for the Text-to-SQL path. When
    # unset the same URL is reused with session-level read-only enforcement.
    readonly_database_url: str | None = None
    sql_echo: bool = False
    sql_row_limit: int = 200
    sql_statement_timeout_ms: int = 5000

    # --- vector store ------------------------------------------------------
    vector_store: str = "chroma"
    vector_collection: str = "contract_clauses"
    rag_top_k: int = 4

    # --- service -----------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 47821
    public_base_url: str | None = None
    inbox_poll_enabled: bool = True
    inbox_poll_interval_seconds: float = 3.0

    # --- business policy ---------------------------------------------------
    auto_approve_limit: float = 0.0
    supervisor_email: str = "ar.supervisor@acme-supply.example"
    company_name: str = "Acme Supply Co."
    company_ar_email: str = "ar@acme-supply.example"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "dra.sqlite3"

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.sqlite_path}"

    @property
    def effective_readonly_database_url(self) -> str:
        return self.readonly_database_url or self.effective_database_url

    @property
    def is_sqlite(self) -> bool:
        return self.effective_database_url.startswith("sqlite")

    @property
    def contracts_pdf_dir(self) -> Path:
        return self.data_dir / "contracts"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        return f"http://{self.api_host}:{self.api_port}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.contracts_pdf_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Used by tests that change the environment between cases."""
    get_settings.cache_clear()
