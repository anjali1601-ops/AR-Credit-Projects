"""The schema slice the Text-to-SQL path is allowed to see and touch.

The catalogue is derived from the SQLAlchemy metadata so it cannot drift from
the real tables, and it is deliberately limited to the ERP system of record.
Workflow tables (cases, drafts, credit memos, audit log) are never described to
the model and are rejected by the guardrails if a query mentions them.
"""

from __future__ import annotations

from functools import lru_cache

from dra.db.models import ERP_TABLES, Base

TABLE_NOTES: dict[str, str] = {
    "customers": "One row per billed customer. `code` is the external customer number.",
    "contracts": (
        "Master agreements. `claim_notice_days` is the contractual deadline, in days "
        "after delivery, for raising a damage or shortage claim."
    ),
    "discount_agreements": (
        "Negotiated discounts. `discount_type` is one of early_payment, volume, "
        "promotional. An early_payment discount is only earned when the payment lands "
        "within `pay_within_days` of the invoice date and between valid_from/valid_to."
    ),
    "invoices": (
        "Billing documents. `amount_paid` is cash applied so far; total_amount - "
        "amount_paid is the open balance being short-paid."
    ),
    "invoice_lines": "Billed lines with sku, quantity and unit_price.",
    "shipments": (
        "Outbound deliveries tied to an invoice. `condition_on_delivery` is one of "
        "clean, damage_noted, short_delivered, refused, and comes from the signed "
        "proof of delivery. `delivery_date` starts the contractual claim window."
    ),
    "shipment_lines": (
        "Per-sku quantities: quantity_ordered vs quantity_shipped reveals a short "
        "shipment; quantity_damaged is what the carrier/POD recorded as damaged."
    ),
    "payments": (
        "Cash receipts. `deduction_amount` is the short-payment the customer took and "
        "`deduction_code` is the reason code on the remittance advice."
    ),
}

ALLOWED_TABLES: frozenset[str] = frozenset(ERP_TABLES)


@lru_cache(maxsize=1)
def schema_prompt() -> str:
    """Render the schema-scoped context injected into every Text-to-SQL prompt."""
    blocks: list[str] = []
    for table_name in ERP_TABLES:
        table = Base.metadata.tables[table_name]
        cols = []
        for col in table.columns:
            flags = []
            if col.primary_key:
                flags.append("pk")
            for fk in col.foreign_keys:
                flags.append(f"fk->{fk.target_fullname}")
            suffix = f"  -- {', '.join(flags)}" if flags else ""
            cols.append(f"    {col.name} {col.type}{suffix}")
        note = TABLE_NOTES.get(table_name, "")
        blocks.append(f"TABLE {table_name}  -- {note}\n" + "\n".join(cols))
    return "\n\n".join(blocks)


@lru_cache(maxsize=1)
def allowed_columns() -> dict[str, frozenset[str]]:
    return {
        name: frozenset(c.name for c in Base.metadata.tables[name].columns)
        for name in ERP_TABLES
    }
