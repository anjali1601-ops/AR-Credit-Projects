"""The reactive, stateful workflow.

The orchestrator owns every write. Each stage commits before the next begins,
so a case is observable in the database at ``ingested``, ``audited``,
``drafted`` and ``awaiting_approval`` rather than appearing fully formed at the
end, and a crash mid-pipeline leaves a resumable case rather than a lost one.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dra.agents import AuditorAgent, IngestionAgent, NegotiationAgent
from dra.agents.schemas import AuditDecision, DisputeExtraction
from dra.db.models import (
    CaseDraft,
    CaseEvent,
    CreditMemo,
    Customer,
    DisputeCase,
    InboxMessage,
    Invoice,
    SqlAuditEntry,
)
from dra.db.session import session_scope
from dra.llm import LLMProvider, get_llm
from dra.settings import get_settings
from dra.workflow.states import (
    STATE_OWNER,
    Agent,
    CaseState,
    assert_transition,
)

logger = logging.getLogger(__name__)


class CaseNotFoundError(LookupError):
    pass


class ApprovalError(RuntimeError):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class DisputeOrchestrator:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm or get_llm()
        self.settings = get_settings()
        self.ingestion = IngestionAgent(self._llm)
        self.negotiation = NegotiationAgent(self._llm)

    # -- helpers ------------------------------------------------------------

    def _next_case_number(self, session: Session) -> str:
        year = dt.date.today().year
        count = session.scalar(select(func.count(DisputeCase.id))) or 0
        return f"CASE-{year}-{count + 1:04d}"

    def _next_memo_number(self, session: Session) -> str:
        year = dt.date.today().year
        count = session.scalar(select(func.count(CreditMemo.id))) or 0
        return f"CM-{year}-{count + 1:04d}"

    def _record(
        self,
        session: Session,
        case: DisputeCase,
        *,
        event_type: str,
        summary: str,
        to_state: CaseState | None = None,
        from_agent: Agent | None = None,
        to_agent: Agent | None = None,
        payload: dict[str, Any] | None = None,
    ) -> CaseEvent:
        from_state = CaseState(case.state)
        if to_state is not None and to_state != from_state:
            assert_transition(from_state, to_state)
        seq = (
            session.scalar(
                select(func.count(CaseEvent.id)).where(CaseEvent.case_id == case.id)
            )
            or 0
        ) + 1
        event = CaseEvent(
            case_id=case.id,
            seq=seq,
            event_type=event_type,
            from_agent=from_agent.value if from_agent else None,
            to_agent=to_agent.value if to_agent else None,
            from_state=from_state.value,
            to_state=(to_state or from_state).value,
            summary=summary,
            payload_json=json.dumps(payload, default=str) if payload else None,
        )
        session.add(event)
        if to_state is not None:
            case.state = to_state.value
            case.owner_agent = STATE_OWNER[to_state].value
        case.updated_at = _utcnow()
        return event

    def _links(self, case: DisputeCase) -> dict[str, str]:
        base = self.settings.base_url
        token = case.approval_token
        return {
            "approve_url": f"{base}/approve/{token}",
            "reject_url": f"{base}/reject/{token}",
            "case_url": f"{base}/cases/{case.id}",
        }

    def _case_view(self, session: Session, case: DisputeCase) -> dict[str, Any]:
        customer = session.get(Customer, case.customer_id) if case.customer_id else None
        invoice = session.get(Invoice, case.invoice_id) if case.invoice_id else None
        facts = json.loads(case.evidence_json or "{}").get("facts", {})
        return {
            "case_number": case.case_number,
            "invoice_number": case.invoice_number,
            "po_number": invoice.po_number if invoice else facts.get("po_number"),
            "disputed_amount": case.disputed_amount,
            "reason_code": case.reason_code,
            "currency": invoice.currency if invoice else "USD",
            "customer_name": customer.name if customer else facts.get("customer_name", ""),
            "customer_code": customer.code if customer else facts.get("customer_code", ""),
            "contact_name": customer.ar_contact_name if customer else None,
            "contact_email": customer.ar_contact_email if customer else case.source_message_id,
        }

    # -- stage 1: ingestion -------------------------------------------------

    def process_message(self, message_id: str) -> dict[str, Any]:
        """Run one inbound message all the way to a supervisor decision point."""
        with session_scope() as session:
            message = session.scalar(
                select(InboxMessage).where(InboxMessage.message_id == message_id)
            )
            if message is None:
                raise CaseNotFoundError(f"no inbox message {message_id}")
            if message.status != "unread":
                return {
                    "message_id": message_id,
                    "status": message.status,
                    "case_id": message.case_id,
                    "note": "already processed",
                }

            extraction = self.ingestion.extract(
                sender=message.sender, subject=message.subject, body=message.body
            )
            if not extraction.is_dispute:
                message.status = "skipped"
                message.processed_at = _utcnow()
                message.note = (
                    f"not a short-payment or dispute: {extraction.triage_note}"
                )
                return {
                    "message_id": message_id,
                    "status": "skipped",
                    "case_id": None,
                    "note": message.note,
                }

            case = self._open_case(session, message, extraction)
            case_id = case.id

        self.audit_case(case_id)
        self.draft_case(case_id)

        with session_scope() as session:
            case = session.get(DisputeCase, case_id)
            return {
                "message_id": message_id,
                "status": "processed",
                "case_id": case_id,
                "case_number": case.case_number,
                "state": case.state,
                "decision": case.decision,
            }

    def _open_case(
        self, session: Session, message: InboxMessage, extraction: DisputeExtraction
    ) -> DisputeCase:
        invoice = session.scalar(
            select(Invoice).where(Invoice.invoice_number == extraction.invoice_number)
        )
        customer = None
        if invoice is not None:
            customer = session.get(Customer, invoice.customer_id)
        if customer is None:
            domain = message.sender.split("@")[-1].lower()
            customer = session.scalar(
                select(Customer).where(func.lower(Customer.email_domain) == domain)
            )

        case = DisputeCase(
            case_number=self._next_case_number(session),
            state=CaseState.RECEIVED.value,
            owner_agent=Agent.INGESTION.value,
            customer_id=customer.id if customer else None,
            invoice_id=invoice.id if invoice else None,
            source_message_id=message.message_id,
            invoice_number=extraction.invoice_number,
            reason_code=extraction.reason_code,
            reason_text=extraction.reason_text,
            disputed_amount=extraction.disputed_amount,
            extraction_confidence=extraction.confidence,
            extraction_json=json.dumps(extraction.to_dict()),
            approval_token=secrets.token_urlsafe(18),
        )
        session.add(case)
        session.flush()

        message.status = "processed"
        message.processed_at = _utcnow()
        message.case_id = case.id
        message.note = f"opened {case.case_number}"

        self._record(
            session,
            case,
            event_type="case_opened",
            summary=(
                f"Email from {message.sender} classified as a "
                f"{extraction.reason_code.replace('_', ' ')} claim of "
                f"{extraction.disputed_amount} on invoice {extraction.invoice_number} "
                f"(confidence {extraction.confidence:.0%})."
            ),
            to_state=CaseState.INGESTED,
            from_agent=Agent.INGESTION,
            to_agent=Agent.AUDITOR,
            payload=extraction.to_dict(),
        )
        return case

    # -- stage 2: audit -----------------------------------------------------

    def audit_case(self, case_id: int) -> AuditDecision:
        with session_scope() as session:
            case = session.get(DisputeCase, case_id)
            if case is None:
                raise CaseNotFoundError(f"no case {case_id}")
            extraction = DisputeExtraction(
                **{
                    **json.loads(case.extraction_json or "{}"),
                    "disputed_amount": case.disputed_amount,
                }
            )
            claim_date = case.created_at.date() if case.created_at else dt.date.today()

            auditor = AuditorAgent(self._llm)
            decision = auditor.audit(extraction, claim_date=claim_date)

            for entry in auditor.tool.audit:
                session.add(
                    SqlAuditEntry(
                        case_id=case.id,
                        agent=Agent.AUDITOR.value,
                        question=entry.question,
                        generated_sql=entry.sql,
                        params_json=json.dumps(entry.params, default=str),
                        status=entry.status,
                        row_count=len(entry.rows),
                        duration_ms=entry.duration_ms,
                        error=entry.error,
                    )
                )

            case.decision = decision.decision
            case.decision_confidence = decision.confidence
            case.decision_rationale = decision.rationale
            case.approved_credit_amount = decision.recommended_credit or None
            case.evidence_json = json.dumps(decision.to_dict(), default=str)
            if decision.citation:
                case.cited_clause_ref = (
                    f"{decision.citation.contract_number} clause "
                    f"{decision.citation.clause_ref}"
                )
                case.cited_clause_text = decision.citation.text

            self._record(
                session,
                case,
                event_type="audit_completed",
                summary=(
                    f"Auditor ruled {decision.decision.upper()} at "
                    f"{decision.confidence:.0%} confidence after "
                    f"{len(decision.queries)} read-only ERP queries and "
                    f"{len(decision.retrieved_clauses)} retrieved clauses. "
                    f"{decision.rationale}"
                ),
                to_state=CaseState.AUDITED,
                from_agent=Agent.AUDITOR,
                to_agent=Agent.NEGOTIATION,
                payload={
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "recommended_credit": float(decision.recommended_credit),
                    "checks": [c.to_dict() for c in decision.checks],
                    "citation": decision.citation.to_dict() if decision.citation else None,
                },
            )
            return decision

    # -- stage 3: drafting + handoff to the supervisor ----------------------

    def draft_case(self, case_id: int) -> dict[str, Any]:
        with session_scope() as session:
            case = session.get(DisputeCase, case_id)
            if case is None:
                raise CaseNotFoundError(f"no case {case_id}")
            payload = json.loads(case.evidence_json or "{}")
            decision = _decision_from_dict(payload)
            view = self._case_view(session, case)

            memo_number = (
                self._next_memo_number(session) if decision.is_valid else None
            )
            output = self.negotiation.draft(
                view, decision, self._links(case), memo_number=memo_number
            )

            for draft in output.drafts:
                session.add(
                    CaseDraft(
                        case_id=case.id,
                        kind=draft.kind,
                        recipient=draft.recipient,
                        subject=draft.subject,
                        body=draft.body,
                    )
                )

            if decision.is_valid and case.customer_id and case.invoice_id:
                session.add(
                    CreditMemo(
                        case_id=case.id,
                        customer_id=case.customer_id,
                        invoice_id=case.invoice_id,
                        memo_number=memo_number,
                        amount=decision.recommended_credit,
                        reason_code=case.reason_code or "other",
                        narrative=decision.rationale,
                        status="draft",
                    )
                )

            self._record(
                session,
                case,
                event_type="drafts_created",
                summary=output.summary,
                to_state=CaseState.DRAFTED,
                from_agent=Agent.NEGOTIATION,
                to_agent=Agent.NEGOTIATION,
                payload={
                    "drafts": [d.kind for d in output.drafts],
                    "credit_memo": memo_number,
                },
            )

            limit = Decimal(str(self.settings.auto_approve_limit))
            auto = (
                decision.is_valid
                and limit > 0
                and decision.recommended_credit <= limit
            )
            if auto:
                self._record(
                    session,
                    case,
                    event_type="auto_approved",
                    summary=(
                        f"Credit of {decision.recommended_credit} is at or below the "
                        f"{limit} auto-approval limit; no supervisor needed."
                    ),
                    to_state=CaseState.AWAITING_APPROVAL,
                    from_agent=Agent.NEGOTIATION,
                    to_agent=Agent.SUPERVISOR,
                )
                session.flush()
                self._settle(session, case, actor="auto-approval policy", note="under limit")
            else:
                self._record(
                    session,
                    case,
                    event_type="approval_requested",
                    summary=(
                        "Handed to the AR supervisor for a one-click decision at "
                        f"{self._links(case)['approve_url']}"
                    ),
                    to_state=CaseState.AWAITING_APPROVAL,
                    from_agent=Agent.NEGOTIATION,
                    to_agent=Agent.SUPERVISOR,
                    payload=self._links(case),
                )
            return {
                "case_id": case.id,
                "state": case.state,
                "drafts": [d.kind for d in output.drafts],
                "credit_memo": memo_number,
            }

    # -- stage 4: supervisor decision ---------------------------------------

    def _resolve_case(self, session: Session, *, case_id: int | None, token: str | None) -> DisputeCase:
        if token:
            case = session.scalar(
                select(DisputeCase).where(DisputeCase.approval_token == token)
            )
        else:
            case = session.get(DisputeCase, case_id)
        if case is None:
            raise CaseNotFoundError("no matching dispute case")
        return case

    def approve(
        self,
        *,
        case_id: int | None = None,
        token: str | None = None,
        actor: str = "supervisor",
        note: str = "",
    ) -> dict[str, Any]:
        with session_scope() as session:
            case = self._resolve_case(session, case_id=case_id, token=token)
            if case.state != CaseState.AWAITING_APPROVAL.value:
                raise ApprovalError(
                    f"case {case.case_number} is {case.state}, not awaiting approval"
                )
            result = self._settle(session, case, actor=actor, note=note)
            return result

    def _settle(
        self, session: Session, case: DisputeCase, *, actor: str, note: str
    ) -> dict[str, Any]:
        """Apply an approval. This is the only place a credit memo is issued."""
        memo = session.scalar(
            select(CreditMemo).where(CreditMemo.case_id == case.id)
        )
        drafts = session.scalars(
            select(CaseDraft).where(CaseDraft.case_id == case.id)
        ).all()

        if case.decision == "valid" and memo is not None:
            memo.status = "issued"
            memo.issued_at = _utcnow()
            memo.issued_by = actor
            invoice = session.get(Invoice, case.invoice_id)
            if invoice is not None:
                invoice.status = "settled_with_credit"
            resolution = f"credit_memo_issued:{memo.memo_number}"
            summary = (
                f"{actor} approved. Credit memo {memo.memo_number} for "
                f"${memo.amount:,.2f} issued against invoice {case.invoice_number} and "
                f"the customer confirmation released."
            )
            session.add(
                CaseDraft(
                    case_id=case.id,
                    kind="customer_email",
                    recipient=next(
                        (d.recipient for d in drafts if d.kind == "credit_memo"), ""
                    ),
                    subject=(
                        f"Credit memo {memo.memo_number} issued against invoice "
                        f"{case.invoice_number}"
                    ),
                    body=_confirmation_body(case, memo),
                    status="sent",
                    sent_at=_utcnow(),
                )
            )
        else:
            resolution = "rebuttal_sent" if case.decision == "invalid" else "information_requested"
            summary = (
                f"{actor} released the drafted "
                + ("rebuttal" if case.decision == "invalid" else "information request")
                + f" to the customer for {case.invoice_number}."
            )

        for draft in drafts:
            if draft.status != "draft":
                continue
            draft.status = "issued" if draft.kind == "credit_memo" else "sent"
            draft.sent_at = _utcnow()

        case.resolution = resolution
        case.resolved_by = actor
        case.resolution_note = note
        self._record(
            session,
            case,
            event_type="approved",
            summary=summary,
            to_state=CaseState.RESOLVED,
            from_agent=Agent.SUPERVISOR,
            to_agent=Agent.SYSTEM,
            payload={"resolution": resolution, "actor": actor, "note": note},
        )
        return {
            "case_id": case.id,
            "case_number": case.case_number,
            "state": case.state,
            "resolution": resolution,
            "credit_memo": memo.memo_number if memo and case.decision == "valid" else None,
            "summary": summary,
        }

    def reject(
        self,
        *,
        case_id: int | None = None,
        token: str | None = None,
        actor: str = "supervisor",
        note: str = "",
    ) -> dict[str, Any]:
        with session_scope() as session:
            case = self._resolve_case(session, case_id=case_id, token=token)
            if case.state != CaseState.AWAITING_APPROVAL.value:
                raise ApprovalError(
                    f"case {case.case_number} is {case.state}, not awaiting approval"
                )
            memo = session.scalar(select(CreditMemo).where(CreditMemo.case_id == case.id))
            if memo is not None:
                memo.status = "voided"
            case.resolution = "declined_by_supervisor"
            case.resolved_by = actor
            case.resolution_note = note
            self._record(
                session,
                case,
                event_type="rejected",
                summary=(
                    f"{actor} declined the recommendation"
                    + (f": {note}" if note else "")
                    + ". Nothing was sent to the customer and no credit was posted."
                ),
                to_state=CaseState.REJECTED,
                from_agent=Agent.SUPERVISOR,
                to_agent=Agent.SYSTEM,
                payload={"actor": actor, "note": note},
            )
            return {
                "case_id": case.id,
                "case_number": case.case_number,
                "state": case.state,
                "resolution": case.resolution,
                "summary": "Recommendation declined; the case is closed unsent.",
            }

    # -- the reactive tick --------------------------------------------------

    def poll_inbox(self, limit: int = 10) -> list[dict[str, Any]]:
        with session_scope() as session:
            pending = session.scalars(
                select(InboxMessage.message_id)
                .where(InboxMessage.status == "unread")
                .order_by(InboxMessage.received_at, InboxMessage.id)
                .limit(limit)
            ).all()
        results = []
        for message_id in pending:
            try:
                results.append(self.process_message(message_id))
            except Exception as exc:  # noqa: BLE001 - one bad email must not stop the loop
                logger.exception("failed to process %s", message_id)
                with session_scope() as session:
                    message = session.scalar(
                        select(InboxMessage).where(InboxMessage.message_id == message_id)
                    )
                    if message is not None:
                        message.status = "failed"
                        message.note = f"{type(exc).__name__}: {exc}"
                results.append(
                    {"message_id": message_id, "status": "failed", "error": str(exc)}
                )
        return results


def _decision_from_dict(payload: dict[str, Any]) -> AuditDecision:
    from dra.agents.schemas import ClauseCitation, PolicyCheck

    citation = payload.get("citation")
    return AuditDecision(
        decision=payload.get("decision", "needs_more_info"),
        confidence=float(payload.get("confidence") or 0.0),
        rationale=payload.get("rationale", ""),
        recommended_credit=Decimal(str(payload.get("recommended_credit") or 0)),
        evidence=list(payload.get("evidence") or []),
        checks=[PolicyCheck(**c) for c in payload.get("checks") or []],
        citation=ClauseCitation(**citation) if citation else None,
        retrieved_clauses=[ClauseCitation(**c) for c in payload.get("retrieved_clauses") or []],
        facts=payload.get("facts") or {},
        queries=payload.get("queries") or [],
        missing_information=list(payload.get("missing_information") or []),
    )


def _confirmation_body(case: DisputeCase, memo: CreditMemo) -> str:
    return f"""Hello,

Thank you for your patience while we reviewed the deduction you took against invoice \
{case.invoice_number}.

We agree with your claim. Credit memo {memo.memo_number} for {memo.amount:,.2f} has been \
issued against invoice {case.invoice_number}, which clears the deduction in full — no \
further payment is needed on this item and nothing further is required from you.

{case.decision_rationale}

Our reference for this review is {case.case_number}, and the contractual basis is \
{case.cited_clause_ref or 'the governing agreement'}.

Thanks again for flagging it.

Rowan Ellis
Deductions & Disputes, Accounts Receivable
Acme Supply Co.
"""
