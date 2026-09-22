"""Deterministic offline provider.

This is the default. It answers every task the agents issue, using only the facts
in the request, so the whole system runs with no API key, no network, and no
run-to-run variation. Where a real model would exercise judgment -- classifying a
news story, phrasing an interpretation -- this provider applies an explicit rule:
the severity lexicon in :mod:`.lexicon` for classification, and a phrasebook for
prose.

It is a stand-in, not a simulation of a language model. The value is that the
pipeline, the citations, and the completeness checks are all exercised for real,
and swapping in :class:`~.openai_provider.OpenAIProvider` changes only the
quality of the wording and the classification judgment.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Direction
from .base import LLMRequest, LLMResponse, UnsupportedTaskError
from .lexicon import classify

TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]

PROVIDER_NAME = "offline"

# --------------------------------------------------------------------------------------
# Phrasebook
# --------------------------------------------------------------------------------------

_TOPIC_LEADIN: dict[tuple[str, str], str] = {
    ("liquidity", "supportive"): "Liquidity is a clear strength",
    ("liquidity", "mixed"): "Liquidity is adequate but leaves little headroom",
    ("liquidity", "adverse"): "Liquidity is the binding constraint on this application",
    ("leverage", "supportive"): "The capital structure is conservative",
    ("leverage", "mixed"): "Leverage is tolerable but rising",
    ("leverage", "adverse"): "Leverage is the principal financial weakness",
    ("coverage", "supportive"): "Debt service is comfortably covered",
    ("coverage", "mixed"): "Coverage of fixed charges is adequate but thin",
    ("coverage", "adverse"): "Earnings do not reliably cover fixed charges",
    ("profitability", "supportive"): "Margins are healthy for the sector",
    ("profitability", "mixed"): "Margins are serviceable but compressing",
    ("profitability", "adverse"): "Margins leave no absorption for a cost shock",
    ("trend", "supportive"): "The direction of travel is favourable",
    ("trend", "mixed"): "The trend picture is mixed",
    ("trend", "adverse"): "The trend picture is the most concerning part of the spread",
    ("cash_flow", "supportive"): "Cash generation is self-funding",
    ("cash_flow", "mixed"): "Cash generation covers operations but not growth",
    ("cash_flow", "adverse"): "The business is consuming cash",
    ("structure", "supportive"): "The balance sheet carries real loss-absorbing capital",
    ("structure", "mixed"): "Capital is thin relative to the balance sheet",
    ("structure", "adverse"): "There is no meaningful equity cushion",
    ("data_quality", "supportive"): "The submitted package is reliable",
    ("data_quality", "mixed"): "The submitted package has limitations",
    ("data_quality", "adverse"): "The submitted package is not independently verified",
    ("rating", "supportive"): "The scorecard places the applicant in the upper bands",
    ("rating", "mixed"): "The scorecard places the applicant mid-scale",
    ("rating", "adverse"): "The scorecard places the applicant in the weakest bands",
}

_RISK_TOPIC_LEADIN: dict[str, str] = {
    "search_coverage": "External research coverage",
    "adverse_signals": "Adverse external findings",
    "supportive_signals": "Supportive external findings",
    "concentration": "Customer concentration",
    "country_and_industry": "Country and industry context",
    "risk_conclusion": "External risk conclusion",
}


def _leadin(topic: str, polarity: str) -> str:
    return _TOPIC_LEADIN.get((topic, polarity), _TOPIC_LEADIN.get((topic, "mixed"), topic.title()))


def _join_sentences(parts: list[str]) -> str:
    cleaned = [p.strip() for p in parts if p and p.strip()]
    out: list[str] = []
    for part in cleaned:
        out.append(part if part.endswith((".", "!", "?")) else part + ".")
    return " ".join(out)


# --------------------------------------------------------------------------------------
# Task handlers
# --------------------------------------------------------------------------------------


def _financial_narrative(facts: dict[str, Any]) -> dict[str, Any]:
    points: list[dict[str, str]] = []
    for topic in facts.get("topics", []):
        name = str(topic.get("topic", "analysis"))
        polarity = str(topic.get("polarity", "mixed"))
        metrics = topic.get("metrics", [])
        metric_clause = ", ".join(
            f"{m['label']} of {m['display']}" for m in metrics if m.get("display")
        )
        head = _leadin(name, polarity)
        first = f"{head}: {metric_clause}" if metric_clause else head
        points.append(
            {
                "topic": name,
                "text": _join_sentences([first, *topic.get("observations", [])]),
            }
        )
    return {"points": points}


def _risk_classification(facts: dict[str, Any]) -> dict[str, Any]:
    text = str(facts.get("text", ""))
    doc_type = str(facts.get("doc_type", "news"))
    result = classify(text, doc_type)

    first_sentence = text.split(". ")[0].strip()
    if first_sentence and not first_sentence.endswith("."):
        first_sentence += "."
    summary = first_sentence or str(facts.get("title", ""))

    rationale_parts = [
        f"Filed as {result.category.value} risk at {result.severity.value} severity"
    ]
    if result.matched_phrases:
        shown = ", ".join(f"'{p}'" for p in result.matched_phrases[:4])
        rationale_parts.append(f"on the language {shown} in {facts.get('source', 'the source')}")
    if result.mitigated:
        rationale_parts.append("reduced one level because the document records the matter as resolved")
    if result.capped_by_doc_type:
        rationale_parts.append(
            f"capped because a {doc_type.replace('_', ' ')} describes the sector or jurisdiction "
            "rather than the obligor"
        )
    if result.direction is Direction.SUPPORTIVE:
        rationale_parts = [
            f"No adverse language found; filed as supportive {result.category.value} context"
        ]

    return {
        "category": result.category.value,
        "severity": result.severity.value,
        "direction": result.direction.value,
        "summary": summary,
        "rationale": _join_sentences([", ".join(rationale_parts)]),
    }


def _risk_synthesis(facts: dict[str, Any]) -> dict[str, Any]:
    points: list[dict[str, str]] = []
    for topic in facts.get("topics", []):
        name = str(topic.get("topic", "risk"))
        head = _RISK_TOPIC_LEADIN.get(name, name.replace("_", " ").title())
        body = topic.get("observations", [])
        points.append({"topic": name, "text": _join_sentences([f"{head}: {body[0]}" if body else head, *body[1:]])})
    return {"points": points}


def _conflict_rationale(facts: dict[str, Any]) -> dict[str, Any]:
    rationales: list[dict[str, str]] = []
    for conflict in facts.get("conflicts", []):
        side = conflict.get("prevailing_side", "policy")
        if side == "risk":
            frame = (
                "The external evidence is more recent and more specific than the submitted "
                "statements, so it governs"
            )
        elif side == "financial":
            frame = (
                "The submitted spread is audited and period-specific while the external signal is "
                "systemic, so the spread governs"
            )
        elif side == "both":
            frame = "Both views survive because they describe different exposures"
        else:
            frame = "Policy settles the disagreement independently of either specialist"
        rationales.append(
            {
                "key": str(conflict.get("key", "")),
                "rationale": _join_sentences(
                    [
                        frame,
                        f"Rule applied: {conflict.get('rule', 'unspecified')}",
                        str(conflict.get("resolution", "")),
                    ]
                ),
            }
        )
    return {"rationales": rationales}


_MEMO_TEMPLATES: dict[str, str] = {
    "recommendation": (
        "{recommendation} a {currency_limit} facility on net {terms} day terms at internal "
        "grade {grade} ({band}), against a request for {currency_requested} on net "
        "{requested_terms} day terms."
    ),
    "rating_basis": (
        "The scorecard returns a composite of {score} out of 100, placing the applicant at "
        "standalone grade {standalone_grade}; after risk adjustment of {notches} notches the "
        "final grade is {grade} ({band}), an estimated {pd}% probability of default."
    ),
    "limit_basis": (
        "The limit is set by the {binding_constraint} capacity test at {capacity}, scaled by the "
        "grade {grade} multiplier of {multiplier} and reduced by a {haircut}% risk haircut, "
        "giving {limit}."
    ),
    "conflict": (
        "The specialists disagreed on {subject}: the financial analyst found {financial_position} "
        "while the risk searcher found {risk_position}. Applying {rule}, {resolution}"
    ),
}


def _memo_section(facts: dict[str, Any]) -> dict[str, Any]:
    claims: list[dict[str, str]] = []
    for point in facts.get("points", []):
        kind = str(point.get("kind", "restate"))
        template = _MEMO_TEMPLATES.get(kind)
        if template is not None:
            text = template.format(**point.get("fields", {}))
        else:
            text = str(point.get("text", "")).strip()
        if not text:
            continue
        claims.append({"point_id": str(point["point_id"]), "text": _join_sentences([text])})
    return {"claims": claims}


TASK_HANDLERS: dict[str, TaskHandler] = {
    "financial_narrative": _financial_narrative,
    "risk_classification": _risk_classification,
    "risk_synthesis": _risk_synthesis,
    "conflict_rationale": _conflict_rationale,
    "memo_section": _memo_section,
}


class OfflineProvider:
    """Rule-based provider used whenever no real model is configured."""

    def __init__(self, model: str = "offline-underwriter-v1") -> None:
        self.name = PROVIDER_NAME
        self.model = model

    def generate(self, request: LLMRequest) -> LLMResponse:
        handler = TASK_HANDLERS.get(request.task)
        if handler is None:
            raise UnsupportedTaskError(request.task, self.name)
        output = handler(request.facts)
        notes = ["deterministic offline provider; no network call made"]
        if request.facts.get("prior_issues"):
            notes.append(
                f"revision pass addressing {len(request.facts['prior_issues'])} completeness issues"
            )
        return LLMResponse(
            task=request.task,
            provider=self.name,
            model=self.model,
            output=output,
            notes=notes,
        )
