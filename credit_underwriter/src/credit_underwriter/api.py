"""FastAPI service.

Submit an application, underwrite it, and fetch the memo. The graph context --
and therefore the Chroma index -- is built once and reused across requests.

Bound to port 47113 by default, which is deliberately outside the usual dev-server
range.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Body, FastAPI, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from .agents.context import GraphContext
from .applicants import ApplicantNotFound, get_application, list_applications
from .config import DEFAULT_API_PORT, Settings
from .models import CreditApplication, format_currency
from .persistence import RunRecord, list_runs, load_run
from .service import underwrite

DESCRIPTION = """
A supervisor-led multi-agent credit underwriter for enterprise B2B onboarding.

A supervisor delegates in parallel to a **financial analyst** (deterministic spread,
ratios, trends, internal rating) and a **risk searcher** (retrieval over a local
corpus of news, filings, litigation, and country/industry reports), reconciles the
two views under documented precedence rules, and writes an underwriting memo in
which every claim cites its source.

The default LLM provider is deterministic and offline, so the service runs with no
API keys.
"""

app = FastAPI(
    title="Autonomous AI Credit Underwriter",
    description=DESCRIPTION,
    version="0.1.0",
)

_settings = Settings.from_env()
_context: GraphContext | None = None

#: Applications submitted over the API, keyed by applicant id. The seeded
#: applicants on disk are always visible; this holds anything posted at runtime.
_submitted: dict[str, CreditApplication] = {}


def get_settings() -> Settings:
    return _settings


def get_context() -> GraphContext:
    """Build the graph context once and reuse it.

    Building it per request would rebuild the vector index every time.
    """
    global _context
    if _context is None:
        _context = GraphContext.build(_settings)
    return _context


def reset_state(settings: Settings | None = None) -> None:
    """Reset the module-level context and submissions. Used by the test suite."""
    global _settings, _context
    if settings is not None:
        _settings = settings
    _context = None
    _submitted.clear()


# --------------------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    llm_provider: str
    llm_model: str
    retrieval_backend: str
    corpus_documents: int
    live_search_enabled: bool
    as_of_date: str
    seeded_applicants: list[str]


class ApplicantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    legal_name: str
    country: str
    industry: str
    years_in_business: float
    requested_limit: float
    requested_terms_days: int
    currency: str
    latest_period: str
    latest_revenue: float
    statement_opinion: str
    source: Literal["seeded", "submitted"]


class SubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str
    legal_name: str
    accepted: bool = True
    statements_received: int
    message: str


class DecisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    applicant_id: str
    legal_name: str
    recommendation: str
    approved_limit: float
    approved_terms_days: int
    requested_limit: float
    requested_terms_days: int
    currency: str
    standalone_grade: int
    final_grade: int
    final_band_label: str
    applied_notches: float
    security_required: bool
    review_frequency_months: int
    resolution_rules: list[str]
    conditions: list[str]
    documents_reviewed: int
    adverse_findings: int
    memo_claims: int
    memo_revision: int
    completeness_check_passed: bool
    outstanding_issues: list[str]
    state_hash: str


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    applicant_id: str
    created_at: str
    recommendation: str
    approved_limit: float
    final_grade: int
    state_hash: str


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    count: int
    items: list[dict[str, Any]]


class TraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    plan: list[str]
    trace: list[dict[str, Any]]


# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> dict[str, Any]:
    return {
        "service": "Autonomous AI Credit Underwriter",
        "docs": "/docs",
        "endpoints": {
            "GET /healthz": "service and configuration status",
            "GET /applicants": "seeded and submitted applicants",
            "POST /applications": "submit a credit application",
            "POST /applications/{applicant_id}/underwrite": "run the underwriting graph",
            "GET /applications/{applicant_id}/memo": "latest memo, markdown or JSON",
            "GET /runs": "persisted runs",
            "GET /runs/{run_id}": "one persisted run decision",
            "GET /runs/{run_id}/evidence": "the run's evidence registry",
            "GET /runs/{run_id}/trace": "what each agent did",
        },
        "default_port": DEFAULT_API_PORT,
    }


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    context = get_context()
    return HealthResponse(
        llm_provider=context.provider.name,
        llm_model=context.provider.model,
        retrieval_backend=context.index.backend,
        corpus_documents=context.index.count(),
        live_search_enabled=_settings.enable_live_search,
        as_of_date=_settings.as_of_date,
        seeded_applicants=[a.applicant_id for a in list_applications(_settings)],
    )


@app.get("/applicants", response_model=list[ApplicantSummary])
def applicants() -> list[ApplicantSummary]:
    summaries: list[ApplicantSummary] = []
    for application in list_applications(_settings):
        summaries.append(_summarise(application, "seeded"))
    for application in _submitted.values():
        summaries.append(_summarise(application, "submitted"))
    return summaries


@app.post("/applications", response_model=SubmissionResponse, status_code=201)
def submit_application(
    application: Annotated[CreditApplication, Body(description="A full credit application")],
) -> SubmissionResponse:
    _submitted[application.applicant_id] = application
    return SubmissionResponse(
        applicant_id=application.applicant_id,
        legal_name=application.legal_name,
        statements_received=len(application.statements),
        message=(
            f"Application accepted. POST /applications/{application.applicant_id}/underwrite "
            "to run the underwriting graph."
        ),
    )


@app.post("/applications/{applicant_id}/underwrite", response_model=DecisionSummary)
def underwrite_application(
    applicant_id: Annotated[str, Path(description="Seeded or submitted applicant id")],
) -> DecisionSummary:
    application = _resolve(applicant_id)
    record = underwrite(application, settings=_settings, context=get_context())
    return _summarise_decision(record)


@app.get("/applications/{applicant_id}/memo")
def get_memo(
    applicant_id: Annotated[str, Path(description="Seeded or submitted applicant id")],
    format: Annotated[Literal["markdown", "json"], Query()] = "markdown",
    underwrite_if_missing: Annotated[bool, Query()] = True,
):
    record = _latest_run_for(applicant_id)
    if record is None:
        if not underwrite_if_missing:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no completed underwriting for {applicant_id!r}; POST "
                    f"/applications/{applicant_id}/underwrite first"
                ),
            )
        record = underwrite(_resolve(applicant_id), settings=_settings, context=get_context())

    if format == "markdown":
        return PlainTextResponse(record.memo_markdown, media_type="text/markdown")
    return {
        "run_id": record.run_id,
        "decision": _summarise_decision(record).model_dump(),
        "memo": record.memo.model_dump(mode="json"),
    }


@app.get("/runs", response_model=list[RunSummary])
def runs() -> list[RunSummary]:
    return [
        RunSummary(
            run_id=record.run_id,
            applicant_id=record.applicant_id,
            created_at=record.created_at,
            recommendation=record.decision.recommendation.value,
            approved_limit=record.decision.approved_limit,
            final_grade=record.decision.final_grade,
            state_hash=record.state_hash,
        )
        for record in list_runs(_settings)
    ]


@app.get("/runs/{run_id}", response_model=DecisionSummary)
def get_run(run_id: str) -> DecisionSummary:
    return _summarise_decision(_load(run_id))


@app.get("/runs/{run_id}/evidence", response_model=EvidenceResponse)
def get_run_evidence(
    run_id: str,
    kind: Annotated[str | None, Query(description="Filter by evidence kind")] = None,
) -> EvidenceResponse:
    record = _load(run_id)
    items = [
        item.model_dump(mode="json")
        for item in record.evidence
        if kind is None or item.kind.value == kind
    ]
    return EvidenceResponse(run_id=run_id, count=len(items), items=items)


@app.get("/runs/{run_id}/trace", response_model=TraceResponse)
def get_run_trace(run_id: str) -> TraceResponse:
    record = _load(run_id)
    return TraceResponse(
        run_id=run_id,
        plan=record.plan,
        trace=[message.model_dump(mode="json") for message in record.trace],
    )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _resolve(applicant_id: str) -> CreditApplication:
    if applicant_id in _submitted:
        return _submitted[applicant_id]
    try:
        return get_application(applicant_id, _settings)
    except ApplicantNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown applicant {applicant_id!r}; available: {exc.available}",
        ) from exc


def _load(run_id: str) -> RunRecord:
    try:
        return load_run(run_id, _settings)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _latest_run_for(applicant_id: str) -> RunRecord | None:
    matches = [r for r in list_runs(_settings) if r.applicant_id == applicant_id]
    return matches[0] if matches else None


def _summarise(application: CreditApplication, source: str) -> ApplicantSummary:
    latest = application.latest_statement
    return ApplicantSummary(
        applicant_id=application.applicant_id,
        legal_name=application.legal_name,
        country=application.country,
        industry=application.industry,
        years_in_business=application.years_in_business,
        requested_limit=application.requested_limit,
        requested_terms_days=application.requested_terms_days,
        currency=application.currency,
        latest_period=latest.period_label,
        latest_revenue=latest.income_statement.revenue,
        statement_opinion=latest.opinion.value,
        source=source,  # type: ignore[arg-type]
    )


def _summarise_decision(record: RunRecord) -> DecisionSummary:
    decision = record.decision
    return DecisionSummary(
        run_id=record.run_id,
        applicant_id=record.applicant_id,
        legal_name=record.application.legal_name,
        recommendation=decision.recommendation.value,
        approved_limit=decision.approved_limit,
        approved_terms_days=decision.approved_terms_days,
        requested_limit=decision.requested_limit,
        requested_terms_days=decision.requested_terms_days,
        currency=record.application.currency,
        standalone_grade=decision.standalone_grade,
        final_grade=decision.final_grade,
        final_band_label=decision.final_band_label,
        applied_notches=decision.applied_notches,
        security_required=decision.security_required,
        review_frequency_months=decision.review_frequency_months,
        resolution_rules=[c.rule for c in decision.conflicts],
        conditions=[c.text for c in decision.conditions],
        documents_reviewed=len(record.risk_assessment.retrieved),
        adverse_findings=len(record.risk_assessment.adverse_findings),
        memo_claims=len(record.memo.claims),
        memo_revision=record.memo.revision,
        completeness_check_passed=record.critique.passed,
        outstanding_issues=[f"{i.code}: {i.detail}" for i in record.critique.issues],
        state_hash=record.state_hash,
    )


__all__ = ["app", "reset_state", "DEFAULT_API_PORT", "format_currency"]
