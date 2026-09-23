"""Performance review. The rating is computed. The narrative has to cite the figures."""

from __future__ import annotations

from ..config import (
    CYCLE_EXCEEDS_DAYS,
    PROMISE_EXCEEDS,
    PROMISE_MEETS,
    QUALITY_EXCEEDS,
)
from ..llm import LLMProvider, LLMRequest, ensure_contains
from ..metrics import cycle_meets_bar, overall_rating, quality_meets_bar, rating_reason
from ..models import Judgment, MetricSnapshot, PerformanceReview, Rating, Teammate


def money(amount: int) -> str:
    return f"${amount:,}"


def rate_pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def build_judgments(snapshot: MetricSnapshot) -> list[Judgment]:
    cash_bar = "ramp bar" if snapshot.ramp else "bar"
    cycle_meets = cycle_meets_bar(snapshot.ramp)
    quality_meets = quality_meets_bar(snapshot.ramp)
    quality_bar = "ramp meets bar" if snapshot.ramp else "meets bar"
    return [
        Judgment(
            metric="cash_applied",
            band=snapshot.bands["cash_applied"],
            sentence=(
                f"Cash applied was {money(snapshot.cash_applied)} against a "
                f"{money(snapshot.cash_target)} {cash_bar} "
                f"({snapshot.cash_ratio:.0%} of that bar): {snapshot.bands['cash_applied']}."
            ),
        ),
        Judgment(
            metric="promises_kept",
            band=snapshot.bands["promises_kept"],
            sentence=(
                f"Promises kept were {snapshot.promises_kept} of {snapshot.promises_made} "
                f"({rate_pct(snapshot.promise_kept_rate)}): {snapshot.bands['promises_kept']}. "
                f"The meets bar is {rate_pct(PROMISE_MEETS)} and the exceeds bar is {rate_pct(PROMISE_EXCEEDS)}."
            ),
        ),
        Judgment(
            metric="dispute_cycle",
            band=snapshot.bands["dispute_cycle"],
            sentence=(
                f"Dispute cycle time averaged {snapshot.dispute_cycle_days:.1f} days across "
                f"{snapshot.disputes_closed} closed disputes: {snapshot.bands['dispute_cycle']}. "
                f"Exceeds is {CYCLE_EXCEEDS_DAYS:.0f} days or fewer; meets is {cycle_meets:.0f} days or fewer."
            ),
        ),
        Judgment(
            metric="quality",
            band=snapshot.bands["quality"],
            sentence=(
                f"Quality score averaged {snapshot.quality_score:.1f} across "
                f"{snapshot.quality_reviews} QA reviews: {snapshot.bands['quality']}. "
                f"The {quality_bar} is {quality_meets:.0f} and the exceeds bar is {QUALITY_EXCEEDS:.0f}."
            ),
        ),
    ]


def workload_sentence(snapshot: MetricSnapshot) -> str:
    base = (
        f"Workload was {snapshot.cases_closed} cases closed against a "
        f"{snapshot.workload_expectation}-case quarter expectation. "
        f"Average weekly overtime was {snapshot.average_weekly_overtime:.1f} hours."
    )
    if snapshot.sustained_overtime:
        return base + " The extra volume is not scored as outperformance."
    if snapshot.cases_closed < snapshot.workload_expectation and not snapshot.ramp:
        return (
            base
            + " The book was lighter than the quarter expectation, so the result is not "
            "explained by an unusually heavy queue."
        )
    if snapshot.ramp:
        return base + " The cash bar for this tenure is the ramp bar, not the full book."
    return base + " Workload is cited here and is not a fifth score."


def cited_figures(snapshot: MetricSnapshot) -> list[str]:
    return [
        money(snapshot.cash_applied),
        f"{snapshot.promises_kept} of {snapshot.promises_made}",
        f"{snapshot.dispute_cycle_days:.1f} days",
        f"{snapshot.quality_score:.1f}",
        f"{snapshot.cases_closed} cases",
    ]


def draft_performance(
    teammate: Teammate,
    snapshot: MetricSnapshot,
    period: str,
    llm: LLMProvider,
) -> PerformanceReview:
    rating: Rating = overall_rating(snapshot.bands)
    judgments = build_judgments(snapshot)
    reason = rating_reason(rating, snapshot.bands)
    workload = workload_sentence(snapshot)
    facts = {
        "name": teammate.name,
        "role": teammate.role,
        "period": period,
        "rating": rating,
        "cash": money(snapshot.cash_applied),
        "promises": f"{snapshot.promises_kept} of {snapshot.promises_made}",
        "promise_pct": rate_pct(snapshot.promise_kept_rate),
        "cycle": f"{snapshot.dispute_cycle_days:.1f} days",
        "quality": f"{snapshot.quality_score:.1f}",
        "cases": snapshot.cases_closed,
    }
    opening = llm.complete(
        LLMRequest(
            task="review_opening",
            prompt=(
                f"Write a short opening for {teammate.name}'s {period} review. "
                f"The rating is already {rating}. Cite these figures and do not change the rating: "
                f"{facts}."
            ),
            facts=facts,
        )
    )
    closing = f"This draft is not visible to HR or to {teammate.name} until you confirm the rating."
    narrative = "\n\n".join(
        [opening.strip(), reason, *[item.sentence for item in judgments], workload, closing]
    )
    narrative = ensure_contains(narrative, cited_figures(snapshot))
    return PerformanceReview(
        rating=rating,
        reason=reason,
        judgments=judgments,
        narrative=narrative,
        provider=llm.name,
    )
