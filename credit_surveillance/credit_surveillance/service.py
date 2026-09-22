"""Draft periodic reviews, then post them through the authority matrix."""

import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from credit_surveillance.authority import ANALYST_ROLES, APPROVER_ROLE, evaluate_authority
from credit_surveillance.db import (
    apply_posted_review,
    get_account,
    get_review,
    insert_review,
    list_accounts,
    list_invoices,
    list_open_orders,
    list_promises,
    list_reviews,
    set_requested_limit,
    supersede_open_reviews,
)
from credit_surveillance.errors import (
    AccountNotFound,
    ReviewNotFound,
    ReviewTransitionError,
)
from credit_surveillance.exposure import AS_OF, compute_exposure
from credit_surveillance.formatting import money
from credit_surveillance.models import Account, ExposureFacts, Review
from credit_surveillance.narrator import Narrator, build_narrator, compose_memo
from credit_surveillance.policy import decide

def run_surveillance(
    conn: sqlite3.Connection,
    *,
    account_id: str | None = None,
    as_of=AS_OF,
    narrator: Narrator | None = None,
) -> list[Review]:
    """Draft a memo for every account, or for one account when an id is given."""
    narrator = narrator or build_narrator()
    if account_id is None:
        accounts = list_accounts(conn)
    else:
        account = get_account(conn, account_id)
        if account is None:
            raise AccountNotFound(f"No account {account_id}.")
        accounts = [account]
    return [_draft(conn, account, as_of, narrator) for account in accounts]


def post_decision(
    conn: sqlite3.Connection,
    review_id: str,
    *,
    actor_name: str,
    actor_role: str,
) -> Review:
    """Post a recommendation that is inside analyst authority."""
    review = _require_open_review(review_id, conn)
    authority = _recheck(review)
    if authority.requires_approver:
        raise ReviewTransitionError(authority.reason, 403)
    if actor_role not in ANALYST_ROLES:
        raise ReviewTransitionError(
            "Posting requires the analyst or credit_manager role.",
            403,
        )
    _commit(conn, review, posted_by=actor_name, approver_name=None, approver_role=None)
    posted = get_review(conn, review_id)
    assert posted is not None
    return posted


def approve_decision(
    conn: sqlite3.Connection,
    review_id: str,
    *,
    approver_name: str,
    approver_role: str,
) -> Review:
    """Post a gated recommendation under a named credit manager."""
    name = approver_name.strip()
    if not name:
        raise ReviewTransitionError("A named approver is required.", 422)
    review = _require_pending(review_id, conn)
    if approver_role != APPROVER_ROLE:
        raise ReviewTransitionError(
            "A named credit manager must approve a limit increase or a "
            "suspension above the analyst threshold.",
            403,
        )
    authority = _recheck(review)
    if not authority.requires_approver:
        raise ReviewTransitionError(
            "This review is inside analyst authority. Post it instead of approving it.",
            409,
        )
    _commit(conn, review, posted_by=name, approver_name=name, approver_role=approver_role)
    posted = get_review(conn, review_id)
    assert posted is not None
    return posted


def request_limit(
    conn: sqlite3.Connection,
    account_id: str,
    requested_limit: Decimal,
) -> Account:
    """Record a request for a higher limit. Surveillance decides whether to advance it."""
    account = get_account(conn, account_id)
    if account is None:
        raise AccountNotFound(f"No account {account_id}.")
    if requested_limit <= account.credit_limit:
        raise ReviewTransitionError(
            "Requested limit must exceed "
            f"credit_limit={money(account.credit_limit)}.",
            422,
        )
    set_requested_limit(conn, account_id, requested_limit)
    updated = get_account(conn, account_id)
    assert updated is not None
    return updated


def portfolio_snapshot(conn: sqlite3.Connection, as_of=AS_OF) -> dict:
    """Portfolio totals computed from the same exposure function as the memos."""
    accounts = list_accounts(conn)
    total_limit = Decimal("0")
    total_exposure = Decimal("0")
    total_past_due = Decimal("0")
    suspended = 0
    for account in accounts:
        facts = account_facts(conn, account, as_of)
        total_limit += account.credit_limit
        total_exposure += facts.exposure
        total_past_due += facts.past_due_ar
        if account.status == "suspended":
            suspended += 1
    pending = sum(
        1 for review in list_reviews(conn) if review.status == "pending_approval"
    )
    return {
        "as_of": as_of.isoformat(),
        "account_count": len(accounts),
        "open_accounts": len(accounts) - suspended,
        "suspended_accounts": suspended,
        "total_credit_limit": money(total_limit),
        "total_exposure": money(total_exposure),
        "total_past_due": money(total_past_due),
        "pending_approval": pending,
    }


def present_facts(facts: ExposureFacts) -> dict[str, str]:
    return {
        "credit_limit": money(facts.credit_limit),
        "accounts_receivable": money(facts.accounts_receivable),
        "current_ar": money(facts.current_ar),
        "past_due_ar": money(facts.past_due_ar),
        "open_orders": money(facts.open_orders),
        "exposure": money(facts.exposure),
        "over_limit_amount": money(facts.over_limit_amount),
        "utilization": f"{facts.utilization:.6f}",
        "past_due_ratio": f"{facts.past_due_ratio:.6f}",
        "avg_days_to_pay_baseline": f"{facts.avg_days_to_pay_baseline:.2f}",
        "avg_days_to_pay_recent": f"{facts.avg_days_to_pay_recent:.2f}",
        "payment_drift_days": f"{facts.payment_drift_days:.2f}",
        "late_payment_rate": f"{facts.late_payment_rate:.6f}",
        "broken_promise_count": str(facts.broken_promise_count),
        "broken_promise_amount": money(facts.broken_promise_amount),
        "max_days_past_due": str(facts.max_days_past_due),
        "terms_days": str(facts.terms_days),
    }


def _draft(conn, account: Account, as_of, narrator: Narrator) -> Review:
    facts = account_facts(conn, account, as_of)
    recommendation = decide(facts, account.requested_limit)
    authority = evaluate_authority(
        action=recommendation.action,
        current_limit=recommendation.current_limit,
        proposed_limit=recommendation.proposed_limit,
        exposure=facts.exposure,
    )
    request = _narrative_request(account, facts, recommendation)
    prose = narrator.narrate(request)
    narrative = compose_memo(prose, request, authority.reason)
    supersede_open_reviews(conn, account.id)
    review = Review(
        id=f"RV-{uuid.uuid4().hex[:12]}",
        account_id=account.id,
        account_name=account.name,
        as_of=facts.as_of,
        action=recommendation.action,
        rule_codes=recommendation.rule_codes,
        current_limit=recommendation.current_limit,
        proposed_limit=recommendation.proposed_limit,
        exposure=facts.exposure,
        cited_figures=dict(recommendation.cited_figures),
        conditions=recommendation.conditions,
        narrative=narrative,
        signals=facts.signals,
        authority=authority,
        status="pending_approval" if authority.requires_approver else "ready",
        created_at=_now(),
        posted_at=None,
        posted_by=None,
        approver_name=None,
        approver_role=None,
    )
    insert_review(conn, review)
    return review


def account_facts(conn, account: Account, as_of=AS_OF) -> ExposureFacts:
    orders = list_open_orders(conn, account.id)
    return compute_exposure(
        credit_limit=account.credit_limit,
        terms_days=account.terms_days,
        invoices=list_invoices(conn, account.id),
        promises=list_promises(conn, account.id),
        open_order_amounts=[order.amount for order in orders],
        as_of=as_of,
    )


def _narrative_request(account, facts, recommendation):
    from credit_surveillance.models import NarrativeRequest

    return NarrativeRequest(
        account_name=account.name,
        account_id=account.id,
        as_of=facts.as_of,
        action=recommendation.action,
        rule_codes=recommendation.rule_codes,
        headline=recommendation.headline,
        cited_figures=dict(recommendation.cited_figures),
        conditions=recommendation.conditions,
        signals=facts.signals,
    )


def _require_open_review(review_id: str, conn) -> Review:
    review = get_review(conn, review_id)
    if review is None:
        raise ReviewNotFound(f"No review {review_id}.")
    if review.status == "posted":
        raise ReviewTransitionError("This review is already posted.", 409)
    if review.status == "superseded":
        raise ReviewTransitionError(
            "This review was superseded by a later surveillance run.",
            409,
        )
    if review.status != "ready":
        authority = _recheck(review)
        raise ReviewTransitionError(authority.reason, 403)
    return review


def _require_pending(review_id: str, conn) -> Review:
    review = get_review(conn, review_id)
    if review is None:
        raise ReviewNotFound(f"No review {review_id}.")
    if review.status == "posted":
        raise ReviewTransitionError("This review is already posted.", 409)
    if review.status == "superseded":
        raise ReviewTransitionError(
            "This review was superseded by a later surveillance run.",
            409,
        )
    if review.status != "pending_approval":
        raise ReviewTransitionError("This review is not awaiting approval.", 409)
    return review


def _recheck(review: Review):
    return evaluate_authority(
        action=review.action,
        current_limit=review.current_limit,
        proposed_limit=review.proposed_limit,
        exposure=review.exposure,
    )


def _commit(conn, review: Review, *, posted_by: str, approver_name, approver_role) -> None:
    apply_posted_review(
        conn,
        review,
        posted_by=posted_by,
        approver_name=approver_name,
        approver_role=approver_role,
        posted_at=_now(),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
