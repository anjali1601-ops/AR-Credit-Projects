"""The guarded Text-to-SQL tool the Auditor agent uses.

Flow for every question:

    question + schema-scoped prompt
        -> LLM proposes {"sql", "params"}
        -> guardrails reject anything that is not a scoped read-only SELECT
        -> execute with bound parameters on a read-only transaction
        -> record the SQL, params, row count and timing on the case
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text as sql_text

from dra.db.schema_catalog import schema_prompt
from dra.db.session import readonly_connection
from dra.llm import LLMProvider, LLMRequest, LLMTask, get_llm, parse_json_object
from dra.llm.offline.sql_planner import QUERY_CATALOGUE
from dra.prompts import TEXT_TO_SQL_PROMPT
from dra.settings import get_settings
from dra.sql.guardrails import SQLGuardrailError, validate_sql


@dataclass
class QueryResult:
    intent: str
    question: str
    sql: str
    params: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None
    duration_ms: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "question": self.question,
            "sql": self.sql,
            "params": jsonable(self.params),
            "status": self.status,
            "row_count": len(self.rows),
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "notes": list(self.notes),
        }


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def dec(value: Any, default: str = "0") -> Decimal:
    """Coerce a raw SQL value to Decimal (SQLite hands back floats)."""
    if value is None:
        return Decimal(default)
    return Decimal(str(value)).quantize(Decimal("0.01"))


class TextToSQLTool:
    """Read-only ERP access for the agents. Collects its own audit trail."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm or get_llm()
        self._settings = get_settings()
        self.audit: list[QueryResult] = []

    def run(
        self,
        intent: str,
        params: dict[str, Any],
        question: str | None = None,
    ) -> QueryResult:
        catalogue_entry = QUERY_CATALOGUE.get(intent, {})
        question = question or catalogue_entry.get("question", intent)

        messages = TEXT_TO_SQL_PROMPT.format_messages(
            schema=schema_prompt(),
            question=question,
            params=", ".join(f":{k}" for k in params) or "(none)",
        )
        request = LLMRequest(
            task=LLMTask.GENERATE_SQL,
            messages=messages,
            context={"intent": intent, "params": params, "question": question},
        )

        result = QueryResult(intent=intent, question=question, sql="", params=params)
        started = time.perf_counter()
        try:
            proposal = parse_json_object(self._llm.generate(request).text)
            proposed_sql = str(proposal.get("sql", ""))
            # Bind values the caller supplied are authoritative; anything extra the
            # model proposed is only used for parameters we did not provide.
            bind: dict[str, Any] = {**(proposal.get("params") or {}), **params}

            validated = validate_sql(
                proposed_sql,
                bind,
                row_limit=self._settings.sql_row_limit,
            )
            result.sql = validated.sql
            result.params = validated.params
            result.notes = validated.notes
            result.rows = self._execute(validated.sql, validated.params)
        except SQLGuardrailError as exc:
            result.status = "blocked"
            result.error = str(exc)
            result.sql = result.sql or str(exc)
        except Exception as exc:  # noqa: BLE001 - surfaced on the case, not raised
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            result.duration_ms = (time.perf_counter() - started) * 1000
            self.audit.append(result)
        return result

    def _execute(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with readonly_connection() as conn:
            cursor = conn.execute(sql_text(sql), params)
            return [dict(row) for row in cursor.mappings().all()]
