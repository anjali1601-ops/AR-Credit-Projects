"""The Text-to-SQL safety net: static guardrails plus database-level enforcement."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from dra.db.session import readonly_connection
from dra.sql.guardrails import SQLGuardrailError, validate_sql

GOOD = "SELECT invoice_number, total_amount FROM invoices WHERE invoice_number = :invoice_number"


def test_allows_a_scoped_select_and_binds_parameters() -> None:
    result = validate_sql(GOOD, {"invoice_number": "INV-2025-0148"})
    assert result.tables == ("invoices",)
    assert result.params == {"invoice_number": "INV-2025-0148"}
    assert "LIMIT" in result.sql


def test_applies_a_row_limit_when_the_model_omits_one() -> None:
    result = validate_sql("SELECT * FROM payments", {}, row_limit=25)
    assert result.sql.strip().endswith("LIMIT 25")


def test_keeps_an_explicit_limit() -> None:
    result = validate_sql("SELECT * FROM payments LIMIT 3", {})
    assert result.sql.count("LIMIT") == 1


def test_allows_common_table_expressions() -> None:
    sql = """
    WITH shipped AS (
        SELECT shipment_id, SUM(quantity_shipped) AS qty FROM shipment_lines GROUP BY shipment_id
    )
    SELECT s.shipment_number, shipped.qty
    FROM shipments s JOIN shipped ON shipped.shipment_id = s.id
    """
    result = validate_sql(sql, {})
    assert set(result.tables) == {"shipment_lines", "shipments"}


def test_strips_markdown_fences_a_model_might_emit() -> None:
    result = validate_sql("```sql\nSELECT id FROM customers\n```", {})
    assert result.sql.startswith("SELECT id FROM customers")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE invoices SET amount_paid = 0",
        "DELETE FROM payments WHERE id = 1",
        "INSERT INTO payments (amount) VALUES (1)",
        "DROP TABLE invoices",
        "ALTER TABLE invoices ADD COLUMN sneaky TEXT",
        "CREATE TABLE evil (id INT)",
        "TRUNCATE TABLE payments",
        "GRANT ALL ON invoices TO PUBLIC",
        "VACUUM",
        "PRAGMA table_info(invoices)",
        "ATTACH DATABASE '/tmp/x.db' AS x",
    ],
)
def test_rejects_every_write_or_ddl_statement(statement: str) -> None:
    with pytest.raises(SQLGuardrailError):
        validate_sql(statement, {})


def test_rejects_a_second_statement_smuggled_after_a_select() -> None:
    with pytest.raises(SQLGuardrailError, match="one statement"):
        validate_sql("SELECT 1 FROM invoices; DROP TABLE invoices", {})


def test_rejects_a_write_hidden_behind_a_comment() -> None:
    sneaky = "SELECT id FROM invoices -- harmless\n; UPDATE invoices SET status = 'paid'"
    with pytest.raises(SQLGuardrailError):
        validate_sql(sneaky, {})


def test_rejects_select_into_which_creates_a_table() -> None:
    with pytest.raises(SQLGuardrailError, match="INTO"):
        validate_sql("SELECT * INTO copy_of_invoices FROM invoices", {})


def test_rejects_tables_outside_the_erp_schema() -> None:
    with pytest.raises(SQLGuardrailError, match="outside the read-only ERP schema"):
        validate_sql("SELECT approval_token FROM dispute_cases", {})


def test_rejects_database_metadata_tables() -> None:
    with pytest.raises(SQLGuardrailError):
        validate_sql("SELECT name FROM sqlite_master", {})


def test_rejects_schema_qualified_names() -> None:
    with pytest.raises(SQLGuardrailError, match="schema-qualified"):
        validate_sql("SELECT * FROM public.invoices", {})


def test_rejects_filesystem_and_network_functions() -> None:
    with pytest.raises(SQLGuardrailError, match="forbidden function"):
        validate_sql("SELECT pg_read_file('/etc/passwd') FROM invoices", {})


def test_requires_a_value_for_every_bind_parameter() -> None:
    with pytest.raises(SQLGuardrailError, match="missing bind values"):
        validate_sql(GOOD, {})


def test_literals_never_trip_the_keyword_scanner() -> None:
    result = validate_sql(
        "SELECT * FROM payments WHERE remittance_note = 'please update your records'", {}
    )
    assert result.tables == ("payments",)


def test_the_database_itself_refuses_writes_on_the_readonly_connection(seeded: dict) -> None:
    """Defence in depth: even if a write got past the guardrails, it cannot land."""
    with readonly_connection() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM invoices")).scalar() > 0
        with pytest.raises(Exception):
            conn.execute(text("UPDATE invoices SET status = 'tampered'"))
