"""Figures the performance rating is allowed to use. All of these are computed here."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from .config import (
    CASH_EXCEEDS_RATIO,
    CASH_MEETS_RATIO,
    CASH_TARGET,
    CORE_METRICS,
    CYCLE_EXCEEDS_DAYS,
    CYCLE_MEETS_DAYS,
    OT_HOURS_THRESHOLD,
    OT_WEEKS_REQUIRED,
    PROMISE_EXCEEDS,
    PROMISE_MEETS,
    QUALITY_EXCEEDS,
    QUALITY_MEETS,
    RAMP_CASH_FACTOR,
    RAMP_CYCLE_MEETS_DAYS,
    RAMP_QUALITY_MEETS,
    RAMP_TENURE_MONTHS,
    WORKLOAD_EXPECTATION,
)
from .models import (
    CashApplication,
    Dispute,
    MetricSnapshot,
    Promise,
    QAReview,
    Rating,
)

Band = Rating


def cash_applied(applications: Sequence[CashApplication]) -> int:
    return sum(item.amount for item in applications)


def promise_counts(promises: Sequence[Promise]) -> tuple[int, int, float]:
    made = len(promises)
    kept = sum(1 for item in promises if item.kept)
    rate = (kept / made) if made else 0.0
    return made, kept, rate


def dispute_cycle_days(disputes: Sequence[Dispute]) -> tuple[int, float]:
    """Mean days from open to close. Disputes still open are not in the average."""
    closed = [item for item in disputes if item.closed_on is not None]
    if not closed:
        return 0, 0.0
    days = [(item.closed_on - item.opened_on).days for item in closed]
    return len(closed), float(fmean(days))


def quality_score(reviews: Sequence[QAReview]) -> tuple[int, float]:
    if not reviews:
        return 0, 0.0
    return len(reviews), float(fmean(review.score for review in reviews))


def sustained_overtime(
    hours: Sequence[float],
    threshold: float = OT_HOURS_THRESHOLD,
    min_weeks: int = OT_WEEKS_REQUIRED,
) -> bool:
    return sum(1 for week in hours if week >= threshold) >= min_weeks


def cash_target_for(tenure_months: int) -> tuple[int, bool]:
    ramp = tenure_months < RAMP_TENURE_MONTHS
    factor = RAMP_CASH_FACTOR if ramp else 1.0
    return int(round(CASH_TARGET * factor)), ramp


def _higher(value: float, exceeds: float, meets: float) -> Band:
    if value >= exceeds:
        return "exceeds"
    if value >= meets:
        return "meets"
    return "below"


def _lower(value: float, exceeds: float, meets: float) -> Band:
    if value <= exceeds:
        return "exceeds"
    if value <= meets:
        return "meets"
    return "below"


def band_cash(amount: int, target: int) -> Band:
    if target <= 0:
        return "below"
    return _higher(amount / target, CASH_EXCEEDS_RATIO, CASH_MEETS_RATIO)


def band_promises(rate: float, made: int) -> Band:
    if made == 0:
        return "below"
    return _higher(rate, PROMISE_EXCEEDS, PROMISE_MEETS)


def band_cycle(days: float, closed: int, ramp: bool) -> Band:
    if closed == 0:
        return "below"
    meets = RAMP_CYCLE_MEETS_DAYS if ramp else CYCLE_MEETS_DAYS
    return _lower(days, CYCLE_EXCEEDS_DAYS, meets)


def band_quality(score: float, reviews: int, ramp: bool) -> Band:
    if reviews == 0:
        return "below"
    meets = RAMP_QUALITY_MEETS if ramp else QUALITY_MEETS
    return _higher(score, QUALITY_EXCEEDS, meets)


def overall_rating(bands: dict[str, Band]) -> Rating:
    """Exceeds needs three core measures and no miss. Two misses is below. Else meets.

    Workload is reported beside the rating and is not one of the four core measures.
    """
    core = [bands[name] for name in CORE_METRICS]
    below = sum(1 for band in core if band == "below")
    exceeds = sum(1 for band in core if band == "exceeds")
    if below >= 2:
        return "below"
    if exceeds >= 3 and below == 0:
        return "exceeds"
    return "meets"


def rating_reason(rating: Rating, bands: dict[str, Band]) -> str:
    core = [bands[name] for name in CORE_METRICS]
    exceeds = sum(1 for band in core if band == "exceeds")
    below = sum(1 for band in core if band == "below")
    return (
        f"{exceeds} of 4 core measures are exceeds and {below} of 4 are below, "
        f"so the quarter rating is {rating}."
    )


def cycle_meets_bar(ramp: bool) -> float:
    return RAMP_CYCLE_MEETS_DAYS if ramp else CYCLE_MEETS_DAYS


def quality_meets_bar(ramp: bool) -> float:
    return RAMP_QUALITY_MEETS if ramp else QUALITY_MEETS


def build_snapshot(
    *,
    tenure_months: int,
    cash: Sequence[CashApplication],
    promises: Sequence[Promise],
    disputes: Sequence[Dispute],
    reviews: Sequence[QAReview],
    overtime_hours: Sequence[float],
    cases_closed: int,
) -> MetricSnapshot:
    applied = cash_applied(cash)
    target, ramp = cash_target_for(tenure_months)
    made, kept, rate = promise_counts(promises)
    closed, cycle = dispute_cycle_days(disputes)
    review_count, score = quality_score(reviews)
    hours = [float(value) for value in overtime_hours]
    high_weeks = sum(1 for value in hours if value >= OT_HOURS_THRESHOLD)
    average = float(fmean(hours)) if hours else 0.0
    bands: dict[str, Band] = {
        "cash_applied": band_cash(applied, target),
        "promises_kept": band_promises(rate, made),
        "dispute_cycle": band_cycle(cycle, closed, ramp),
        "quality": band_quality(score, review_count, ramp),
    }
    return MetricSnapshot(
        cash_applied=applied,
        cash_target=target,
        cash_ratio=(applied / target) if target else 0.0,
        ramp=ramp,
        promises_made=made,
        promises_kept=kept,
        promise_kept_rate=rate,
        dispute_cycle_days=cycle,
        disputes_closed=closed,
        quality_score=score,
        quality_reviews=review_count,
        cases_closed=cases_closed,
        workload_expectation=WORKLOAD_EXPECTATION,
        overtime_hours=hours,
        average_weekly_overtime=average,
        overtime_weeks_high=high_weeks,
        sustained_overtime=sustained_overtime(hours),
        bands=bands,
    )
