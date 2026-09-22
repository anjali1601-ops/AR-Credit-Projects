from __future__ import annotations

import pytest

from dunning import graph as graph_module
from dunning.domain import ReviewResult
from dunning.graph import run_account
from dunning.providers.tracing import read_trace
from tests.conftest import ARCHETYPE_SAMPLES


@pytest.mark.parametrize("archetype,account_id", sorted(ARCHETYPE_SAMPLES.items()))
def test_graph_runs_end_to_end_for_every_archetype(runs, archetype, account_id):
    result = runs[account_id]

    assert result.profile.archetype == archetype
    assert result.sequence.steps
    assert result.review.passed
    assert result.strategy.stage == result.sequence.stage
    assert all(step.body.strip() for step in result.sequence.steps)


def test_every_agent_node_reports_into_the_shared_log(runs):
    log = "\n".join(runs["ACC-2001"].agent_log)
    for node in ("ingest:", "profiler:", "sentiment[", "strategy:", "communications:", "review:", "finalize:"):
        assert node in log


def test_approval_gate_only_runs_when_governance_requires_it(runs):
    high_risk_log = "\n".join(runs["ACC-3001"].agent_log)
    reliable_log = "\n".join(runs["ACC-1001"].agent_log)

    assert "approval_gate:" in high_risk_log
    assert "approval_gate:" not in reliable_log


def test_runs_are_deterministic(deps):
    first = run_account("ACC-2002", deps)
    second = run_account("ACC-2002", deps)

    assert first.run_id != second.run_id
    assert [step.body for step in first.sequence.steps] == [step.body for step in second.sequence.steps]
    assert first.profile.risk_score == second.profile.risk_score


def test_failed_review_loops_back_to_the_communications_agent(deps, monkeypatch):
    calls = {"count": 0}
    original = graph_module.review_sequence

    def flaky_review(sequence, decision):
        calls["count"] += 1
        if calls["count"] == 1:
            return ReviewResult(passed=False, issues=["opening message must state the outstanding amount"], checks_run=1)
        return original(sequence, decision)

    monkeypatch.setattr(graph_module, "review_sequence", flaky_review)
    result = run_account("ACC-1002", deps)

    assert result.revisions == 1
    assert calls["count"] == 2
    assert result.review.passed
    assert any("revision 1" in line for line in result.agent_log)


def test_revision_loop_is_bounded(deps, monkeypatch):
    monkeypatch.setattr(
        graph_module,
        "review_sequence",
        lambda sequence, decision: ReviewResult(passed=False, issues=["always fails"], checks_run=1),
    )
    result = run_account("ACC-1002", deps)

    assert result.revisions == deps.settings.max_draft_revisions
    assert not result.review.passed  # surfaced to the operator rather than looping forever


def test_local_tracer_records_a_span_per_agent(runs, settings):
    result = runs["ACC-3001"]
    records = read_trace(result.trace_reference)
    header, spans = records[0], records[1:]
    span_names = [span["span"] for span in spans]

    assert header["metadata"]["account_id"] == "ACC-3001"
    assert span_names[0] == "ingest"
    # profiler and sentiment fan out in parallel, so their relative order is not fixed
    assert set(span_names[1:3]) == {"profiler_agent", "sentiment_agent"}
    assert span_names.index("strategy_agent") > 2
    assert span_names[-1] == "finalize"
    assert "compliance_review" in span_names
    assert all(span["duration_ms"] >= 0 for span in spans)

    by_name = {span["span"]: span for span in spans}
    assert by_name["profiler_agent"]["output"]["archetype"] == "high_risk_delinquent"
    assert by_name["strategy_agent"]["output"]["stage"] == "pre_legal_notice"


def test_unknown_account_raises(deps):
    with pytest.raises(KeyError):
        run_account("ACC-9999", deps)
