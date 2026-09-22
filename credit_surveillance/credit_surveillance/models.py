"""Records passed between the engine, the narrator, and the store."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Invoice:
    id: str
    account_id: str
    invoice_date: date
    due_date: date
    amount: Decimal
    paid_date: date | None


@dataclass(frozen=True)
class PromiseToPay:
    id: str
    account_id: str
    amount: Decimal
    promised_date: date
    status: str


@dataclass(frozen=True)
class OpenOrder:
    id: str
    account_id: str
    amount: Decimal
    description: str


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    segment: str
    terms_days: int
    credit_limit: Decimal
    status: str
    conditions: tuple[str, ...]
    analyst_note: str
    requested_limit: Decimal | None


@dataclass(frozen=True)
class ExposureFacts:
    as_of: date
    credit_limit: Decimal
    accounts_receivable: Decimal
    current_ar: Decimal
    past_due_ar: Decimal
    open_orders: Decimal
    exposure: Decimal
    over_limit_amount: Decimal
    utilization: Decimal
    past_due_ratio: Decimal
    avg_days_to_pay_recent: Decimal
    avg_days_to_pay_baseline: Decimal
    payment_drift_days: Decimal
    terms_days: int
    late_payment_rate: Decimal
    invoices_paid_recent: int
    invoices_late_recent: int
    broken_promise_count: int
    broken_promise_amount: Decimal
    max_days_past_due: int
    signals: tuple[str, ...]


@dataclass(frozen=True)
class Recommendation:
    action: str
    rule_codes: tuple[str, ...]
    current_limit: Decimal
    proposed_limit: Decimal
    cited_figures: dict[str, str]
    conditions: tuple[str, ...]
    headline: str


@dataclass(frozen=True)
class AuthorityDecision:
    can_post: bool
    requires_approver: bool
    code: str
    reason: str
    required_role: str | None


@dataclass(frozen=True)
class NarrativeRequest:
    account_name: str
    account_id: str
    as_of: date
    action: str
    rule_codes: tuple[str, ...]
    headline: str
    cited_figures: dict[str, str]
    conditions: tuple[str, ...]
    signals: tuple[str, ...]


@dataclass(frozen=True)
class Review:
    id: str
    account_id: str
    account_name: str
    as_of: date
    action: str
    rule_codes: tuple[str, ...]
    current_limit: Decimal
    proposed_limit: Decimal
    exposure: Decimal
    cited_figures: dict[str, str]
    conditions: tuple[str, ...]
    narrative: str
    signals: tuple[str, ...]
    authority: AuthorityDecision
    status: str
    created_at: str
    posted_at: str | None
    posted_by: str | None
    approver_name: str | None
    approver_role: str | None
