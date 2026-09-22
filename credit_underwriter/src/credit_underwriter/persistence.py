"""Persisted run state.

A completed underwriting is written out whole: the application as submitted, the
financial analysis, the risk assessment, the reconciled decision, the memo, the
critique history, the full evidence registry, and the trace of what each agent
did. Two things make it reproducible:

* ``fingerprint`` records everything about the configuration that could change
  the outcome -- provider, model, retrieval backend, as-of date, engine version,
  and a hash of the corpus; and
* ``state_hash`` is a digest of the analysis, decision, and memo, so re-running
  the same application under the same fingerprint can be checked for an
  identical result. ``credit-underwriter verify`` does exactly that.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .config import ENGINE_VERSION, Settings
from .evidence import EvidenceRegistry
from .models import (
    CreditApplication,
    CreditDecision,
    Critique,
    EvidenceItem,
    FinancialAnalysis,
    RiskAssessment,
    UnderwritingMemo,
)
from .state import AgentMessage

SCHEMA_VERSION = 1


class RunFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_version: str = ENGINE_VERSION
    settings_fingerprint: str
    llm_provider: str
    llm_model: str
    retrieval_backend: str
    retrieval_top_k: int
    live_search_enabled: bool
    as_of_date: str
    corpus_hash: str
    application_hash: str

    def digest(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    run_id: str
    applicant_id: str
    created_at: str
    fingerprint: RunFingerprint
    application: CreditApplication
    plan: list[str] = Field(default_factory=list)
    financial_analysis: FinancialAnalysis
    risk_assessment: RiskAssessment
    decision: CreditDecision
    memo: UnderwritingMemo
    memo_markdown: str
    critique: Critique
    critique_history: list[Critique] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    trace: list[AgentMessage] = Field(default_factory=list)
    state_hash: str = ""

    @property
    def registry(self) -> EvidenceRegistry:
        return EvidenceRegistry(self.evidence)

    def compute_state_hash(self) -> str:
        """Digest of the decision-bearing content, excluding wall-clock fields."""
        payload = {
            "fingerprint": self.fingerprint.model_dump(mode="json"),
            "financial_analysis": self.financial_analysis.model_dump(mode="json"),
            "risk_assessment": self.risk_assessment.model_dump(mode="json"),
            "decision": self.decision.model_dump(mode="json"),
            "memo": self.memo.model_dump(mode="json"),
            "critique": self.critique.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in self.evidence],
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def with_state_hash(self) -> RunRecord:
        return self.model_copy(update={"state_hash": self.compute_state_hash()})


def application_hash(application: CreditApplication) -> str:
    blob = json.dumps(application.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build_fingerprint(
    settings: Settings, application: CreditApplication, corpus_hash: str
) -> RunFingerprint:
    return RunFingerprint(
        settings_fingerprint=settings.fingerprint(),
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        retrieval_backend=settings.retrieval_backend,
        retrieval_top_k=settings.retrieval_top_k,
        live_search_enabled=settings.enable_live_search,
        as_of_date=settings.as_of_date,
        corpus_hash=corpus_hash,
        application_hash=application_hash(application),
    )


def make_run_id(application: CreditApplication, fingerprint: RunFingerprint) -> str:
    """Deterministic run id.

    The same application under the same configuration always produces the same
    run id, so re-running overwrites the prior record instead of accumulating
    near-duplicates. Pass ``unique=True`` behaviour by varying the fingerprint.
    """
    return f"{application.applicant_id}-{fingerprint.digest()[:10]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_path(settings: Settings, run_id: str) -> Path:
    return settings.runs_dir / f"{run_id}.json"


def save_run(record: RunRecord, settings: Settings) -> Path:
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    path = run_path(settings, record.run_id)
    path.write_text(json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=False))
    return path


def load_run(run_id: str, settings: Settings) -> RunRecord:
    path = run_path(settings, run_id)
    if not path.exists():
        raise FileNotFoundError(f"no persisted run {run_id!r} at {path}")
    return RunRecord.model_validate(json.loads(path.read_text()))


def list_runs(settings: Settings) -> list[RunRecord]:
    if not settings.runs_dir.exists():
        return []
    records: list[RunRecord] = []
    for path in sorted(settings.runs_dir.glob("*.json")):
        try:
            records.append(RunRecord.model_validate(json.loads(path.read_text())))
        except (json.JSONDecodeError, ValueError):
            continue
    return sorted(records, key=lambda r: r.created_at, reverse=True)
