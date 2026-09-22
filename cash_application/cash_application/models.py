"""SQLite ERP plus the cash-application workflow.

Customers and invoices are the system of record. Remittances, proposals,
and candidates are the agent workpapers. Cash receipts and applications
are the ledger, and only the confirm path writes them.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from cash_application.money import money


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Money(TypeDecorator):
    """Store cents as text so SQLite cannot turn 7333.33 into a float."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return f"{money(value)}"

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        return Decimal(value)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    aliases: Mapped[str] = mapped_column(Text, default="[]")
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(2))
    payment_terms: Mapped[str] = mapped_column(String(32))

    invoices: Mapped[list[Invoice]] = relationship(back_populates="customer")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    po_number: Mapped[str | None] = mapped_column(String(40))
    issue_date: Mapped[dt.date] = mapped_column(Date)
    due_date: Mapped[dt.date] = mapped_column(Date)
    original_amount: Mapped[Decimal] = mapped_column(Money)
    open_amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    description: Mapped[str] = mapped_column(String(240))

    customer: Mapped[Customer] = relationship(back_populates="invoices")
    applications: Mapped[list[Application]] = relationship(back_populates="invoice")


class Remittance(Base):
    """A lockbox stub, an EDI 820, or a short-pay email. Not yet cash."""

    __tablename__ = "remittances"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)
    received_on: Mapped[dt.date] = mapped_column(Date)
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text)

    payer_name: Mapped[str | None] = mapped_column(String(160))
    payer_account: Mapped[str | None] = mapped_column(String(32))
    amount: Mapped[Decimal | None] = mapped_column(Money)
    payment_reference: Mapped[str | None] = mapped_column(String(64))
    invoice_numbers: Mapped[str] = mapped_column(Text, default="[]")
    reference_values: Mapped[str] = mapped_column(Text, default="[]")
    line_amounts: Mapped[str] = mapped_column(Text, default="{}")
    deduction_note: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[str] = mapped_column(Text, default="[]")

    proposal: Mapped[Proposal | None] = relationship(back_populates="remittance")
    receipt: Mapped[CashReceipt | None] = relationship(back_populates="remittance")


class Proposal(Base):
    """Matcher output waiting on a clerk. Status stays off the ledger."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    remittance_id: Mapped[int] = mapped_column(ForeignKey("remittances.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[str] = mapped_column(String(8))
    queue: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    customer_account: Mapped[str | None] = mapped_column(String(32))
    customer_name: Mapped[str | None] = mapped_column(String(160))
    payment_amount: Mapped[Decimal] = mapped_column(Money)
    applied_amount: Mapped[Decimal] = mapped_column(Money)
    unapplied_cash: Mapped[Decimal] = mapped_column(Money)
    short_fall: Mapped[Decimal] = mapped_column(Money)
    recommended_action: Mapped[str] = mapped_column(String(32))
    allowed_actions: Mapped[str] = mapped_column(Text)
    route_reason: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="[]")
    invoices_cited: Mapped[bool] = mapped_column(default=False)
    customer_mismatch: Mapped[bool] = mapped_column(default=False)
    explanation: Mapped[str] = mapped_column(Text)
    explainer_provider: Mapped[str] = mapped_column(String(32))
    explainer_model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_action: Mapped[str | None] = mapped_column(String(32))
    confirmed_by: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    remittance: Mapped[Remittance] = relationship(back_populates="proposal")
    candidates: Mapped[list[Candidate]] = relationship(
        back_populates="proposal", order_by="Candidate.rank"
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    invoice_number: Mapped[str] = mapped_column(String(32), index=True)
    customer_account: Mapped[str] = mapped_column(String(32))
    customer_name: Mapped[str] = mapped_column(String(160))
    open_amount: Mapped[Decimal] = mapped_column(Money)
    score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[str] = mapped_column(Text)
    selected: Mapped[bool] = mapped_column(default=False)
    proposed_amount: Mapped[Decimal | None] = mapped_column(Money)
    line_order: Mapped[int | None] = mapped_column(Integer)

    proposal: Mapped[Proposal] = relationship(back_populates="candidates")


class CashReceipt(Base):
    """Posted cash. Created only by clerk confirm."""

    __tablename__ = "cash_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    remittance_id: Mapped[int] = mapped_column(ForeignKey("remittances.id"), unique=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    payer_name: Mapped[str | None] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Money)
    applied_amount: Mapped[Decimal] = mapped_column(Money)
    unapplied_amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(24), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    posted_by: Mapped[str] = mapped_column(String(80))
    posted_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    remittance: Mapped[Remittance] = relationship(back_populates="receipt")
    customer: Mapped[Customer | None] = relationship()
    applications: Mapped[list[Application]] = relationship(back_populates="receipt")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("cash_receipts.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    open_before: Mapped[Decimal] = mapped_column(Money)
    open_after: Mapped[Decimal] = mapped_column(Money)
    posted_by: Mapped[str] = mapped_column(String(80))
    posted_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    receipt: Mapped[CashReceipt] = relationship(back_populates="applications")
    invoice: Mapped[Invoice] = relationship(back_populates="applications")
