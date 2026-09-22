"""The underwriting facade shared by the CLI and the API."""

from __future__ import annotations

from .agents.context import GraphContext
from .config import Settings
from .evidence import EvidenceRegistry
from .graph import build_graph
from .memo import render_memo
from .models import CreditApplication
from .persistence import (
    RunRecord,
    build_fingerprint,
    load_run,
    make_run_id,
    save_run,
    utc_now,
)
from .state import UnderwritingState


def underwrite(
    application: CreditApplication,
    settings: Settings | None = None,
    context: GraphContext | None = None,
    persist: bool = True,
) -> RunRecord:
    """Run the full graph for one application and return the persisted record."""
    settings = settings or Settings.from_env()
    context = context or GraphContext.build(settings)

    fingerprint = build_fingerprint(settings, application, context.corpus_fingerprint())
    run_id = make_run_id(application, fingerprint)

    graph = build_graph(context)
    initial: UnderwritingState = {
        "run_id": run_id,
        "application": application,
        "evidence": [],
        "trace": [],
        "critique_history": [],
        "revision": 0,
    }
    final: UnderwritingState = graph.invoke(initial)

    registry = EvidenceRegistry(final.get("evidence", []))
    memo = final["memo"]
    decision = final["decision"]
    critique = final["critique"]

    markdown = render_memo(
        memo=memo,
        decision=decision,
        registry=registry,
        critique=critique,
        run_id=run_id,
        provenance={
            "llm provider": f"{context.provider.name} ({context.provider.model})",
            "retrieval backend": final["risk_assessment"].retrieval_backend,
            "live search": "enabled" if settings.enable_live_search else "disabled",
            "corpus hash": fingerprint.corpus_hash,
            "engine version": fingerprint.engine_version,
            "documents reviewed": str(len(final["risk_assessment"].retrieved)),
            "evidence items": str(len(registry)),
        },
    )

    record = RunRecord(
        run_id=run_id,
        applicant_id=application.applicant_id,
        created_at=utc_now(),
        fingerprint=fingerprint,
        application=application,
        plan=final.get("plan", []),
        financial_analysis=final["financial_analysis"],
        risk_assessment=final["risk_assessment"],
        decision=decision,
        memo=memo,
        memo_markdown=markdown,
        critique=critique,
        critique_history=final.get("critique_history", []),
        evidence=registry.items(),
        trace=final.get("trace", []),
    ).with_state_hash()

    if persist:
        save_run(record, settings)
    return record


def replay(run_id: str, settings: Settings | None = None) -> RunRecord:
    """Reload a persisted run. The memo is re-rendered from stored state."""
    settings = settings or Settings.from_env()
    record = load_run(run_id, settings)
    markdown = render_memo(
        memo=record.memo,
        decision=record.decision,
        registry=record.registry,
        critique=record.critique,
        run_id=record.run_id,
        provenance={
            "llm provider": f"{record.fingerprint.llm_provider} ({record.fingerprint.llm_model})",
            "retrieval backend": record.fingerprint.retrieval_backend,
            "corpus hash": record.fingerprint.corpus_hash,
            "engine version": record.fingerprint.engine_version,
            "replayed from": "persisted run state",
        },
    )
    return record.model_copy(update={"memo_markdown": markdown})


def verify(run_id: str, settings: Settings | None = None) -> tuple[bool, RunRecord, RunRecord]:
    """Re-underwrite a persisted run and compare state hashes.

    Returns ``(matches, stored, fresh)``. A mismatch means either the engine or
    the corpus changed since the run was recorded, which the differing
    fingerprints will show.
    """
    settings = settings or Settings.from_env()
    stored = load_run(run_id, settings)
    fresh = underwrite(stored.application, settings=settings, persist=False)
    return stored.state_hash == fresh.state_hash, stored, fresh
