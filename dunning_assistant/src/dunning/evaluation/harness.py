"""Offline eval: run the graph over the seeded portfolio and grade each sequence.

No API keys and no judge model - every dimension is a deterministic assertion
about the strategy the agents produced for a known archetype.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..domain import RunResult
from ..graph import GraphDependencies, run_account
from .expectations import EXPECTATIONS, ArchetypeExpectation

DIMENSION_WEIGHTS = {
    "archetype_detection": 2.0,
    "escalation_stage": 2.0,
    "tone": 1.5,
    "channel_mix": 1.0,
    "policy_flags": 1.5,
    "sequence_shape": 1.0,
    "content_safety": 1.0,
    "compliance_review": 1.0,
}


@dataclass
class DimensionScore:
    name: str
    passed: bool
    weight: float
    detail: str


@dataclass
class CaseResult:
    account_id: str
    customer: str
    expected_archetype: str
    predicted_archetype: str
    relationship_label: str
    stage: str
    tone: str
    risk_score: float
    steps: int
    score: float
    max_score: float
    passed: bool
    dimensions: list[DimensionScore] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [f"{d.name}: {d.detail}" for d in self.dimensions if not d.passed]


@dataclass
class EvalReport:
    cases: list[CaseResult]
    overall_score: float
    pass_rate: float
    by_dimension: dict[str, float]
    by_archetype: dict[str, float]
    llm_provider: str
    sentiment_backend: str
    generated_at: str

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "llm_provider": self.llm_provider,
            "sentiment_backend": self.sentiment_backend,
            "overall_score": self.overall_score,
            "pass_rate": self.pass_rate,
            "by_dimension": self.by_dimension,
            "by_archetype": self.by_archetype,
            "cases": [asdict(case) for case in self.cases],
        }

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "account_id": c.account_id,
                    "customer": c.customer,
                    "expected": c.expected_archetype,
                    "predicted": c.predicted_archetype,
                    "sentiment": c.relationship_label,
                    "stage": c.stage,
                    "tone": c.tone,
                    "steps": c.steps,
                    "score": round(c.score / c.max_score, 3),
                    "passed": c.passed,
                    "failures": "; ".join(c.failures),
                }
                for c in self.cases
            ]
        )

    def write_json(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str) + "\n")
        return path


def score_case(result: RunResult, expected_archetype: str) -> CaseResult:
    expectation: ArchetypeExpectation = EXPECTATIONS[expected_archetype]
    decision = result.strategy
    sequence = result.sequence
    dimensions: list[DimensionScore] = []

    dimensions.append(
        DimensionScore(
            "archetype_detection",
            result.profile.archetype == expected_archetype,
            DIMENSION_WEIGHTS["archetype_detection"],
            f"predicted {result.profile.archetype}, expected {expected_archetype}",
        )
    )
    dimensions.append(
        DimensionScore(
            "escalation_stage",
            decision.stage in expectation.allowed_stages,
            DIMENSION_WEIGHTS["escalation_stage"],
            f"{decision.stage} not in {sorted(expectation.allowed_stages)}"
            if decision.stage not in expectation.allowed_stages
            else decision.stage,
        )
    )
    dimensions.append(
        DimensionScore(
            "tone",
            decision.tone in expectation.allowed_tones,
            DIMENSION_WEIGHTS["tone"],
            f"{decision.tone} not in {sorted(expectation.allowed_tones)}"
            if decision.tone not in expectation.allowed_tones
            else decision.tone,
        )
    )

    channels = set(decision.channels)
    missing_channels = expectation.required_channels - channels
    banned_channels = expectation.forbidden_channels & channels
    dimensions.append(
        DimensionScore(
            "channel_mix",
            not missing_channels and not banned_channels,
            DIMENSION_WEIGHTS["channel_mix"],
            f"missing {sorted(missing_channels)} / forbidden {sorted(banned_channels)}"
            if (missing_channels or banned_channels)
            else ", ".join(decision.channels),
        )
    )

    active_flags = {name for name in EXPECTATIONS[expected_archetype].required_flags | expectation.forbidden_flags}
    flag_values = {name: bool(getattr(decision, name, False)) for name in active_flags}
    missing_flags = {name for name in expectation.required_flags if not flag_values.get(name)}
    banned_flags = {name for name in expectation.forbidden_flags if flag_values.get(name)}
    dimensions.append(
        DimensionScore(
            "policy_flags",
            not missing_flags and not banned_flags,
            DIMENSION_WEIGHTS["policy_flags"],
            f"missing {sorted(missing_flags)} / must not be set {sorted(banned_flags)}"
            if (missing_flags or banned_flags)
            else "ok",
        )
    )

    offsets = [step.day_offset for step in sequence.steps]
    shape_problems = []
    if not expectation.min_steps <= len(sequence.steps) <= expectation.max_steps:
        shape_problems.append(f"{len(sequence.steps)} steps outside {expectation.min_steps}-{expectation.max_steps}")
    if offsets != sorted(offsets) or len(set(offsets)) != len(offsets):
        shape_problems.append("step timings are not strictly increasing")
    if offsets and offsets[-1] > expectation.max_sequence_days:
        shape_problems.append(f"sequence spans {offsets[-1]} days (max {expectation.max_sequence_days})")
    dimensions.append(
        DimensionScore(
            "sequence_shape",
            not shape_problems,
            DIMENSION_WEIGHTS["sequence_shape"],
            "; ".join(shape_problems) if shape_problems else f"{len(sequence.steps)} steps over {offsets[-1] if offsets else 0} days",
        )
    )

    blob = "\n".join(step.body.lower() for step in sequence.steps)
    leaked = sorted(phrase for phrase in expectation.forbidden_phrases if phrase in blob)
    dimensions.append(
        DimensionScore(
            "content_safety",
            not leaked,
            DIMENSION_WEIGHTS["content_safety"],
            f"forbidden phrasing for this archetype: {leaked}" if leaked else "clean",
        )
    )
    dimensions.append(
        DimensionScore(
            "compliance_review",
            result.review.passed,
            DIMENSION_WEIGHTS["compliance_review"],
            "; ".join(result.review.issues) if result.review.issues else f"{result.review.checks_run} checks passed",
        )
    )

    max_score = sum(d.weight for d in dimensions)
    score = sum(d.weight for d in dimensions if d.passed)
    return CaseResult(
        account_id=result.account_id,
        customer=result.customer.name,
        expected_archetype=expected_archetype,
        predicted_archetype=result.profile.archetype,
        relationship_label=result.sentiment.relationship_label,
        stage=decision.stage,
        tone=decision.tone,
        risk_score=result.profile.risk_score,
        steps=len(sequence.steps),
        score=score,
        max_score=max_score,
        passed=score == max_score,
        dimensions=dimensions,
    )


def run_eval(deps: GraphDependencies | None = None, account_ids: list[str] | None = None) -> EvalReport:
    deps = deps or GraphDependencies.build()
    customers = deps.repository.customers()
    if account_ids:
        customers = customers[customers["account_id"].isin(account_ids)]

    cases = [
        score_case(run_account(row["account_id"], deps), row["archetype"])
        for _, row in customers.iterrows()
    ]

    total = sum(c.score for c in cases)
    possible = sum(c.max_score for c in cases) or 1.0
    by_dimension = {
        name: round(
            sum(1 for c in cases for d in c.dimensions if d.name == name and d.passed) / max(1, len(cases)), 3
        )
        for name in DIMENSION_WEIGHTS
    }
    by_archetype = {
        archetype: round(
            sum(1 for c in cases if c.expected_archetype == archetype and c.passed)
            / max(1, sum(1 for c in cases if c.expected_archetype == archetype)),
            3,
        )
        for archetype in sorted({c.expected_archetype for c in cases})
    }

    return EvalReport(
        cases=cases,
        overall_score=round(total / possible, 4),
        pass_rate=round(sum(1 for c in cases if c.passed) / max(1, len(cases)), 4),
        by_dimension=by_dimension,
        by_archetype=by_archetype,
        llm_provider=f"{getattr(deps.llm, 'name', '?')}:{getattr(deps.llm, 'model', '?')}",
        sentiment_backend=getattr(deps.sentiment, "name", "?"),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
