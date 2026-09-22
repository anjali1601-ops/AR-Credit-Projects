"""Clerk workbench and JSON API. Port 47221.

Nothing in this module posts cash except the confirm routes, and those
only call service.confirm.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cash_application import __version__
from cash_application.db import get_db, init_db, session_scope
from cash_application.explain import get_explainer
from cash_application.ingest import detect_channel
from cash_application.models import Invoice, Remittance
from cash_application.money import format_money
from cash_application.service import (
    ConfirmError,
    confirm,
    customer_count,
    invoice_payload,
    list_proposals,
    load_seed,
    process_pending,
    process_remittance,
    proposal_payload,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
TEMPLATES.env.filters["money"] = lambda value: format_money(value)

KIND_LABEL = {
    "full": "Full",
    "short_pay": "Short pay",
    "overpay": "Overpay",
    "unapplied": "Unapplied",
}
CHANNEL_LABEL = {"lockbox": "Lockbox", "edi_820": "EDI 820", "email": "Email"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with session_scope() as session:
        if customer_count(session) == 0:
            load_seed(session)
        process_pending(session)
    yield


app = FastAPI(title="Cash Application Agent", version=__version__, lifespan=lifespan)


class SplitLine(BaseModel):
    invoice_number: str
    amount: str


class ConfirmIn(BaseModel):
    action: str
    clerk: str
    lines: list[SplitLine] = Field(default_factory=list)


class RemittanceIn(BaseModel):
    external_ref: str
    raw_text: str
    channel: str | None = None
    received_on: date | None = None


def _http(exc: ConfirmError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _decorate(payload: dict) -> dict:
    payload = dict(payload)
    payload["kind_label"] = KIND_LABEL.get(payload["kind"], payload["kind"])
    payload["channel_label"] = CHANNEL_LABEL.get(payload["channel"], payload["channel"])
    return payload


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_explainer().provider, "version": __version__}


@app.get("/")
def queue(request: Request, session: Session = Depends(get_db)):
    payloads = [_decorate(proposal_payload(proposal)) for proposal in list_proposals(session)]
    return TEMPLATES.TemplateResponse(
        request=request,
        name="queue.html",
        context={
            "ready": [item for item in payloads if item["queue"] == "ready" and item["status"] != "posted"],
            "exceptions": [item for item in payloads if item["queue"] == "exception" and item["status"] != "posted"],
            "posted": [item for item in payloads if item["status"] == "posted"],
            "provider": get_explainer().provider,
        },
    )


@app.get("/remittances/{external_ref}")
def detail(external_ref: str, request: Request, session: Session = Depends(get_db)):
    payload = _find(session, external_ref)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "item": _decorate(payload),
            "error": request.query_params.get("error"),
            "provider": payload["explainer_provider"],
        },
    )


@app.post("/remittances/{external_ref}/confirm")
async def confirm_form(external_ref: str, request: Request, session: Session = Depends(get_db)):
    form = await request.form()
    action = str(form.get("action") or "")
    clerk = str(form.get("clerk") or "")
    lines = []
    for key, value in form.multi_items():
        if not key.startswith("amount_"):
            continue
        text = str(value).strip()
        if not text:
            continue
        lines.append({"invoice_number": key.removeprefix("amount_"), "amount": text})
    try:
        confirm(session, external_ref, action, clerk, lines if action == "split" else None)
    except ConfirmError as exc:
        return RedirectResponse(f"/remittances/{external_ref}?error={quote(exc.detail)}", status_code=303)
    return RedirectResponse(f"/remittances/{external_ref}", status_code=303)


@app.get("/api/remittances")
def api_remittances(session: Session = Depends(get_db)) -> list[dict]:
    return [_decorate(proposal_payload(proposal)) for proposal in list_proposals(session)]


@app.get("/api/remittances/{external_ref}")
def api_remittance(external_ref: str, session: Session = Depends(get_db)) -> dict:
    return _decorate(_find(session, external_ref))


@app.post("/api/remittances")
def api_create(body: RemittanceIn, session: Session = Depends(get_db)) -> dict:
    existing = session.scalar(select(Remittance).where(Remittance.external_ref == body.external_ref))
    if existing is not None:
        raise HTTPException(status_code=409, detail="A remittance with that reference already exists.")
    channel = body.channel or detect_channel(body.raw_text)
    if channel not in {"lockbox", "edi_820", "email"}:
        raise HTTPException(status_code=422, detail="channel must be lockbox, edi_820, or email")
    remittance = Remittance(
        external_ref=body.external_ref,
        channel=channel,
        received_on=body.received_on or date.today(),
        raw_text=body.raw_text,
        status="pending",
    )
    session.add(remittance)
    session.flush()
    proposal = process_remittance(session, remittance, get_explainer())
    if proposal is None:
        raise HTTPException(status_code=422, detail=remittance.error or "Could not read the remittance.")
    return _decorate(proposal_payload(proposal))


@app.get("/api/exceptions")
def api_exceptions(session: Session = Depends(get_db)) -> list[dict]:
    payloads = [_decorate(proposal_payload(proposal)) for proposal in list_proposals(session)]
    return [item for item in payloads if item["queue"] == "exception" and item["status"] != "posted"]


@app.post("/api/remittances/{external_ref}/confirm")
def api_confirm(external_ref: str, body: ConfirmIn, session: Session = Depends(get_db)) -> dict:
    try:
        confirm(
            session,
            external_ref,
            body.action,
            body.clerk,
            [line.model_dump() for line in body.lines],
        )
    except ConfirmError as exc:
        raise _http(exc) from exc
    return _decorate(_find(session, external_ref))


@app.get("/api/invoices")
def api_invoices(session: Session = Depends(get_db)) -> list[dict]:
    invoices = session.scalars(select(Invoice).order_by(Invoice.invoice_number)).all()
    return [invoice_payload(invoice) for invoice in invoices]


@app.get("/api/outcomes")
def api_outcomes(session: Session = Depends(get_db)) -> dict:
    from cash_application.seed import PRIMARY_REFS

    payloads = {item["external_ref"]: item for item in (_decorate(proposal_payload(p)) for p in list_proposals(session))}
    rows = []
    for ref in PRIMARY_REFS:
        item = payloads[ref]
        rows.append(
            {
                "external_ref": ref,
                "kind": item["kind"],
                "line_count": item["line_count"],
                "queue": item["queue"],
                "unapplied_cash": item["unapplied_cash"],
                "short_fall": item["short_fall"],
                "confidence": item["confidence"],
                "recommended_action": item["recommended_action"],
            }
        )
    signatures = [
        (row["kind"], row["line_count"], row["queue"], row["unapplied_cash"] != "0.00", row["short_fall"] != "0.00")
        for row in rows
    ]
    return {"distinct": len(set(signatures)) == len(rows), "outcomes": rows}


def _find(session: Session, external_ref: str) -> dict:
    for proposal in list_proposals(session):
        if proposal.remittance.external_ref == external_ref:
            return proposal_payload(proposal)
    raise HTTPException(status_code=404, detail=f"No proposal for {external_ref}.")
