"""ERP tables plus the dispute workflow tables.

The ``erp`` group models the read-only system of record the Auditor agent
interrogates with Text-to-SQL. The ``workflow`` group holds the persisted case
state machine; only explicit application code ever writes to it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TWO_PLACES = Decimal("0.01")


class Money(TypeDecorator):
    """Decimal money that still behaves numerically inside raw SQL on SQLite.

    SQLite has no decimal type. Storing as REAL keeps ``SUM``/``>`` working for
    the generated Text-to-SQL queries, and values are quantized back to two
    places whenever they are read through the ORM.
    """

    impl = Numeric(14, 2)
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Float())
        return dialect.type_descriptor(Numeric(14, 2))

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        quantized = Decimal(str(value)).quantize(TWO_PLACES)
        return float(quantized) if dialect.name == "sqlite" else quantized

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return Decimal(str(value)).quantize(TWO_PLACES)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# ERP system of record
# --------------------------------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    segment: Mapped[str] = mapped_column(String(40), default="retail")
    email_domain: Mapped[str] = mapped_column(String(120), index=True)
    ar_contact_name: Mapped[str] = mapped_column(String(120))
    ar_contact_email: Mapped[str] = mapped_column(String(160))
    payment_terms: Mapped[str] = mapped_column(String(40), default="NET30")
    credit_limit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="customer")
    contracts: Mapped[list["Contract"]] = relationship(back_populates="customer")
    discount_agreements: Mapped[list["DiscountAgreement"]] = relationship(
        back_populates="customer"
    )


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    contract_number: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    effective_date: Mapped[dt.date] = mapped_column(Date)
    expiry_date: Mapped[dt.date] = mapped_column(Date)
    document_filename: Mapped[str] = mapped_column(String(160))
    claim_notice_days: Mapped[int] = mapped_column(Integer, default=10)
    summary: Mapped[str] = mapped_column(Text, default="")

    customer: Mapped[Customer] = relationship(back_populates="contracts")


class DiscountAgreement(Base):
    __tablename__ = "discount_agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"))
    code: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(240))
    discount_type: Mapped[str] = mapped_column(String(32))  # early_payment|volume|promotional
    rate_pct: Mapped[float] = mapped_column(Float, default=0.0)
    pay_within_days: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[dt.date] = mapped_column(Date)
    valid_to: Mapped[dt.date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    conditions: Mapped[str] = mapped_column(Text, default="")

    customer: Mapped[Customer] = relationship(back_populates="discount_agreements")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    po_number: Mapped[str] = mapped_column(String(32), index=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    due_date: Mapped[dt.date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    amount_paid: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(24), default="open")

    customer: Mapped[Customer] = relationship(back_populates="invoices")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="invoice")
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice")

    @property
    def open_balance(self) -> Decimal:
        return (self.total_amount - self.amount_paid).quantize(TWO_PLACES)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (UniqueConstraint("invoice_id", "line_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    shipment_number: Mapped[str] = mapped_column(String(32), unique=True)
    carrier: Mapped[str] = mapped_column(String(60))
    tracking_number: Mapped[str] = mapped_column(String(48))
    ship_date: Mapped[dt.date] = mapped_column(Date)
    delivery_date: Mapped[dt.date | None] = mapped_column(Date)
    pod_signed_by: Mapped[str | None] = mapped_column(String(120))
    # clean | damage_noted | short_delivered | refused
    condition_on_delivery: Mapped[str] = mapped_column(String(32), default="clean")
    exception_notes: Mapped[str] = mapped_column(Text, default="")

    invoice: Mapped[Invoice] = relationship(back_populates="shipments")
    lines: Mapped[list["ShipmentLine"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class ShipmentLine(Base):
    __tablename__ = "shipment_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    quantity_ordered: Mapped[int] = mapped_column(Integer)
    quantity_shipped: Mapped[int] = mapped_column(Integer)
    quantity_damaged: Mapped[int] = mapped_column(Integer, default=0)
    unit_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    shipment: Mapped[Shipment] = relationship(back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    payment_reference: Mapped[str] = mapped_column(String(32), unique=True)
    payment_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(String(24), default="ACH")
    deduction_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    deduction_code: Mapped[str | None] = mapped_column(String(32))
    remittance_note: Mapped[str] = mapped_column(Text, default="")

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


# --------------------------------------------------------------------------
# Dispute workflow state (written only by application code)
# --------------------------------------------------------------------------


class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    sender: Mapped[str] = mapped_column(String(160))
    sender_name: Mapped[str] = mapped_column(String(120), default="")
    recipient: Mapped[str] = mapped_column(String(160))
    subject: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    # unread | processed | skipped | failed
    status: Mapped[str] = mapped_column(String(16), default="unread", index=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")
    case_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_cases.id"))


class DisputeCase(Base):
    __tablename__ = "dispute_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_number: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(24), default="received", index=True)
    owner_agent: Mapped[str] = mapped_column(String(32), default="ingestion")

    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    source_message_id: Mapped[str | None] = mapped_column(String(80))

    invoice_number: Mapped[str | None] = mapped_column(String(32), index=True)
    reason_code: Mapped[str | None] = mapped_column(String(40))
    reason_text: Mapped[str | None] = mapped_column(Text)
    disputed_amount: Mapped[Decimal | None] = mapped_column(Money)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)

    # valid | invalid | needs_more_info
    decision: Mapped[str | None] = mapped_column(String(20), index=True)
    decision_confidence: Mapped[float | None] = mapped_column(Float)
    decision_rationale: Mapped[str | None] = mapped_column(Text)
    cited_clause_ref: Mapped[str | None] = mapped_column(String(80))
    cited_clause_text: Mapped[str | None] = mapped_column(Text)
    approved_credit_amount: Mapped[Decimal | None] = mapped_column(Money)

    evidence_json: Mapped[str | None] = mapped_column(Text)
    extraction_json: Mapped[str | None] = mapped_column(Text)

    approval_token: Mapped[str | None] = mapped_column(String(48), index=True)
    resolution: Mapped[str | None] = mapped_column(String(40))
    resolved_by: Mapped[str | None] = mapped_column(String(120))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CaseEvent.seq"
    )
    drafts: Mapped[list["CaseDraft"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CaseDraft.id"
    )
    queries: Mapped[list["SqlAuditEntry"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="SqlAuditEntry.id"
    )
    credit_memo: Mapped["CreditMemo | None"] = relationship(
        back_populates="case", cascade="all, delete-orphan", uselist=False
    )


class CaseEvent(Base):
    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_cases.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    from_agent: Mapped[str | None] = mapped_column(String(32))
    to_agent: Mapped[str | None] = mapped_column(String(32))
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str | None] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    case: Mapped[DisputeCase] = relationship(back_populates="events")


class CaseDraft(Base):
    __tablename__ = "case_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_cases.id"), index=True)
    # customer_email | supervisor_email | credit_memo
    kind: Mapped[str] = mapped_column(String(32), index=True)
    recipient: Mapped[str] = mapped_column(String(160), default="")
    subject: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    case: Mapped[DisputeCase] = relationship(back_populates="drafts")


class CreditMemo(Base):
    __tablename__ = "credit_memos"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_cases.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    memo_number: Mapped[str] = mapped_column(String(24), unique=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    reason_code: Mapped[str] = mapped_column(String(40))
    narrative: Mapped[str] = mapped_column(Text, default="")
    # draft | issued | voided
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    issued_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    issued_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    case: Mapped[DisputeCase] = relationship(back_populates="credit_memo")


class SqlAuditEntry(Base):
    """Every Text-to-SQL query the Auditor runs, kept for explainability."""

    __tablename__ = "sql_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_cases.id"), index=True)
    agent: Mapped[str] = mapped_column(String(32), default="auditor")
    question: Mapped[str] = mapped_column(Text)
    generated_sql: Mapped[str] = mapped_column(Text)
    params_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    case: Mapped[DisputeCase | None] = relationship(back_populates="queries")


ERP_TABLES = (
    "customers",
    "contracts",
    "discount_agreements",
    "invoices",
    "invoice_lines",
    "shipments",
    "shipment_lines",
    "payments",
)
