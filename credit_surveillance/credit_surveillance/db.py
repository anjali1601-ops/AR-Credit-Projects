"""SQLite portfolio store. Money is stored as decimal text."""

import json
import os
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from credit_surveillance.formatting import q_money
from credit_surveillance.models import (
    Account,
    AuthorityDecision,
    Invoice,
    OpenOrder,
    PromiseToPay,
    Review,
)

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "portfolio.db"


def resolve_db_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("CREDIT_SURVEILLANCE_DB")
    if env:
        return Path(env)
    return DEFAULT_DB


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            segment TEXT NOT NULL,
            terms_days INTEGER NOT NULL,
            credit_limit TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'suspended')),
            conditions_json TEXT NOT NULL,
            analyst_note TEXT NOT NULL,
            requested_limit TEXT
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            invoice_date TEXT NOT NULL,
            due_date TEXT NOT NULL,
            amount TEXT NOT NULL,
            paid_date TEXT
        );

        CREATE TABLE IF NOT EXISTS promises (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            amount TEXT NOT NULL,
            promised_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('kept', 'broken', 'open'))
        );

        CREATE TABLE IF NOT EXISTS open_orders (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            amount TEXT NOT NULL,
            description TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            as_of TEXT NOT NULL,
            action TEXT NOT NULL,
            rule_codes_json TEXT NOT NULL,
            current_limit TEXT NOT NULL,
            proposed_limit TEXT NOT NULL,
            exposure TEXT NOT NULL,
            cited_figures_json TEXT NOT NULL,
            conditions_json TEXT NOT NULL,
            narrative TEXT NOT NULL,
            signals_json TEXT NOT NULL,
            authority_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('ready', 'pending_approval', 'posted', 'superseded')
            ),
            created_at TEXT NOT NULL,
            posted_at TEXT,
            posted_by TEXT,
            approver_name TEXT,
            approver_role TEXT
        );
        """
    )


def clear_portfolio(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM reviews")
    conn.execute("DELETE FROM open_orders")
    conn.execute("DELETE FROM promises")
    conn.execute("DELETE FROM invoices")
    conn.execute("DELETE FROM accounts")


def insert_account(conn: sqlite3.Connection, account: Account) -> None:
    conn.execute(
        """
        INSERT INTO accounts (
            id, name, segment, terms_days, credit_limit, status,
            conditions_json, analyst_note, requested_limit
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account.id,
            account.name,
            account.segment,
            account.terms_days,
            _money(account.credit_limit),
            account.status,
            json.dumps(list(account.conditions)),
            account.analyst_note,
            _money(account.requested_limit) if account.requested_limit is not None else None,
        ),
    )


def insert_invoice(conn: sqlite3.Connection, invoice: Invoice) -> None:
    conn.execute(
        """
        INSERT INTO invoices (id, account_id, invoice_date, due_date, amount, paid_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            invoice.id,
            invoice.account_id,
            invoice.invoice_date.isoformat(),
            invoice.due_date.isoformat(),
            _money(invoice.amount),
            invoice.paid_date.isoformat() if invoice.paid_date else None,
        ),
    )


def insert_promise(conn: sqlite3.Connection, promise: PromiseToPay) -> None:
    conn.execute(
        """
        INSERT INTO promises (id, account_id, amount, promised_date, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            promise.id,
            promise.account_id,
            _money(promise.amount),
            promise.promised_date.isoformat(),
            promise.status,
        ),
    )


def insert_open_order(conn: sqlite3.Connection, order: OpenOrder) -> None:
    conn.execute(
        """
        INSERT INTO open_orders (id, account_id, amount, description)
        VALUES (?, ?, ?, ?)
        """,
        (order.id, order.account_id, _money(order.amount), order.description),
    )


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return [_account(row) for row in rows]


def get_account(conn: sqlite3.Connection, account_id: str) -> Account | None:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return _account(row) if row else None


def list_invoices(conn: sqlite3.Connection, account_id: str) -> list[Invoice]:
    rows = conn.execute(
        "SELECT * FROM invoices WHERE account_id = ? ORDER BY invoice_date, id",
        (account_id,),
    ).fetchall()
    return [
        Invoice(
            id=row["id"],
            account_id=row["account_id"],
            invoice_date=date.fromisoformat(row["invoice_date"]),
            due_date=date.fromisoformat(row["due_date"]),
            amount=Decimal(row["amount"]),
            paid_date=date.fromisoformat(row["paid_date"]) if row["paid_date"] else None,
        )
        for row in rows
    ]


def list_promises(conn: sqlite3.Connection, account_id: str) -> list[PromiseToPay]:
    rows = conn.execute(
        "SELECT * FROM promises WHERE account_id = ? ORDER BY promised_date, id",
        (account_id,),
    ).fetchall()
    return [
        PromiseToPay(
            id=row["id"],
            account_id=row["account_id"],
            amount=Decimal(row["amount"]),
            promised_date=date.fromisoformat(row["promised_date"]),
            status=row["status"],
        )
        for row in rows
    ]


def list_open_orders(conn: sqlite3.Connection, account_id: str) -> list[OpenOrder]:
    rows = conn.execute(
        "SELECT * FROM open_orders WHERE account_id = ? ORDER BY id",
        (account_id,),
    ).fetchall()
    return [
        OpenOrder(
            id=row["id"],
            account_id=row["account_id"],
            amount=Decimal(row["amount"]),
            description=row["description"],
        )
        for row in rows
    ]


def set_requested_limit(
    conn: sqlite3.Connection,
    account_id: str,
    requested_limit: Decimal,
) -> None:
    conn.execute(
        "UPDATE accounts SET requested_limit = ? WHERE id = ?",
        (_money(requested_limit), account_id),
    )


def supersede_open_reviews(conn: sqlite3.Connection, account_id: str) -> None:
    conn.execute(
        """
        UPDATE reviews
        SET status = 'superseded'
        WHERE account_id = ? AND status IN ('ready', 'pending_approval')
        """,
        (account_id,),
    )


def insert_review(conn: sqlite3.Connection, review: Review) -> None:
    authority = {
        "can_post": review.authority.can_post,
        "requires_approver": review.authority.requires_approver,
        "code": review.authority.code,
        "reason": review.authority.reason,
        "required_role": review.authority.required_role,
    }
    conn.execute(
        """
        INSERT INTO reviews (
            id, account_id, as_of, action, rule_codes_json, current_limit,
            proposed_limit, exposure, cited_figures_json, conditions_json,
            narrative, signals_json, authority_json, status, created_at,
            posted_at, posted_by, approver_name, approver_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review.id,
            review.account_id,
            review.as_of.isoformat(),
            review.action,
            json.dumps(list(review.rule_codes)),
            _money(review.current_limit),
            _money(review.proposed_limit),
            _money(review.exposure),
            json.dumps(review.cited_figures),
            json.dumps(list(review.conditions)),
            review.narrative,
            json.dumps(list(review.signals)),
            json.dumps(authority),
            review.status,
            review.created_at,
            review.posted_at,
            review.posted_by,
            review.approver_name,
            review.approver_role,
        ),
    )


def list_reviews(
    conn: sqlite3.Connection,
    *,
    include_superseded: bool = False,
) -> list[Review]:
    sql = """
        SELECT reviews.*, accounts.name AS account_name
        FROM reviews
        JOIN accounts ON accounts.id = reviews.account_id
    """
    if not include_superseded:
        sql += " WHERE reviews.status != 'superseded'"
    sql += " ORDER BY reviews.created_at DESC, reviews.rowid DESC"
    return [_review(row) for row in conn.execute(sql).fetchall()]


def get_review(conn: sqlite3.Connection, review_id: str) -> Review | None:
    row = conn.execute(
        """
        SELECT reviews.*, accounts.name AS account_name
        FROM reviews
        JOIN accounts ON accounts.id = reviews.account_id
        WHERE reviews.id = ?
        """,
        (review_id,),
    ).fetchone()
    return _review(row) if row else None


def apply_posted_review(
    conn: sqlite3.Connection,
    review: Review,
    *,
    posted_by: str,
    approver_name: str | None,
    approver_role: str | None,
    posted_at: str,
) -> None:
    """Post the decision onto the account and the review in one transaction."""
    if review.action == "reduce" or review.action == "increase":
        credit_limit = _money(review.proposed_limit)
    else:
        credit_limit = _money(review.current_limit)
    status = "suspended" if review.action == "suspend" else "open"
    conditions = list(review.conditions) if review.action == "conditions" else []
    conn.execute(
        """
        UPDATE accounts
        SET credit_limit = ?, status = ?, conditions_json = ?, requested_limit = NULL
        WHERE id = ?
        """,
        (credit_limit, status, json.dumps(conditions), review.account_id),
    )
    conn.execute(
        """
        UPDATE reviews
        SET status = 'posted', posted_at = ?, posted_by = ?,
            approver_name = ?, approver_role = ?
        WHERE id = ?
        """,
        (posted_at, posted_by, approver_name, approver_role, review.id),
    )


def _money(value: Decimal) -> str:
    return f"{q_money(value):.2f}"


def _account(row: sqlite3.Row) -> Account:
    requested = row["requested_limit"]
    return Account(
        id=row["id"],
        name=row["name"],
        segment=row["segment"],
        terms_days=row["terms_days"],
        credit_limit=Decimal(row["credit_limit"]),
        status=row["status"],
        conditions=tuple(json.loads(row["conditions_json"])),
        analyst_note=row["analyst_note"],
        requested_limit=Decimal(requested) if requested else None,
    )


def _review(row: sqlite3.Row) -> Review:
    authority = json.loads(row["authority_json"])
    return Review(
        id=row["id"],
        account_id=row["account_id"],
        account_name=row["account_name"],
        as_of=date.fromisoformat(row["as_of"]),
        action=row["action"],
        rule_codes=tuple(json.loads(row["rule_codes_json"])),
        current_limit=Decimal(row["current_limit"]),
        proposed_limit=Decimal(row["proposed_limit"]),
        exposure=Decimal(row["exposure"]),
        cited_figures=json.loads(row["cited_figures_json"]),
        conditions=tuple(json.loads(row["conditions_json"])),
        narrative=row["narrative"],
        signals=tuple(json.loads(row["signals_json"])),
        authority=AuthorityDecision(
            can_post=bool(authority["can_post"]),
            requires_approver=bool(authority["requires_approver"]),
            code=authority["code"],
            reason=authority["reason"],
            required_role=authority["required_role"],
        ),
        status=row["status"],
        created_at=row["created_at"],
        posted_at=row["posted_at"],
        posted_by=row["posted_by"],
        approver_name=row["approver_name"],
        approver_role=row["approver_role"],
    )
