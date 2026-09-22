"""The graph itself: fan-out, fan-in, the revise loop, and reproducibility."""

from __future__ import annotations

import pytest
from langgraph.graph import END

from credit_underwriter.agents.context import GraphContext
from credit_underwriter.graph import (
    NODE_MEMO_WRITER,
    build_graph,
    should_revise,
)
from credit_underwriter.llm.base import LLMRequest, LLMResponse
from credit_underwriter.models import Critique, CritiqueIssue
from credit_underwriter.persistence import build_fingerprint, load_run, make_run_id
from credit_underwriter.service import replay, underwrite, verify
from credit_underwriter.state import merge_evidence


class Fabricator:
    """A provider that invents an unsupported figure in the memo.

    This is how the revise loop gets exercised: a well-behaved provider never
    trips the citation check, so a misbehaving one is needed to prove the loop
    fires, bounds itself, and repairs what it can.
    """

    name = "fabricator"
    model = "fabricator-v1"

    def __init__(self, inner, sabotage_sections=("recommendation",)) -> None:
        self.inner = inner
        self.sabotage_sections = set(sabotage_sections)
        self.calls: list[str] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request.task)
        response = self.inner.generate(request)
        if request.task != "memo_section":
            return response
        if request.facts.get("section_key") not in self.sabotage_sections:
            return response

        claims = list(response.output.get("claims", []))
        if claims:
            claims[0] = {
                **claims[0],
                "text": claims[0]["text"].rstrip(".")
                + ", against peer group losses of $77.77m.",
            }
        return LLMResponse(
            task=request.task,
            output={**response.output, "claims": claims},
            provider=self.name,
            model=self.model,
            notes=list(response.notes),
        )


# --------------------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------------------


def test_specialists_fan_out_from_the_plan_and_fan_in_to_reconciliation(context):
    graph = build_graph(context)
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    assert ("supervisor_plan", "financial_analyst") in edges
    assert ("supervisor_plan", "risk_searcher") in edges
    assert ("financial_analyst", "supervisor_reconcile") in edges
    assert ("risk_searcher", "supervisor_reconcile") in edges
    assert ("supervisor_reconcile", "memo_writer") in edges
    assert ("memo_writer", "memo_critic") in edges


def test_the_critic_can_route_back_to_the_writer(context):
    """Without this edge there is no revise loop, only a single pass."""
    graph = build_graph(context)
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("memo_critic", "memo_writer") in edges


def test_both_specialists_run_and_report_on_the_trace(runs):
    for applicant_id, record in runs.items():
        agents = [m.agent for m in record.trace]
        assert "financial_analyst" in agents, applicant_id
        assert "risk_searcher" in agents, applicant_id
        assert agents[0] == "supervisor", applicant_id
        assert "memo_writer" in agents and "memo_critic" in agents, applicant_id


def test_the_supervisor_records_its_delegation_plan(runs):
    for applicant_id, record in runs.items():
        assert record.plan, f"{applicant_id} recorded no plan"
        assert any("financial" in step.lower() for step in record.plan)
        assert any("risk" in step.lower() for step in record.plan)


def test_both_specialists_contribute_evidence(runs):
    """Fan-in must merge both branches, not let one overwrite the other."""
    for applicant_id, record in runs.items():
        prefixes = {item.evidence_id.split(":", 1)[0] for item in record.evidence}
        assert "ratio" in prefixes, applicant_id
        assert "doc" in prefixes, applicant_id


def test_evidence_reducer_merges_without_duplicating(runs):
    record = runs["atlas-precision-works"]
    half = len(record.evidence) // 2
    merged = merge_evidence(record.evidence[:half], record.evidence)
    assert len(merged) == len(record.evidence)
    assert [i.evidence_id for i in merged] == [i.evidence_id for i in record.evidence]


def test_evidence_reducer_is_order_independent_on_ids(runs):
    record = runs["atlas-precision-works"]
    forward = {i.evidence_id for i in merge_evidence(record.evidence, [])}
    backward = {i.evidence_id for i in merge_evidence([], record.evidence)}
    assert forward == backward


# --------------------------------------------------------------------------------------
# The revise loop
# --------------------------------------------------------------------------------------


def test_router_ends_on_a_passing_memo():
    passing = Critique(passed=True, revision=0, issues=[])
    assert should_revise({"critique": passing, "revision": 0}, max_revisions=2) == END


def test_router_returns_to_the_writer_on_a_failing_memo():
    failing = Critique(
        passed=False,
        revision=0,
        issues=[CritiqueIssue(code="unsupported_number", detail="invented")],
    )
    assert (
        should_revise({"critique": failing, "revision": 0}, max_revisions=2)
        == NODE_MEMO_WRITER
    )


def test_router_stops_at_the_revision_ceiling():
    """The loop must be bounded, or a stubborn provider spins forever."""
    failing = Critique(
        passed=False,
        revision=2,
        issues=[CritiqueIssue(code="unsupported_number", detail="invented")],
    )
    assert should_revise({"critique": failing, "revision": 2}, max_revisions=2) == END


def test_a_fabricated_figure_is_repaired_rather_than_published(
    atlas, settings, corpus, tmp_path
):
    """The writer's own repair step should strip an unevidenced figure."""
    scoped = settings.with_overrides(runs_dir=tmp_path / "runs")
    base = GraphContext.build(scoped)
    context = GraphContext(
        settings=scoped,
        provider=Fabricator(base.provider),
        index=base.index,
        documents=base.documents,
    )

    record = underwrite(atlas, settings=scoped, context=context, persist=False)

    assert "77.77" not in record.memo_markdown
    assert record.critique.passed
    assert record.memo.revision == 0, "repair should avoid a revision round trip"


def test_the_loop_bounds_itself_when_the_memo_cannot_be_fixed(
    atlas, settings, tmp_path, monkeypatch
):
    """A writer that keeps failing must stop at the ceiling, not loop forever."""
    import credit_underwriter.agents.memo_writer as writer

    # Defeat the writer's repair step so the critic keeps failing the memo.
    monkeypatch.setattr(writer, "_repair_claim", lambda claim, points, registry: claim)

    scoped = settings.with_overrides(runs_dir=tmp_path / "runs", max_memo_revisions=2)
    base = GraphContext.build(scoped)
    context = GraphContext(
        settings=scoped,
        provider=Fabricator(base.provider),
        index=base.index,
        documents=base.documents,
    )

    record = underwrite(atlas, settings=scoped, context=context, persist=False)

    assert not record.critique.passed
    assert record.memo.revision == scoped.max_memo_revisions
    assert len(record.critique_history) == scoped.max_memo_revisions + 1
    assert any(i.code == "unsupported_number" for i in record.critique.issues)

    writer_passes = [m for m in record.trace if m.agent == "memo_writer"]
    assert len(writer_passes) == scoped.max_memo_revisions + 1
    assert record.trace[-1].metrics["revision_ceiling_reached"] is True


def test_the_revision_ceiling_is_configurable(atlas, settings, tmp_path, monkeypatch):
    import credit_underwriter.agents.memo_writer as writer

    monkeypatch.setattr(writer, "_repair_claim", lambda claim, points, registry: claim)

    scoped = settings.with_overrides(runs_dir=tmp_path / "runs", max_memo_revisions=1)
    base = GraphContext.build(scoped)
    context = GraphContext(
        settings=scoped,
        provider=Fabricator(base.provider),
        index=base.index,
        documents=base.documents,
    )
    record = underwrite(atlas, settings=scoped, context=context, persist=False)
    assert record.memo.revision == 1


def test_a_failed_memo_still_produces_a_readable_document(atlas, settings, tmp_path, monkeypatch):
    """Releasing with recorded issues beats releasing nothing."""
    import credit_underwriter.agents.memo_writer as writer

    monkeypatch.setattr(writer, "_repair_claim", lambda claim, points, registry: claim)
    scoped = settings.with_overrides(runs_dir=tmp_path / "runs")
    base = GraphContext.build(scoped)
    context = GraphContext(
        settings=scoped,
        provider=Fabricator(base.provider),
        index=base.index,
        documents=base.documents,
    )
    record = underwrite(atlas, settings=scoped, context=context, persist=False)

    assert "## Outstanding completeness issues" in record.memo_markdown
    assert "unsupported_number" in record.memo_markdown


# --------------------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------------------


def test_the_same_application_underwrites_identically_twice(atlas, settings, context, tmp_path):
    scoped = settings.with_overrides(runs_dir=tmp_path / "runs")
    first = underwrite(atlas, settings=scoped, context=context, persist=False)
    second = underwrite(atlas, settings=scoped, context=context, persist=False)
    assert first.state_hash == second.state_hash


def test_a_changed_statement_changes_the_state_hash(atlas, settings, context, tmp_path):
    scoped = settings.with_overrides(runs_dir=tmp_path / "runs")
    baseline = underwrite(atlas, settings=scoped, context=context, persist=False)

    weaker = atlas.model_copy(deep=True)
    weaker.statements[-1].income_statement.operating_expenses *= 2.5
    changed = underwrite(weaker, settings=scoped, context=context, persist=False)

    assert changed.state_hash != baseline.state_hash
    assert changed.fingerprint.application_hash != baseline.fingerprint.application_hash


def test_the_run_id_is_derived_from_the_application_and_configuration(
    atlas, settings, corpus
):
    from credit_underwriter.retrieval import corpus_hash

    fingerprint = build_fingerprint(settings, atlas, corpus_hash(corpus))
    assert make_run_id(atlas, fingerprint) == make_run_id(atlas, fingerprint)
    assert make_run_id(atlas, fingerprint).startswith(atlas.applicant_id)


def test_the_fingerprint_records_what_could_change_the_outcome(runs):
    record = runs["atlas-precision-works"]
    fingerprint = record.fingerprint
    assert fingerprint.llm_provider == "offline"
    assert fingerprint.live_search_enabled is False
    assert fingerprint.corpus_hash
    assert fingerprint.engine_version
    assert fingerprint.as_of_date


def test_a_different_provider_yields_a_different_fingerprint(atlas, settings, corpus):
    from credit_underwriter.retrieval import corpus_hash

    offline = build_fingerprint(settings, atlas, corpus_hash(corpus))
    other = build_fingerprint(
        settings.with_overrides(llm_provider="openai", llm_model="gpt-4o-mini"),
        atlas,
        corpus_hash(corpus),
    )
    assert offline.digest() != other.digest()


# --------------------------------------------------------------------------------------
# Persistence, replay, and verify
# --------------------------------------------------------------------------------------


def test_a_run_persists_and_reloads_without_loss(atlas, tmp_settings, context):
    record = underwrite(atlas, settings=tmp_settings, context=context)
    reloaded = load_run(record.run_id, tmp_settings)
    assert reloaded.state_hash == record.state_hash
    assert reloaded.decision.model_dump() == record.decision.model_dump()
    assert reloaded.memo.model_dump() == record.memo.model_dump()
    assert len(reloaded.evidence) == len(record.evidence)
    assert len(reloaded.trace) == len(record.trace)


def test_replay_rebuilds_the_memo_from_persisted_state(atlas, tmp_settings, context):
    record = underwrite(atlas, settings=tmp_settings, context=context)
    replayed = replay(record.run_id, tmp_settings)

    assert replayed.run_id == record.run_id
    assert "replayed from" in replayed.memo_markdown
    for claim in record.memo.claims:
        assert claim.text in replayed.memo_markdown


def test_verify_confirms_a_reproducible_run(atlas, tmp_settings, context):
    record = underwrite(atlas, settings=tmp_settings, context=context)
    result = verify(record.run_id, tmp_settings)
    assert result.passed
    assert result.content_intact
    assert result.reproducible
    assert result.stored.state_hash == result.fresh.state_hash


def test_verify_detects_a_tampered_run(atlas, tmp_settings, context):
    """A persisted run whose numbers were edited must fail verification."""
    import json

    from credit_underwriter.persistence import run_path

    record = underwrite(atlas, settings=tmp_settings, context=context)
    path = run_path(tmp_settings, record.run_id)
    payload = json.loads(path.read_text())
    payload["decision"]["approved_limit"] = 9_000_000.0
    path.write_text(json.dumps(payload))

    result = verify(record.run_id, tmp_settings)
    assert not result.passed
    assert not result.content_intact
    assert result.stored.decision.approved_limit == 9_000_000.0
    assert result.fresh.decision.approved_limit != 9_000_000.0


def test_replaying_an_unknown_run_raises(tmp_settings):
    with pytest.raises(FileNotFoundError):
        replay("no-such-run", tmp_settings)


def test_persisted_runs_are_listed_newest_first(applications, tmp_settings, context):
    from credit_underwriter.persistence import list_runs

    for application in applications.values():
        underwrite(application, settings=tmp_settings, context=context)

    listed = list_runs(tmp_settings)
    assert len(listed) == len(applications)
    assert [r.created_at for r in listed] == sorted(
        (r.created_at for r in listed), reverse=True
    )
