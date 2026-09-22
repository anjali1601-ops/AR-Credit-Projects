"""SQLite case file. Confirmation cannot flip a row to sent."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deduction_workbench.models import CaseRecord
from deduction_workbench.seed import SEED_CASES

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    debit_memo TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    claimed_amount REAL NOT NULL,
    claim_date TEXT NOT NULL,
    backup_email TEXT NOT NULL,
    debit_memo_text TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    agreement_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    case_id TEXT PRIMARY KEY REFERENCES cases(id),
    reason_code TEXT NOT NULL,
    reason_label TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    citation_id TEXT NOT NULL,
    citation_heading TEXT NOT NULL,
    citation_text TEXT NOT NULL,
    agreement_id TEXT NOT NULL,
    retrieved_json TEXT NOT NULL,
    proposed_queue TEXT NOT NULL,
    queue TEXT NOT NULL,
    queue_label TEXT NOT NULL,
    organization TEXT NOT NULL,
    desk TEXT NOT NULL,
    rationale TEXT NOT NULL,
    narrative TEXT NOT NULL,
    llm_provider TEXT NOT NULL,
    auto_send INTEGER NOT NULL DEFAULT 0 CHECK (auto_send = 0),
    sent INTEGER NOT NULL DEFAULT 0 CHECK (sent = 0),
    overridden INTEGER NOT NULL DEFAULT 0,
    proposed_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT,
    confirmation_note TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def seed_cases(conn: sqlite3.Connection) -> int:
    inserted = 0
    now = _now()
    for raw in SEED_CASES:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO cases (
                id, customer_id, customer_name, debit_memo, invoice_number,
                claimed_amount, claim_date, backup_email, debit_memo_text,
                facts_json, agreement_ids, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
            """,
            (
                raw["id"],
                raw["customer_id"],
                raw["customer_name"],
                raw["debit_memo"],
                raw["invoice_number"],
                raw["claimed_amount"],
                raw["claim_date"],
                raw["backup_email"],
                raw["debit_memo_text"],
                json.dumps(raw["facts"], sort_keys=True),
                json.dumps(raw["agreement_ids"]),
                now,
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def list_case_records(conn: sqlite3.Connection, status: str | None = None) -> list[CaseRecord]:
    if status is None:
        rows = conn.execute("SELECT * FROM cases ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM cases WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [_case(row) for row in rows]


def get_case_record(conn: sqlite3.Connection, case_id: str) -> CaseRecord | None:
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return _case(row) if row else None


def save_decision(conn: sqlite3.Connection, case_id: str, decision: dict[str, Any]) -> None:
    conn.execute("DELETE FROM decisions WHERE case_id = ?", (case_id,))
    conn.execute(
        """
        INSERT INTO decisions (
            case_id, reason_code, reason_label, confidence, evidence_json,
            outcome, checks_json, citation_id, citation_heading, citation_text,
            agreement_id, retrieved_json, proposed_queue, queue, queue_label,
            organization, desk, rationale, narrative, llm_provider, auto_send,
            sent, overridden, proposed_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, 0,
            0, 0, ?
        )
        """,
        (
            case_id,
            decision["reason_code"],
            decision["reason_label"],
            decision["confidence"],
            json.dumps(decision["evidence"]),
            decision["outcome"],
            json.dumps(decision["checks"]),
            decision["citation_id"],
            decision["citation_heading"],
            decision["citation_text"],
            decision["agreement_id"],
            json.dumps(decision["retrieved"]),
            decision["queue"],
            decision["queue"],
            decision["queue_label"],
            decision["organization"],
            decision["desk"],
            decision["rationale"],
            decision["narrative"],
            decision["llm_provider"],
            decision["proposed_at"],
        ),
    )
    conn.execute(
        "UPDATE cases SET status = 'awaiting_confirmation' WHERE id = ?",
        (case_id,),
    )
    conn.commit()


def confirm_decision(
    conn: sqlite3.Connection,
    case_id: str,
    clerk_id: str,
    note: str,
    queue: str | None,
    queue_label: str | None,
    organization: str | None,
    desk: str | None,
) -> None:
    row = conn.execute("SELECT queue, proposed_queue FROM decisions WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        raise KeyError(case_id)
    overridden = 0
    if queue and queue != row["proposed_queue"]:
        overridden = 1
        conn.execute(
            """
            UPDATE decisions
            SET queue = ?, queue_label = ?, organization = ?, desk = ?,
                overridden = 1, confirmed_at = ?, confirmed_by = ?, confirmation_note = ?,
                sent = 0, auto_send = 0
            WHERE case_id = ?
            """,
            (queue, queue_label, organization, desk, _now(), clerk_id, note, case_id),
        )
    else:
        conn.execute(
            """
            UPDATE decisions
            SET overridden = 0, confirmed_at = ?, confirmed_by = ?, confirmation_note = ?,
                sent = 0, auto_send = 0
            WHERE case_id = ?
            """,
            (_now(), clerk_id, note, case_id),
        )
    conn.execute("UPDATE cases SET status = 'confirmed' WHERE id = ?", (case_id,))
    conn.commit()
    _ = overridden


def list_views(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*, d.reason_code, d.reason_label, d.outcome, d.queue, d.queue_label,
               d.organization, d.desk, d.citation_id, d.sent, d.auto_send,
               d.confirmed_by, d.proposed_queue
        FROM cases c
        LEFT JOIN decisions d ON d.case_id = c.id
        ORDER BY c.id
        """
    ).fetchall()
    return [_summary(row) for row in rows]


def get_view(conn: sqlite3.Connection, case_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT c.*, d.reason_code, d.reason_label, d.confidence, d.evidence_json,
               d.outcome, d.checks_json, d.citation_id, d.citation_heading,
               d.citation_text, d.agreement_id AS cited_agreement_id, d.retrieved_json,
               d.proposed_queue, d.queue, d.queue_label, d.organization, d.desk,
               d.rationale, d.narrative, d.llm_provider, d.auto_send, d.sent,
               d.overridden, d.proposed_at, d.confirmed_at, d.confirmed_by,
               d.confirmation_note
        FROM cases c
        LEFT JOIN decisions d ON d.case_id = c.id
        WHERE c.id = ?
        """,
        (case_id,),
    ).fetchone()
    if row is None:
        return None
    view = _summary(row)
    view.update(
        {
            "backup_email": row["backup_email"],
            "debit_memo_text": row["debit_memo_text"],
            "facts": json.loads(row["facts_json"]),
            "agreement_ids": json.loads(row["agreement_ids"]),
            "confidence": row["confidence"],
            "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else [],
            "checks": json.loads(row["checks_json"]) if row["checks_json"] else [],
            "citation_heading": row["citation_heading"],
            "citation_text": row["citation_text"],
            "cited_agreement_id": row["cited_agreement_id"],
            "retrieved": json.loads(row["retrieved_json"]) if row["retrieved_json"] else [],
            "proposed_queue": row["proposed_queue"],
            "rationale": row["rationale"],
            "narrative": row["narrative"],
            "llm_provider": row["llm_provider"],
            "overridden": bool(row["overridden"]) if row["overridden"] is not None else False,
            "proposed_at": row["proposed_at"],
            "confirmed_at": row["confirmed_at"],
            "confirmation_note": row["confirmation_note"],
        }
    )
    return view


def sent_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(sent), 0) AS n FROM decisions").fetchone()
    return int(row["n"])


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    sent = row["sent"] if "sent" in row.keys() else None
    return {
        "id": row["id"],
        "customer_id": row["customer_id"],
        "customer_name": row["customer_name"],
        "debit_memo": row["debit_memo"],
        "invoice_number": row["invoice_number"],
        "claimed_amount": row["claimed_amount"],
        "claim_date": row["claim_date"],
        "status": row["status"],
        "reason_code": row["reason_code"],
        "reason_label": row["reason_label"],
        "outcome": row["outcome"],
        "queue": row["queue"],
        "queue_label": row["queue_label"],
        "organization": row["organization"],
        "desk": row["desk"],
        "citation_id": row["citation_id"],
        "proposed_queue": row["proposed_queue"] if "proposed_queue" in row.keys() else row["queue"],
        "sent": bool(sent) if sent is not None else False,
        "auto_send": bool(row["auto_send"]) if row["auto_send"] is not None else False,
        "dispatched": False,
        "confirmed_by": row["confirmed_by"],
    }


def _case(row: sqlite3.Row) -> CaseRecord:
    return CaseRecord(
        id=row["id"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        debit_memo=row["debit_memo"],
        invoice_number=row["invoice_number"],
        claimed_amount=float(row["claimed_amount"]),
        claim_date=row["claim_date"],
        backup_email=row["backup_email"],
        debit_memo_text=row["debit_memo_text"],
        facts=json.loads(row["facts_json"]),
        agreement_ids=json.loads(row["agreement_ids"]),
        status=row["status"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
