from __future__ import annotations

import json

from dunning.evaluation.expectations import EXPECTATIONS
from dunning.evaluation.harness import run_eval, score_case


def test_every_seeded_account_passes_its_archetype_expectations(deps):
    report = run_eval(deps)

    assert len(report.cases) == 9
    assert report.pass_rate == 1.0
    assert report.overall_score == 1.0
    assert set(report.by_archetype) == set(EXPECTATIONS)
    assert all(rate == 1.0 for rate in report.by_dimension.values())


def test_eval_catches_an_under_escalated_high_risk_sequence(runs):
    result = runs["ACC-3001"]
    softened = result.model_copy(
        update={
            "strategy": result.strategy.model_copy(
                update={
                    "stage": "courtesy_reminder",
                    "tone": "warm",
                    "channels": ["email"],
                    "legal_referral": False,
                    "human_approval_required": False,
                }
            )
        }
    )
    case = score_case(softened, "high_risk_delinquent")
    failed = {dimension.name for dimension in case.dimensions if not dimension.passed}

    assert not case.passed
    assert {"escalation_stage", "tone", "channel_mix", "policy_flags"} <= failed
    assert case.score < case.max_score


def test_eval_catches_legal_threats_sent_to_a_reliable_payer(runs):
    result = runs["ACC-1001"]
    harsh_steps = [
        step.model_copy(update={"body": step.body + "\nThis account will be referred to our collections counsel."})
        for step in result.sequence.steps
    ]
    harsh = result.model_copy(update={"sequence": result.sequence.model_copy(update={"steps": harsh_steps})})

    case = score_case(harsh, "reliable_but_late")
    failures = {dimension.name for dimension in case.dimensions if not dimension.passed}

    assert "content_safety" in failures
    assert not case.passed


def test_eval_catches_a_misclassified_archetype(runs):
    result = runs["ACC-2001"]
    case = score_case(result, "reliable_but_late")
    failures = {dimension.name for dimension in case.dimensions if not dimension.passed}

    assert "archetype_detection" in failures
    assert any("deteriorating_avoidant" in dimension.detail for dimension in case.dimensions)


def test_report_serialises_to_json_and_a_frame(deps, tmp_path):
    report = run_eval(deps, account_ids=["ACC-1001", "ACC-3001"])
    path = report.write_json(tmp_path / "eval.json")
    payload = json.loads(path.read_text())
    frame = report.to_frame()

    assert payload["cases"][0]["account_id"] == "ACC-1001"
    assert payload["sentiment_backend"] == "rules"
    assert list(frame.columns)[:4] == ["account_id", "customer", "expected", "predicted"]
    assert len(frame) == 2
