"""FastAPI service: inbox simulation, case list/detail, one-click approvals."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from dra.api.schemas import (
    ActionResult,
    CaseDetail,
    CaseSummary,
    ClauseHit,
    InboxMessageOut,
    NewEmail,
    SupervisorAction,
)
from dra.db.models import CreditMemo, Customer, DisputeCase, InboxMessage
from dra.db.session import get_session, init_db, session_scope
from dra.inbox.simulator import deliver
from dra.rag.contracts import retrieve_clauses
from dra.settings import PACKAGE_ROOT, get_settings
from dra.workflow import ApprovalError, CaseNotFoundError, DisputeOrchestrator

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "api" / "templates"))


def get_orchestrator() -> DisputeOrchestrator:
    return DisputeOrchestrator()


async def _poller(app: FastAPI) -> None:
    """The reactive loop: new mail is picked up without anyone asking."""
    settings = get_settings()
    orchestrator = DisputeOrchestrator()
    while True:
        try:
            processed = await asyncio.to_thread(orchestrator.poll_inbox, 5)
            if processed:
                logger.info("inbox poller handled %d message(s)", len(processed))
                app.state.last_poll = processed
        except Exception:  # noqa: BLE001 - the loop must survive a bad message
            logger.exception("inbox poll failed")
        await asyncio.sleep(settings.inbox_poll_interval_seconds)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    init_db()
    app.state.last_poll = []
    task: asyncio.Task | None = None
    if settings.inbox_poll_enabled:
        task = asyncio.create_task(_poller(app))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="AR Dispute Resolution & Negotiation Agent",
    description=(
        "Ingestion, Auditor and Negotiation agents working a short-payment dispute "
        "from inbound email to a supervisor's one-click approval."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# serialization helpers
# --------------------------------------------------------------------------


def _summary(session: Session, case: DisputeCase) -> dict[str, Any]:
    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    return {
        "id": case.id,
        "case_number": case.case_number,
        "state": case.state,
        "owner_agent": case.owner_agent,
        "decision": case.decision,
        "decision_confidence": case.decision_confidence,
        "customer_name": customer.name if customer else None,
        "customer_code": customer.code if customer else None,
        "invoice_number": case.invoice_number,
        "reason_code": case.reason_code,
        "disputed_amount": float(case.disputed_amount) if case.disputed_amount else None,
        "approved_credit_amount": (
            float(case.approved_credit_amount) if case.approved_credit_amount else None
        ),
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def _detail(session: Session, case: DisputeCase) -> dict[str, Any]:
    evidence = json.loads(case.evidence_json or "{}")
    memo = session.scalar(select(CreditMemo).where(CreditMemo.case_id == case.id))
    base = get_settings().base_url
    return {
        **_summary(session, case),
        "reason_text": case.reason_text,
        "decision_rationale": case.decision_rationale,
        "cited_clause_ref": case.cited_clause_ref,
        "cited_clause_text": case.cited_clause_text,
        "resolution": case.resolution,
        "resolved_by": case.resolved_by,
        "evidence": evidence.get("evidence", []),
        "checks": evidence.get("checks", []),
        "retrieved_clauses": evidence.get("retrieved_clauses", []),
        "facts": evidence.get("facts", {}),
        "events": [
            {
                "seq": e.seq,
                "event_type": e.event_type,
                "from_agent": e.from_agent,
                "to_agent": e.to_agent,
                "from_state": e.from_state,
                "to_state": e.to_state,
                "summary": e.summary,
                "created_at": e.created_at,
            }
            for e in case.events
        ],
        "drafts": [
            {
                "id": d.id,
                "kind": d.kind,
                "recipient": d.recipient,
                "subject": d.subject,
                "body": d.body,
                "status": d.status,
                "created_at": d.created_at,
            }
            for d in case.drafts
        ],
        "queries": [
            {
                "question": q.question,
                "generated_sql": q.generated_sql,
                "params": json.loads(q.params_json or "{}"),
                "status": q.status,
                "row_count": q.row_count,
                "duration_ms": round(q.duration_ms, 2),
            }
            for q in case.queries
        ],
        "credit_memo": (
            {
                "memo_number": memo.memo_number,
                "amount": float(memo.amount),
                "status": memo.status,
                "reason_code": memo.reason_code,
                "issued_at": memo.issued_at,
                "issued_by": memo.issued_by,
            }
            if memo
            else None
        ),
        "approval_links": (
            {
                "approve_url": f"{base}/approve/{case.approval_token}",
                "reject_url": f"{base}/reject/{case.approval_token}",
            }
            if case.state == "awaiting_approval"
            else {}
        ),
    }


def _load_case(session: Session, case_id: int) -> DisputeCase:
    case = session.get(DisputeCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no dispute case {case_id}")
    return case


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------


@app.get("/health", tags=["service"])
def health() -> dict[str, Any]:
    settings = get_settings()
    with session_scope() as session:
        cases = session.scalars(select(DisputeCase)).all()
        unread = session.scalars(
            select(InboxMessage).where(InboxMessage.status == "unread")
        ).all()
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "vector_store": settings.vector_store,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "cases": len(cases),
        "unread_messages": len(unread),
        "inbox_poller": settings.inbox_poll_enabled,
    }


@app.get("/api/inbox", response_model=list[InboxMessageOut], tags=["inbox"])
def list_inbox(
    status: str | None = Query(default=None, description="unread, processed, skipped, failed"),
    session: Session = Depends(get_session),
) -> list[InboxMessage]:
    stmt = select(InboxMessage).order_by(InboxMessage.received_at.desc())
    if status:
        stmt = stmt.where(InboxMessage.status == status)
    return list(session.scalars(stmt).all())


@app.post("/api/inbox/messages", tags=["inbox"], status_code=201)
def post_email(
    email: NewEmail,
    orchestrator: DisputeOrchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    """Drop an email into the simulated inbox (optionally running it straight away)."""
    with session_scope() as session:
        message = deliver(
            session,
            sender=email.sender,
            subject=email.subject,
            body=email.body,
            sender_name=email.sender_name,
            recipient=email.recipient,
        )
        message_id = message.message_id
    if not email.process:
        return {"message_id": message_id, "status": "unread"}
    return orchestrator.process_message(message_id)


@app.post("/api/inbox/poll", tags=["inbox"])
def poll_inbox(
    limit: int = 10,
    orchestrator: DisputeOrchestrator = Depends(get_orchestrator),
) -> list[dict[str, Any]]:
    """Process every unread message. The background poller calls the same code."""
    return orchestrator.poll_inbox(limit=limit)


@app.get("/api/cases", response_model=list[CaseSummary], tags=["cases"])
def list_cases(
    state: str | None = None,
    decision: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(DisputeCase).order_by(DisputeCase.id.desc())
    if state:
        stmt = stmt.where(DisputeCase.state == state)
    if decision:
        stmt = stmt.where(DisputeCase.decision == decision)
    return [_summary(session, c) for c in session.scalars(stmt).all()]


@app.get("/api/cases/{case_id}", response_model=CaseDetail, tags=["cases"])
def get_case(case_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return _detail(session, _load_case(session, case_id))


@app.post("/api/cases/{case_id}/approve", response_model=ActionResult, tags=["supervisor"])
def approve_case(
    case_id: int,
    action: SupervisorAction,
    orchestrator: DisputeOrchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    try:
        return orchestrator.approve(case_id=case_id, actor=action.actor, note=action.note)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/cases/{case_id}/reject", response_model=ActionResult, tags=["supervisor"])
def reject_case(
    case_id: int,
    action: SupervisorAction,
    orchestrator: DisputeOrchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    try:
        return orchestrator.reject(case_id=case_id, actor=action.actor, note=action.note)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/contracts/search", response_model=list[ClauseHit], tags=["contracts"])
def search_contracts(
    q: str,
    customer_code: str | None = None,
    reason_code: str | None = None,
    k: int = 4,
) -> list[dict[str, Any]]:
    """The same RAG retrieval the Auditor uses, exposed for inspection."""
    hits = retrieve_clauses(q, customer_code=customer_code, reason_code=reason_code, k=k)
    return [
        {
            "contract_number": str(h.metadata.get("contract_number", "")),
            "clause_ref": h.clause_ref,
            "clause_title": h.clause_title,
            "text": h.text,
            "source_pdf": str(h.metadata.get("source_pdf", "")),
            "score": round(float(h.score), 4),
        }
        for h in hits
    ]


# --------------------------------------------------------------------------
# One-click supervisor links (what the approval email points at)
# --------------------------------------------------------------------------


def _action_page(request: Request, title: str, tone: str, message: str, case_id: int | None):
    return templates.TemplateResponse(
        request,
        "action.html",
        {"title": title, "tone": tone, "message": message, "case_id": case_id},
    )


@app.get("/approve/{token}", response_class=HTMLResponse, tags=["supervisor"])
def approve_by_token(
    request: Request,
    token: str,
    actor: str = "supervisor",
    orchestrator: DisputeOrchestrator = Depends(get_orchestrator),
):  # noqa: ANN201
    try:
        result = orchestrator.approve(token=token, actor=actor)
    except CaseNotFoundError:
        return _action_page(
            request, "Link not recognised", "warn",
            "That approval link does not match any dispute case.", None,
        )
    except ApprovalError as exc:
        return _action_page(request, "Already decided", "warn", str(exc), None)
    return _action_page(
        request, "Approved", "ok", result["summary"], result["case_id"]
    )


@app.get("/reject/{token}", response_class=HTMLResponse, tags=["supervisor"])
def reject_by_token(
    request: Request,
    token: str,
    actor: str = "supervisor",
    note: str = "",
    orchestrator: DisputeOrchestrator = Depends(get_orchestrator),
):  # noqa: ANN201
    try:
        result = orchestrator.reject(token=token, actor=actor, note=note)
    except CaseNotFoundError:
        return _action_page(
            request, "Link not recognised", "warn",
            "That rejection link does not match any dispute case.", None,
        )
    except ApprovalError as exc:
        return _action_page(request, "Already decided", "warn", str(exc), None)
    return _action_page(request, "Declined", "warn", result["summary"], result["case_id"])


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request, session: Session = Depends(get_session)):  # noqa: ANN201
    cases = [
        _summary(session, c)
        for c in session.scalars(select(DisputeCase).order_by(DisputeCase.id.desc())).all()
    ]
    messages = session.scalars(
        select(InboxMessage).order_by(InboxMessage.received_at.desc())
    ).all()
    settings = get_settings()
    open_credit = sum(
        c["approved_credit_amount"] or 0
        for c in cases
        if c["decision"] == "valid" and c["state"] == "awaiting_approval"
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "cases": cases,
            "messages": messages,
            "settings": settings,
            "open_credit": open_credit,
            "awaiting": sum(1 for c in cases if c["state"] == "awaiting_approval"),
            "disputed_total": sum(c["disputed_amount"] or 0 for c in cases),
        },
    )


@app.get("/cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
def case_page(request: Request, case_id: int, session: Session = Depends(get_session)):  # noqa: ANN201
    case = _detail(session, _load_case(session, case_id))
    return templates.TemplateResponse(request, "case.html", {"case": case})
