"""Flight risk only when the relationship, the rating, tenure, and overtime all say so.

A stay conversation is a script for the manager. It is not sent anywhere.
"""

from __future__ import annotations

from ..agents.performance import money, rate_pct
from ..agents.sentiment import WITHDRAWING_PHRASES
from ..config import OT_HOURS_THRESHOLD, TENURE_MONTHS_MIN
from ..llm import LLMProvider, LLMRequest, STAY_MARKER, ensure_contains
from ..models import AttritionFinding, MetricSnapshot, Rating, Relationship, SentimentFinding, Teammate


def flight_risk_gates(
    sentiment: Relationship,
    rating: Rating,
    tenure_months: int,
    sustained_overtime: bool,
) -> dict[str, bool]:
    return {
        "adverse_sentiment": sentiment in ("strained", "withdrawing"),
        "solid_performer": rating in ("meets", "exceeds"),
        "tenured": tenure_months >= TENURE_MONTHS_MIN,
        "sustained_overtime": sustained_overtime,
    }


def is_flight_risk(gates: dict[str, bool]) -> bool:
    return all(gates.values())


def explain(
    gates: dict[str, bool],
    sentiment: Relationship,
    rating: Rating,
    tenure_months: int,
    high_weeks: int,
) -> list[str]:
    lines: list[str] = []
    if gates["adverse_sentiment"]:
        lines.append(f"Relationship with the work is {sentiment}.")
    else:
        lines.append(
            f"Relationship with the work is {sentiment}, which is not a flight-risk signal."
        )
    if gates["solid_performer"]:
        lines.append(f"The performance rating is {rating}, so the person is still delivering.")
    else:
        lines.append(
            f"The performance rating is {rating}, so this goes through the review rather than a stay conversation."
        )
    if gates["tenured"]:
        lines.append(f"Tenure is {tenure_months} months, which clears the {TENURE_MONTHS_MIN}-month gate.")
    else:
        lines.append(f"Tenure is {tenure_months} months, under the {TENURE_MONTHS_MIN}-month gate.")
    if gates["sustained_overtime"]:
        lines.append(
            f"Overtime was at or above {OT_HOURS_THRESHOLD:.0f} hours in {high_weeks} weeks."
        )
    else:
        lines.append(
            f"Overtime was at or above {OT_HOURS_THRESHOLD:.0f} hours in {high_weeks} weeks, "
            "so it is not sustained."
        )
    if all(gates.values()):
        lines.append("Flight risk is raised. A stay conversation is drafted for the manager and is not sent.")
    else:
        lines.append("Flight risk is not raised.")
    return lines


def supporting_quote(sentiment: SentimentFinding) -> str:
    def score(item: object) -> tuple[int, int]:
        phrases = item.matched_phrases  # type: ignore[attr-defined]
        withdrawing = sum(1 for phrase in phrases if phrase in WITHDRAWING_PHRASES)
        return withdrawing, len(phrases)

    ranked = sorted(sentiment.evidence, key=score, reverse=True)
    if not ranked:
        return ""
    return ranked[0].text


def assess_attrition(
    teammate: Teammate,
    snapshot: MetricSnapshot,
    sentiment: SentimentFinding,
    rating: Rating,
    llm: LLMProvider,
) -> AttritionFinding:
    gates = flight_risk_gates(
        sentiment.label,
        rating,
        teammate.tenure_months,
        snapshot.sustained_overtime,
    )
    risk = is_flight_risk(gates)
    reasons = explain(
        gates,
        sentiment.label,
        rating,
        teammate.tenure_months,
        snapshot.overtime_weeks_high,
    )
    if not risk:
        return AttritionFinding(
            flight_risk=False,
            gates=gates,
            reasons=reasons,
            stay_conversation=None,
            provider="rules",
        )

    quote = supporting_quote(sentiment)
    facts = {
        "name": teammate.name,
        "cash": money(snapshot.cash_applied),
        "promises": f"{snapshot.promises_kept} of {snapshot.promises_made}",
        "promise_pct": rate_pct(snapshot.promise_kept_rate),
        "quality": f"{snapshot.quality_score:.1f}",
        "ot_weeks": snapshot.overtime_weeks_high,
        "ot_avg": f"{snapshot.average_weekly_overtime:.1f}",
        "cases": snapshot.cases_closed,
        "expectation": snapshot.workload_expectation,
        "quote": quote,
    }
    prose = llm.complete(
        LLMRequest(
            task="stay_conversation",
            prompt=(
                f"Draft a stay conversation for the manager to use with {teammate.name}. "
                f"Do not send it. Use only these facts and this exact quote: {facts}"
            ),
            facts=facts,
        )
    )
    required = [
        facts["cash"],
        facts["promises"],
        facts["quality"],
        str(facts["ot_weeks"]),
        facts["ot_avg"],
        str(facts["cases"]),
        quote,
        STAY_MARKER,
    ]
    return AttritionFinding(
        flight_risk=True,
        gates=gates,
        reasons=reasons,
        stay_conversation=ensure_contains(prose, required),
        provider=llm.name,
    )
