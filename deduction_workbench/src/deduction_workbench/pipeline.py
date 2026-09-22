"""Classify, check, and route. The clerk is the only person who can confirm.

Confirmation writes the decision on the case. It does not email the customer,
post a credit, or release the deduction.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from deduction_workbench.agents.classifier import classify
from deduction_workbench.agents.policy import evaluate
from deduction_workbench.agents.router import QUEUES, decide_route, describe_queue
from deduction_workbench.db import (
    confirm_decision,
    connect,
    get_case_record,
    get_view,
    init_db,
    list_case_records,
    save_decision,
    seed_cases,
)
from deduction_workbench.llm.base import Narrator, get_narrator
from deduction_workbench.llm.offline import OfflineNarrator
from deduction_workbench.models import NarrativeBrief


class ConfirmError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def prepare(db_path: str | Path, narrator: Narrator | None = None) -> sqlite3.Connection:
    conn = connect(db_path)
    init_db(conn)
    seed_cases(conn)
    process_pending(conn, narrator or get_narrator())
    return conn


def process_pending(conn: sqlite3.Connection, narrator: Narrator | None = None) -> list[str]:
    voice = narrator or get_narrator()
    done: list[str] = []
    for case in list_case_records(conn, status="new"):
        process_case(conn, case.id, voice)
        done.append(case.id)
    return done


def process_case(conn: sqlite3.Connection, case_id: str, narrator: Narrator | None = None) -> dict:
    case = get_case_record(conn, case_id)
    if case is None:
        raise ConfirmError(404, f"No deduction {case_id}.")
    if case.status == "confirmed":
        raise ConfirmError(409, "This route is already confirmed and will not be rerun.")
    voice = narrator or get_narrator()
    classification = classify(case.backup_email, case.debit_memo_text)
    policy = evaluate(classification.reason_code, case)
    route = decide_route(classification.reason_code, policy)
    brief = NarrativeBrief(
        customer_name=case.customer_name,
        debit_memo=case.debit_memo,
        invoice_number=case.invoice_number,
        claimed_amount=case.claimed_amount,
        reason_code=classification.reason_code,
        reason_label=classification.reason_label,
        outcome=policy.outcome,
        queue=route.queue,
        queue_label=route.queue_label,
        citation_id=policy.citation_id,
        citation_text=policy.citation_text,
        checks=policy.checks,
    )
    try:
        narrative = voice.narrate(brief)
        provider_name = voice.name
    except Exception:
        narrative = OfflineNarrator().narrate(brief)
        provider_name = "offline"
    save_decision(
        conn,
        case.id,
        {
            "reason_code": classification.reason_code,
            "reason_label": classification.reason_label,
            "confidence": classification.confidence,
            "evidence": classification.evidence,
            "outcome": policy.outcome,
            "checks": [
                {"name": check.name, "passed": check.passed, "detail": check.detail}
                for check in policy.checks
            ],
            "citation_id": policy.citation_id,
            "citation_heading": policy.citation_heading,
            "citation_text": policy.citation_text,
            "agreement_id": policy.agreement_id,
            "retrieved": [
                {
                    "clause_id": hit.clause_id,
                    "agreement_id": hit.agreement_id,
                    "heading": hit.heading,
                    "kind": hit.kind,
                    "score": hit.score,
                    "excerpt": hit.excerpt,
                }
                for hit in policy.retrieved
            ],
            "queue": route.queue,
            "queue_label": route.queue_label,
            "organization": route.organization,
            "desk": route.desk,
            "rationale": route.rationale,
            "narrative": narrative,
            "llm_provider": provider_name,
            "proposed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
    )
    view = get_view(conn, case.id)
    if view is None:  # pragma: no cover - row was just written
        raise RuntimeError(f"Decision for {case.id} did not persist")
    return view


def confirm_case(
    conn: sqlite3.Connection,
    case_id: str,
    clerk_id: str,
    note: str = "",
    queue: str | None = None,
) -> dict:
    clerk = (clerk_id or "").strip()
    if not clerk:
        raise ConfirmError(422, "A clerk id is required.")
    if len(clerk) > 80:
        raise ConfirmError(422, "Clerk id is too long.")
    view = get_view(conn, case_id)
    if view is None:
        raise ConfirmError(404, f"No deduction {case_id}.")
    if view["status"] == "confirmed":
        raise ConfirmError(409, "A clerk already confirmed this route. Nothing was sent.")
    if view["status"] != "awaiting_confirmation" or not view.get("queue"):
        raise ConfirmError(409, "This deduction has not been routed yet.")
    chosen = (queue or "").strip() or view["proposed_queue"]
    if chosen not in QUEUES:
        raise ConfirmError(422, f"Unknown queue '{chosen}'.")
    label, organization, desk = describe_queue(chosen)
    confirm_decision(
        conn,
        case_id,
        clerk_id=clerk,
        note=(note or "").strip()[:500],
        queue=chosen,
        queue_label=label,
        organization=organization,
        desk=desk,
    )
    updated = get_view(conn, case_id)
    if updated is None:  # pragma: no cover
        raise RuntimeError(f"Confirmed case {case_id} disappeared")
    # The check constraint keeps sent at 0. Restate it for the caller.
    updated["sent"] = False
    updated["auto_send"] = False
    updated["dispatched"] = False
    return updated
