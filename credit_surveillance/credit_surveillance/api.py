"""HTTP desk for periodic credit surveillance."""

from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator

from credit_surveillance.db import (
    connect,
    get_account,
    get_review,
    list_accounts,
    list_invoices,
    list_open_orders,
    list_promises,
    list_reviews,
    resolve_db_path,
)
from credit_surveillance.errors import (
    AccountNotFound,
    NarratorError,
    ReviewNotFound,
    ReviewTransitionError,
    SurveillanceError,
)
from credit_surveillance.exposure import AS_OF
from credit_surveillance.formatting import money
from credit_surveillance.seed import seed_database
from credit_surveillance.service import (
    approve_decision,
    portfolio_snapshot,
    post_decision,
    present_facts,
    request_limit,
    run_surveillance,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ActorRequest(BaseModel):
    actor_name: str
    actor_role: str = "analyst"

    @field_validator("actor_name")
    @classmethod
    def name_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("actor_name is required")
        return cleaned


class ApproveRequest(BaseModel):
    approver_name: str
    approver_role: str = "credit_manager"

    @field_validator("approver_name")
    @classmethod
    def name_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("approver_name is required")
        return cleaned


class LimitRequestBody(BaseModel):
    requested_limit: str

    def amount(self) -> Decimal:
        try:
            value = Decimal(self.requested_limit)
        except InvalidOperation as exc:
            raise ValueError("requested_limit must be a decimal string") from exc
        if value <= 0:
            raise ValueError("requested_limit must be positive")
        return value


def create_app(db_path: Path | None = None, seed_on_startup: bool = True) -> FastAPI:
    path = db_path or resolve_db_path()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(app.state.db_path)
        try:
            if app.state.seed_on_startup and not list_accounts(conn):
                seed_database(conn)
                conn.commit()
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="Credit Portfolio Surveillance",
        version="0.1.0",
        description=(
            "Periodic review of an existing B2B credit portfolio. "
            "Exposure, payment drift, and broken promises are computed in code. "
            "The memo only narrates those figures. A limit increase, or a "
            "suspension above the analyst threshold, stays pending until a "
            "named credit manager approves it."
        ),
        lifespan=lifespan,
    )
    app.state.db_path = path
    app.state.seed_on_startup = seed_on_startup

    @app.exception_handler(ReviewTransitionError)
    async def transition_error(_request: Request, exc: ReviewTransitionError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(NarratorError)
    async def narrator_error(_request: Request, exc: NarratorError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(AccountNotFound)
    async def missing_account(_request: Request, exc: AccountNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ReviewNotFound)
    async def missing_review(_request: Request, exc: ReviewNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/")
    def desk():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "credit-surveillance", "as_of": AS_OF.isoformat()}

    @app.get("/portfolio")
    def portfolio(request: Request):
        with _conn(request) as conn:
            return portfolio_snapshot(conn)

    @app.post("/portfolio/seed")
    def seed(request: Request):
        with _conn(request) as conn:
            ids = seed_database(conn)
            conn.commit()
            return {"seeded": len(ids), "account_ids": ids}

    @app.get("/accounts")
    def accounts(request: Request):
        with _conn(request) as conn:
            return {"accounts": [_account_payload(conn, account.id) for account in list_accounts(conn)]}

    @app.get("/accounts/{account_id}")
    def account_detail(account_id: str, request: Request):
        with _conn(request) as conn:
            payload = _account_payload(conn, account_id, include_history=True)
            if payload is None:
                raise HTTPException(status_code=404, detail=f"No account {account_id}.")
            return payload

    @app.post("/accounts/{account_id}/limit-request")
    def limit_request(account_id: str, body: LimitRequestBody, request: Request):
        try:
            amount = body.amount()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with _conn(request) as conn:
            try:
                account = request_limit(conn, account_id, amount)
            except SurveillanceError:
                conn.rollback()
                raise
            conn.commit()
            return _account_summary(account)

    @app.post("/reviews/run")
    def run_reviews(
        request: Request,
        account_id: str | None = Query(default=None),
    ):
        with _conn(request) as conn:
            try:
                reviews = run_surveillance(conn, account_id=account_id)
            except SurveillanceError:
                conn.rollback()
                raise
            conn.commit()
            return {"reviews": [_review_payload(review) for review in reviews]}

    @app.get("/reviews")
    def reviews(
        request: Request,
        include_superseded: bool = Query(default=False),
    ):
        with _conn(request) as conn:
            rows = list_reviews(conn, include_superseded=include_superseded)
            return {"reviews": [_review_payload(review) for review in rows]}

    @app.get("/reviews/{review_id}")
    def review_detail(review_id: str, request: Request):
        with _conn(request) as conn:
            review = get_review(conn, review_id)
            if review is None:
                raise HTTPException(status_code=404, detail=f"No review {review_id}.")
            return _review_payload(review)

    @app.post("/reviews/{review_id}/post")
    def post_review(review_id: str, body: ActorRequest, request: Request):
        with _conn(request) as conn:
            try:
                review = post_decision(
                    conn,
                    review_id,
                    actor_name=body.actor_name,
                    actor_role=body.actor_role,
                )
            except SurveillanceError:
                conn.rollback()
                raise
            conn.commit()
            return _review_payload(review)

    @app.post("/reviews/{review_id}/approve")
    def approve_review(review_id: str, body: ApproveRequest, request: Request):
        with _conn(request) as conn:
            try:
                review = approve_decision(
                    conn,
                    review_id,
                    approver_name=body.approver_name,
                    approver_role=body.approver_role,
                )
            except SurveillanceError:
                conn.rollback()
                raise
            conn.commit()
            return _review_payload(review)

    return app


class _Conn:
    def __init__(self, request: Request) -> None:
        self.request = request
        self.conn = None

    def __enter__(self):
        self.conn = connect(self.request.app.state.db_path)
        return self.conn

    def __exit__(self, exc_type, exc, _tb):
        if self.conn is not None:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.close()
        return False


def _conn(request: Request) -> _Conn:
    return _Conn(request)


def _account_summary(account) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "segment": account.segment,
        "terms_days": account.terms_days,
        "credit_limit": money(account.credit_limit),
        "status": account.status,
        "conditions": list(account.conditions),
        "analyst_note": account.analyst_note,
        "requested_limit": money(account.requested_limit) if account.requested_limit else None,
    }


def _account_payload(conn, account_id: str, include_history: bool = False) -> dict | None:
    account = get_account(conn, account_id)
    if account is None:
        return None
    from credit_surveillance.service import account_facts

    facts = account_facts(conn, account, AS_OF)
    payload = _account_summary(account)
    payload["signals"] = list(facts.signals)
    payload["facts"] = present_facts(facts)
    if include_history:
        payload["invoices"] = [
            {
                "id": invoice.id,
                "invoice_date": invoice.invoice_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "amount": money(invoice.amount),
                "paid_date": invoice.paid_date.isoformat() if invoice.paid_date else None,
            }
            for invoice in list_invoices(conn, account_id)
        ]
        payload["promises"] = [
            {
                "id": promise.id,
                "amount": money(promise.amount),
                "promised_date": promise.promised_date.isoformat(),
                "status": promise.status,
            }
            for promise in list_promises(conn, account_id)
        ]
        payload["open_orders"] = [
            {
                "id": order.id,
                "amount": money(order.amount),
                "description": order.description,
            }
            for order in list_open_orders(conn, account_id)
        ]
    return payload


def _review_payload(review) -> dict:
    return {
        "id": review.id,
        "account_id": review.account_id,
        "account_name": review.account_name,
        "as_of": review.as_of.isoformat(),
        "action": review.action,
        "rule_codes": list(review.rule_codes),
        "current_limit": money(review.current_limit),
        "proposed_limit": money(review.proposed_limit),
        "exposure": money(review.exposure),
        "cited_figures": review.cited_figures,
        "conditions": list(review.conditions),
        "narrative": review.narrative,
        "signals": list(review.signals),
        "authority": {
            "can_post": review.authority.can_post,
            "requires_approver": review.authority.requires_approver,
            "code": review.authority.code,
            "reason": review.authority.reason,
            "required_role": review.authority.required_role,
        },
        "status": review.status,
        "created_at": review.created_at,
        "posted_at": review.posted_at,
        "posted_by": review.posted_by,
        "approver_name": review.approver_name,
        "approver_role": review.approver_role,
    }


app = create_app()
