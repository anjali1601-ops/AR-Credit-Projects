"""Static validation for model-generated SQL.

This is the first of three defences on the Text-to-SQL path:

1. these guardrails reject anything that is not a single, read-only, schema
   scoped ``SELECT``/``WITH`` over allow-listed ERP tables;
2. values are always bound parameters, never string-formatted into the SQL;
3. execution happens on a connection whose transaction the database itself
   holds read-only (``PRAGMA query_only`` / ``SET TRANSACTION READ ONLY``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlparse

from dra.db.schema_catalog import ALLOWED_TABLES

# Anything that writes, changes session state, escapes to the filesystem, or
# reaches another server. Matched as whole words on the comment-stripped SQL.
FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "replace",
    "merge",
    "upsert",
    "grant",
    "revoke",
    "commit",
    "rollback",
    "savepoint",
    "begin",
    "vacuum",
    "reindex",
    "attach",
    "detach",
    "pragma",
    "set",
    "copy",
    "call",
    "exec",
    "execute",
    "prepare",
    "deallocate",
    "listen",
    "notify",
    "lock",
    "refresh",
    "do",
)

FORBIDDEN_FUNCTIONS: tuple[str, ...] = (
    "load_extension",
    "readfile",
    "writefile",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_sleep",
    "pg_terminate_backend",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "query_to_xml",
)

_TABLE_REF = re.compile(r"\b(?:from|join)\s+(?:only\s+)?([a-zA-Z_][\w$]*(?:\.[\w$]+)?)", re.I)
_CTE_NAME = re.compile(r"(?:\bwith\b|,)\s*([a-zA-Z_][\w$]*)\s+as\s*\(", re.I)
_BIND_PARAM = re.compile(r"(?<![:\w]):([a-zA-Z_]\w*)")
_LIMIT_CLAUSE = re.compile(r"\blimit\b", re.I)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class SQLGuardrailError(ValueError):
    """Raised when generated SQL violates a safety rule."""


@dataclass(frozen=True)
class ValidatedQuery:
    """SQL that passed every static check, ready for parameterized execution."""

    sql: str
    params: dict[str, object]
    tables: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())


def _strip_comments(sql: str) -> str:
    return sqlparse.format(sql, strip_comments=True).strip()


def _unwrap_fences(sql: str) -> str:
    """Models like to wrap SQL in markdown fences. Remove them before parsing."""
    text = sql.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    return text.strip()


def _mask_literals(sql: str) -> str:
    """Blank out string literals so keyword scanning never trips on data."""
    return _STRING_LITERAL.sub("''", sql)


def validate_sql(
    sql: str,
    params: dict[str, object] | None = None,
    *,
    row_limit: int = 200,
    allowed_tables: frozenset[str] | None = None,
) -> ValidatedQuery:
    """Validate and normalize model-generated SQL, or raise ``SQLGuardrailError``."""
    params = dict(params or {})
    allowed = allowed_tables if allowed_tables is not None else ALLOWED_TABLES
    notes: list[str] = []

    candidate = _strip_comments(_unwrap_fences(sql))
    if not candidate:
        raise SQLGuardrailError("empty statement")
    if candidate != _unwrap_fences(sql).strip():
        notes.append("comments stripped")

    statements = [s for s in sqlparse.split(candidate) if s.strip().rstrip(";").strip()]
    if len(statements) > 1:
        raise SQLGuardrailError(
            f"only one statement is allowed, got {len(statements)}"
        )
    statement = statements[0].strip().rstrip(";").strip()

    scan = _mask_literals(statement)
    lowered = scan.lower()

    first = lowered.split(None, 1)[0] if lowered.split() else ""
    if first not in {"select", "with"}:
        raise SQLGuardrailError(
            f"statement must start with SELECT or WITH, got {first.upper() or '<empty>'}"
        )

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            raise SQLGuardrailError(f"forbidden keyword: {keyword.upper()}")

    for func in FORBIDDEN_FUNCTIONS:
        if re.search(rf"\b{re.escape(func)}\s*\(", lowered):
            raise SQLGuardrailError(f"forbidden function: {func}")

    # `SELECT ... INTO new_table` is a write in disguise.
    if re.search(r"\binto\b", lowered):
        raise SQLGuardrailError("forbidden keyword: INTO")

    cte_names = {m.lower() for m in _CTE_NAME.findall(scan)}
    referenced = {t.lower() for t in _TABLE_REF.findall(scan)}
    real_tables = sorted(referenced - cte_names)

    if not real_tables:
        raise SQLGuardrailError("query does not reference any known table")

    for table in real_tables:
        if "." in table:
            raise SQLGuardrailError(
                f"schema-qualified names are not allowed: {table}"
            )
        if table not in allowed:
            raise SQLGuardrailError(
                f"table '{table}' is outside the read-only ERP schema"
            )

    declared = set(_BIND_PARAM.findall(scan))
    missing = declared - set(params)
    if missing:
        raise SQLGuardrailError(
            f"missing bind values for parameter(s): {', '.join(sorted(missing))}"
        )
    unused = set(params) - declared
    if unused:
        # Harmless, but surfacing it keeps the audit trail honest.
        notes.append(f"unused params: {', '.join(sorted(unused))}")

    final_sql = statement
    if not _LIMIT_CLAUSE.search(_mask_literals(final_sql)):
        final_sql = f"{final_sql}\nLIMIT {int(row_limit)}"
        notes.append(f"row limit {row_limit} applied")

    return ValidatedQuery(
        sql=final_sql,
        params=params,
        tables=tuple(real_tables),
        notes=tuple(notes),
    )
