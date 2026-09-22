"""Offline Text-to-SQL planner.

A hosted LLM writes free-form SQL from the schema-scoped prompt. Offline we
emit the same queries from a small intent catalogue so the demo is byte-for-byte
reproducible. Both paths return ``{"sql", "params"}`` and both are then pushed
through the identical guardrails and read-only executor — the planner has no
privileged route to the database.
"""

from __future__ import annotations

from typing import Any

QUERY_CATALOGUE: dict[str, dict[str, Any]] = {
    "invoice_snapshot": {
        "question": "What are the header details and open balance for this invoice?",
        "sql": """
SELECT i.invoice_number,
       i.po_number,
       i.issue_date,
       i.due_date,
       i.currency,
       i.subtotal,
       i.tax_amount,
       i.total_amount,
       i.amount_paid,
       i.total_amount - i.amount_paid AS open_balance,
       i.status,
       c.code AS customer_code,
       c.name AS customer_name,
       c.payment_terms,
       c.ar_contact_name,
       c.ar_contact_email
FROM invoices i
JOIN customers c ON c.id = i.customer_id
WHERE i.invoice_number = :invoice_number
""",
        "required": ("invoice_number",),
    },
    "invoice_lines": {
        "question": "Which lines were billed on this invoice?",
        "sql": """
SELECT l.line_no, l.sku, l.description, l.quantity, l.unit_price, l.line_total
FROM invoice_lines l
JOIN invoices i ON i.id = l.invoice_id
WHERE i.invoice_number = :invoice_number
ORDER BY l.line_no
""",
        "required": ("invoice_number",),
    },
    "payment_history": {
        "question": "What cash has been applied and what deduction did the customer take?",
        "sql": """
SELECT p.payment_reference,
       p.payment_date,
       p.amount,
       p.method,
       p.deduction_amount,
       p.deduction_code,
       p.remittance_note
FROM payments p
JOIN invoices i ON i.id = p.invoice_id
WHERE i.invoice_number = :invoice_number
ORDER BY p.payment_date
""",
        "required": ("invoice_number",),
    },
    "shipment_records": {
        "question": "What do the shipping logs and proof of delivery say?",
        "sql": """
SELECT s.shipment_number,
       s.carrier,
       s.tracking_number,
       s.ship_date,
       s.delivery_date,
       s.pod_signed_by,
       s.condition_on_delivery,
       s.exception_notes,
       sl.sku,
       sl.quantity_ordered,
       sl.quantity_shipped,
       sl.quantity_damaged,
       sl.unit_price
FROM shipments s
JOIN shipment_lines sl ON sl.shipment_id = s.id
JOIN invoices i ON i.id = s.invoice_id
WHERE i.invoice_number = :invoice_number
ORDER BY s.ship_date, sl.sku
""",
        "required": ("invoice_number",),
    },
    "discount_agreements": {
        "question": "Which discount agreements exist for this customer?",
        "sql": """
SELECT d.code,
       d.description,
       d.discount_type,
       d.rate_pct,
       d.pay_within_days,
       d.valid_from,
       d.valid_to,
       d.is_active,
       d.conditions
FROM discount_agreements d
JOIN customers c ON c.id = d.customer_id
JOIN invoices i ON i.customer_id = c.id
WHERE i.invoice_number = :invoice_number
ORDER BY d.valid_from DESC
""",
        "required": ("invoice_number",),
    },
    "contract_terms": {
        "question": "Which contract governs this invoice and what is the claim window?",
        "sql": """
SELECT ct.contract_number,
       ct.title,
       ct.effective_date,
       ct.expiry_date,
       ct.claim_notice_days,
       ct.document_filename,
       ct.summary
FROM contracts ct
JOIN customers c ON c.id = ct.customer_id
JOIN invoices i ON i.customer_id = c.id
WHERE i.invoice_number = :invoice_number
ORDER BY ct.effective_date DESC
""",
        "required": ("invoice_number",),
    },
    "duplicate_invoices": {
        "question": "Was the same purchase order billed more than once?",
        "sql": """
SELECT dup.invoice_number,
       dup.po_number,
       dup.issue_date,
       dup.total_amount,
       dup.status
FROM invoices dup
JOIN invoices src
  ON src.po_number = dup.po_number
 AND src.customer_id = dup.customer_id
WHERE src.invoice_number = :invoice_number
  AND dup.invoice_number <> :invoice_number
ORDER BY dup.issue_date
""",
        "required": ("invoice_number",),
    },
}


class UnknownIntentError(KeyError):
    pass


def plan_sql(context: dict[str, Any]) -> dict[str, Any]:
    intent = str(context.get("intent", ""))
    entry = QUERY_CATALOGUE.get(intent)
    if entry is None:
        raise UnknownIntentError(
            f"unknown query intent '{intent}'; known: {sorted(QUERY_CATALOGUE)}"
        )
    params = dict(context.get("params") or {})
    missing = [name for name in entry["required"] if name not in params]
    if missing:
        raise ValueError(f"intent '{intent}' requires params: {', '.join(missing)}")
    return {
        "sql": entry["sql"].strip(),
        "params": {name: params[name] for name in entry["required"]},
        "rationale": entry["question"],
    }
