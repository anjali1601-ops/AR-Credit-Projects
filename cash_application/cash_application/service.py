"""Pipeline and the only ledger writer.

process_remittance runs ingestion, the matcher, the exception agent, and
the explainer. confirm is the only function that posts a cash receipt or
moves an invoice balance.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cash_application.exceptions import route
from cash_application.explain import ExplainFacts, Explanation, get_explainer
from cash_application.ingest import IngestError, parse_remittance
from cash_application.matcher import REASON_LABELS, CustomerView, InvoiceView, propose
from cash_application.models import (
    Application,
    Candidate,
    CashReceipt,
    Customer,
    Invoice,
    Proposal,
    Remittance,
    utcnow,
)
from cash_application.money import ZERO, money
from cash_application.seed import CUSTOMERS, INVOICES, REMITTANCES

CLERK_NAME = re.compile(r"[A-Za-z][A-Za-z .'\-]{1,79}")
CHANNEL_METHOD = {"lockbox": "check", "edi_820": "ach", "email": "ach"}


class ConfirmError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def load_seed(session: Session) -> None:
    for item in CUSTOMERS:
        session.add(
            Customer(
                account_number=item.account_number,
                name=item.name,
                aliases=json.dumps(list(item.aliases)),
                city=item.city,
                state=item.state,
                payment_terms=item.payment_terms,
            )
        )
    session.flush()
    by_account = {row.account_number: row for row in session.scalars(select(Customer))}
    for item in INVOICES:
        open_amount = ZERO if item.status == "paid" else money(item.amount)
        session.add(
            Invoice(
                invoice_number=item.invoice_number,
                customer_id=by_account[item.account_number].id,
                po_number=item.po_number,
                issue_date=item.issue_date,
                due_date=item.due_date,
                original_amount=money(item.amount),
                open_amount=open_amount,
                status=item.status,
                description=item.description,
            )
        )
    for item in REMITTANCES:
        session.add(
            Remittance(
                external_ref=item.external_ref,
                channel=item.channel,
                received_on=item.received_on,
                raw_text=item.raw_text,
                status="pending",
            )
        )
    session.flush()


def reset_and_seed(session: Session) -> None:
    """Caller drops tables first. This fills an empty schema."""
    load_seed(session)


def _customer_views(customers: list[Customer]) -> list[CustomerView]:
    views = []
    for customer in customers:
        aliases = tuple(json.loads(customer.aliases or "[]"))
        views.append(CustomerView(customer.account_number, customer.name, aliases))
    return views


def _invoice_views(invoices: list[Invoice]) -> list[InvoiceView]:
    return [
        InvoiceView(
            invoice_number=invoice.invoice_number,
            customer_account=invoice.customer.account_number,
            customer_name=invoice.customer.name,
            po_number=invoice.po_number,
            open_amount=invoice.open_amount,
            status=invoice.status,
        )
        for invoice in invoices
    ]


def process_remittance(session: Session, remittance: Remittance, explainer) -> Proposal | None:
    if remittance.proposal is not None:
        return remittance.proposal
    try:
        extraction = parse_remittance(remittance.channel, remittance.raw_text)
    except IngestError as exc:
        remittance.status = "error"
        remittance.error = str(exc)
        return None

    remittance.payer_name = extraction.payer_name
    remittance.payer_account = extraction.payer_account
    remittance.amount = extraction.amount
    remittance.payment_reference = extraction.payment_reference
    remittance.invoice_numbers = json.dumps(list(extraction.invoice_numbers))
    remittance.reference_values = json.dumps(list(extraction.references))
    remittance.line_amounts = json.dumps({key: f"{value}" for key, value in extraction.line_amounts.items()})
    remittance.deduction_note = extraction.deduction_note
    remittance.warnings = json.dumps(list(extraction.warnings))

    customers = list(session.scalars(select(Customer)))
    invoices = list(session.scalars(select(Invoice)))
    match = propose(extraction, _invoice_views(invoices), _customer_views(customers))
    decision = route(match)
    notes = tuple(match.notes) + tuple(extraction.warnings)
    facts = ExplainFacts(
        channel=remittance.channel,
        external_ref=remittance.external_ref,
        payer_name=extraction.payer_name,
        payer_account=extraction.payer_account,
        payment_reference=extraction.payment_reference,
        amount=f"{match.payment_amount}",
        kind=match.kind,
        confidence=f"{match.confidence:.2f}",
        recommended_action=decision.recommended_action,
        queue=decision.queue,
        applied_amount=f"{match.applied_amount}",
        unapplied_cash=f"{match.unapplied_cash}",
        short_fall=f"{match.short_fall}",
        deduction_note=extraction.deduction_note,
        invoices_cited=match.invoices_cited,
        lines=tuple(
            {
                "invoice_number": line.invoice_number,
                "customer_name": line.customer_name,
                "customer_account": line.customer_account,
                "open_amount": f"{line.open_amount}",
                "apply_amount": f"{line.apply_amount}",
            }
            for line in match.lines
        ),
        notes=notes,
    )
    explanation: Explanation = explainer.explain(facts)
    proposal = Proposal(
        remittance=remittance,
        kind=match.kind,
        confidence=f"{match.confidence:.2f}",
        queue=decision.queue,
        status=decision.queue,
        customer_account=match.customer_account,
        customer_name=match.customer_name,
        payment_amount=match.payment_amount,
        applied_amount=match.applied_amount,
        unapplied_cash=match.unapplied_cash,
        short_fall=match.short_fall,
        recommended_action=decision.recommended_action,
        allowed_actions=json.dumps(list(decision.allowed_actions)),
        route_reason=decision.reason,
        notes=json.dumps(list(notes)),
        invoices_cited=match.invoices_cited,
        customer_mismatch=match.customer_mismatch,
        explanation=explanation.text,
        explainer_provider=explanation.provider,
        explainer_model=explanation.model,
    )
    session.add(proposal)
    session.flush()
    line_order = {line.invoice_number: index for index, line in enumerate(match.lines, start=1)}
    for rank, candidate in enumerate(match.candidates, start=1):
        session.add(
            Candidate(
                proposal=proposal,
                rank=rank,
                invoice_number=candidate.invoice_number,
                customer_account=candidate.customer_account,
                customer_name=candidate.customer_name,
                open_amount=candidate.open_amount,
                score=candidate.score,
                reasons=json.dumps(list(candidate.reasons)),
                selected=candidate.selected,
                proposed_amount=candidate.proposed_amount,
                line_order=line_order.get(candidate.invoice_number),
            )
        )
    remittance.status = "processed"
    session.flush()
    return proposal


def process_pending(session: Session, explainer=None) -> int:
    explainer = explainer or get_explainer()
    pending = list(session.scalars(select(Remittance).where(Remittance.status == "pending").order_by(Remittance.id)))
    count = 0
    for remittance in pending:
        process_remittance(session, remittance, explainer)
        count += 1
    return count


def _allocations(proposal: Proposal, action: str, lines: list[dict] | None) -> list[tuple[str, Decimal]]:
    if action == "leave_unapplied":
        return []
    if action == "apply":
        selected = [
            candidate
            for candidate in proposal.candidates
            if candidate.selected and candidate.proposed_amount is not None and candidate.proposed_amount > 0
        ]
        selected.sort(key=lambda candidate: candidate.line_order or 0)
        if not selected:
            raise ConfirmError(
                422,
                "Nothing was selected to apply. Split the cash onto invoices you choose, or leave it unapplied.",
            )
        return [(candidate.invoice_number, candidate.proposed_amount) for candidate in selected]
    if action == "split":
        if not lines:
            raise ConfirmError(422, "Split needs at least one invoice amount.")
        allocations: list[tuple[str, Decimal]] = []
        seen: set[str] = set()
        for line in lines:
            number = str(line.get("invoice_number", "")).strip().upper()
            raw_amount = line.get("amount", "")
            try:
                amount = money(raw_amount)
            except ValueError as exc:
                raise ConfirmError(422, f"Amount for {number or 'a line'} is not valid money.") from exc
            if amount <= 0:
                continue
            if not number:
                raise ConfirmError(422, "Split line is missing an invoice number.")
            if number in seen:
                raise ConfirmError(422, f"Invoice {number} is listed twice.")
            seen.add(number)
            allocations.append((number, amount))
        if not allocations:
            raise ConfirmError(422, "Split needs a positive amount on at least one invoice.")
        return allocations
    raise ConfirmError(422, f"Unknown action {action!r}.")


def confirm(
    session: Session,
    external_ref: str,
    action: str,
    clerk: str,
    lines: list[dict] | None = None,
) -> CashReceipt:
    """Post the remittance. Refuses to run twice, and refuses a blank clerk."""
    clerk_name = (clerk or "").strip()
    if not CLERK_NAME.fullmatch(clerk_name):
        raise ConfirmError(
            422,
            "Enter the clerk's name. There is no login; the name is stored on the receipt.",
        )
    remittance = session.scalar(select(Remittance).where(Remittance.external_ref == external_ref))
    if remittance is None or remittance.proposal is None:
        raise ConfirmError(404, f"No proposal for {external_ref}.")
    proposal = remittance.proposal
    if proposal.status == "posted" or remittance.receipt is not None:
        raise ConfirmError(409, "This remittance is already posted.")
    allowed = json.loads(proposal.allowed_actions)
    if action not in allowed:
        raise ConfirmError(422, f"{action} is not available on this proposal. Choose one of: {', '.join(allowed)}.")

    allocations = _allocations(proposal, action, lines)
    total = money(sum((amount for _, amount in allocations), ZERO))
    if total > proposal.payment_amount:
        raise ConfirmError(422, "Allocated amount exceeds the remittance.")

    applied_rows: list[tuple[Invoice, Decimal]] = []
    for number, amount in allocations:
        invoice = session.scalar(select(Invoice).where(Invoice.invoice_number == number))
        if invoice is None:
            raise ConfirmError(422, f"Invoice {number} is not on the ledger.")
        if invoice.status == "paid" or invoice.open_amount <= 0:
            raise ConfirmError(409, f"Invoice {number} is already paid.")
        if amount > invoice.open_amount:
            raise ConfirmError(409, f"Amount on {number} exceeds the open balance of {invoice.open_amount}.")
        applied_rows.append((invoice, amount))

    customer_id = None
    if applied_rows:
        customer_ids = {invoice.customer_id for invoice, _ in applied_rows}
        if len(customer_ids) == 1:
            customer_id = applied_rows[0][0].customer_id
    elif proposal.customer_account:
        customer = session.scalar(select(Customer).where(Customer.account_number == proposal.customer_account))
        if customer is not None:
            customer_id = customer.id

    unapplied = money(proposal.payment_amount - total)
    if total == 0:
        receipt_status = "unapplied"
    elif unapplied == 0:
        receipt_status = "applied"
    else:
        receipt_status = "partially_applied"

    receipt = CashReceipt(
        remittance=remittance,
        customer_id=customer_id,
        payer_name=remittance.payer_name,
        amount=proposal.payment_amount,
        applied_amount=total,
        unapplied_amount=unapplied,
        status=receipt_status,
        channel=remittance.channel,
        method=CHANNEL_METHOD.get(remittance.channel, remittance.channel),
        reference=remittance.payment_reference,
        action=action,
        posted_by=clerk_name,
        posted_at=utcnow(),
    )
    session.add(receipt)
    for invoice, amount in applied_rows:
        before = invoice.open_amount
        after = money(before - amount)
        invoice.open_amount = after
        invoice.status = "paid" if after == 0 else "partial"
        application = Application(
            receipt=receipt,
            invoice=invoice,
            amount=amount,
            open_before=before,
            open_after=after,
            posted_by=clerk_name,
            posted_at=receipt.posted_at,
        )
        session.add(application)
        receipt.applications.append(application)
    proposal.status = "posted"
    proposal.confirmed_action = action
    proposal.confirmed_by = clerk_name
    proposal.confirmed_at = receipt.posted_at
    remittance.status = "posted"
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConfirmError(409, "This remittance is already posted.") from exc
    return receipt


def proposal_payload(proposal: Proposal) -> dict:
    remittance = proposal.remittance
    candidates = sorted(proposal.candidates, key=lambda candidate: candidate.rank)
    lines = [candidate for candidate in candidates if candidate.selected]
    lines.sort(key=lambda candidate: candidate.line_order or 0)
    receipt = remittance.receipt
    return {
        "id": proposal.id,
        "external_ref": remittance.external_ref,
        "channel": remittance.channel,
        "received_on": remittance.received_on.isoformat(),
        "payer_name": remittance.payer_name,
        "payer_account": remittance.payer_account,
        "payment_reference": remittance.payment_reference,
        "amount": f"{proposal.payment_amount}",
        "kind": proposal.kind,
        "confidence": proposal.confidence,
        "queue": proposal.queue,
        "status": proposal.status,
        "customer_account": proposal.customer_account,
        "customer_name": proposal.customer_name,
        "applied_amount": f"{proposal.applied_amount}",
        "unapplied_cash": f"{proposal.unapplied_cash}",
        "short_fall": f"{proposal.short_fall}",
        "recommended_action": proposal.recommended_action,
        "allowed_actions": json.loads(proposal.allowed_actions),
        "route_reason": proposal.route_reason,
        "notes": json.loads(proposal.notes or "[]"),
        "invoices_cited": proposal.invoices_cited,
        "customer_mismatch": proposal.customer_mismatch,
        "explanation": proposal.explanation,
        "explainer_provider": proposal.explainer_provider,
        "explainer_model": proposal.explainer_model,
        "line_count": len(lines),
        "lines": [_candidate_payload(candidate) for candidate in lines],
        "candidates": [_candidate_payload(candidate) for candidate in candidates],
        "raw_text": remittance.raw_text,
        "deduction_note": remittance.deduction_note,
        "confirmed_action": proposal.confirmed_action,
        "confirmed_by": proposal.confirmed_by,
        "receipt": _receipt_payload(receipt) if receipt is not None else None,
    }


def _candidate_payload(candidate: Candidate) -> dict:
    preview = None
    if candidate.proposed_amount is not None:
        preview = f"{money(candidate.open_amount - candidate.proposed_amount)}"
    return {
        "rank": candidate.rank,
        "invoice_number": candidate.invoice_number,
        "customer_account": candidate.customer_account,
        "customer_name": candidate.customer_name,
        "open_amount": f"{candidate.open_amount}",
        "score": candidate.score,
        "reasons": json.loads(candidate.reasons or "[]"),
        "reason_labels": [REASON_LABELS.get(code, code) for code in json.loads(candidate.reasons or "[]")],
        "selected": candidate.selected,
        "proposed_amount": None if candidate.proposed_amount is None else f"{candidate.proposed_amount}",
        "preview_open_after": preview,
        "line_order": candidate.line_order,
    }


def _receipt_payload(receipt: CashReceipt) -> dict:
    return {
        "id": receipt.id,
        "status": receipt.status,
        "amount": f"{receipt.amount}",
        "applied_amount": f"{receipt.applied_amount}",
        "unapplied_amount": f"{receipt.unapplied_amount}",
        "method": receipt.method,
        "reference": receipt.reference,
        "action": receipt.action,
        "posted_by": receipt.posted_by,
        "posted_at": receipt.posted_at.isoformat(timespec="seconds"),
        "applications": [
            {
                "invoice_number": application.invoice.invoice_number,
                "amount": f"{application.amount}",
                "open_before": f"{application.open_before}",
                "open_after": f"{application.open_after}",
            }
            for application in receipt.applications
        ],
    }


def invoice_payload(invoice: Invoice) -> dict:
    return {
        "invoice_number": invoice.invoice_number,
        "account_number": invoice.customer.account_number,
        "customer_name": invoice.customer.name,
        "po_number": invoice.po_number,
        "original_amount": f"{invoice.original_amount}",
        "open_amount": f"{invoice.open_amount}",
        "status": invoice.status,
        "due_date": invoice.due_date.isoformat(),
        "description": invoice.description,
    }


def list_proposals(session: Session) -> list[Proposal]:
    return list(session.scalars(select(Proposal).join(Remittance).order_by(Remittance.received_on, Remittance.external_ref)))


def customer_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Customer)) or 0)
