"""Small FastAPI surface over the same agent graph the CLI uses."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .domain import RunResult
from .evaluation.harness import run_eval
from .graph import GraphDependencies, run_account

api = FastAPI(
    title="Dunning & Collections Assistant",
    version="0.1.0",
    description="Profiler, sentiment and communications agents behind one HTTP endpoint.",
)


@lru_cache(maxsize=1)
def _deps() -> GraphDependencies:
    return GraphDependencies.build(get_settings())


class RunRequest(BaseModel):
    account_id: str = Field(..., examples=["ACC-2001"])
    include_bodies: bool = True


class AccountSummary(BaseModel):
    account_id: str
    name: str
    segment: str
    open_invoices: int
    past_due_balance: float
    oldest_days_past_due: int
    archetype: str


@api.get("/health")
def health() -> dict:
    deps = _deps()
    return {
        "status": "ok",
        "as_of": deps.settings.as_of.isoformat(),
        "llm_provider": getattr(deps.llm, "name", "unknown"),
        "sentiment_backend": getattr(deps.sentiment, "name", "unknown"),
        "tracing_backend": getattr(deps.tracer, "name", "unknown"),
        "accounts": len(deps.repository.account_ids()),
    }


@api.get("/accounts", response_model=list[AccountSummary])
def list_accounts() -> list[AccountSummary]:
    summary = _deps().repository.portfolio_summary().fillna(0)
    return [
        AccountSummary(
            account_id=row["account_id"],
            name=row["name"],
            segment=row["segment"],
            open_invoices=int(row["open_invoices"]),
            past_due_balance=float(row["past_due_balance"]),
            oldest_days_past_due=int(row["oldest_days_past_due"]),
            archetype=row["archetype"],
        )
        for _, row in summary.iterrows()
    ]


@api.post("/runs", response_model=RunResult)
def create_run(request: RunRequest) -> RunResult:
    try:
        result = run_account(request.account_id.upper(), _deps())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not request.include_bodies:
        trimmed = [step.model_copy(update={"body": ""}) for step in result.sequence.steps]
        result = result.model_copy(update={"sequence": result.sequence.model_copy(update={"steps": trimmed})})
    return result


@api.get("/accounts/{account_id}/sequence", response_model=RunResult)
def account_sequence(account_id: str) -> RunResult:
    return create_run(RunRequest(account_id=account_id))


@api.get("/eval")
def evaluate(account_id: Optional[str] = None) -> dict:
    report = run_eval(_deps(), [account_id.upper()] if account_id else None)
    return report.to_dict()
