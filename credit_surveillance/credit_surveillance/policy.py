"""Periodic-review actions.

Rules run in order: suspend, reduce, conditions, affirm. Anything left is
conditions, so a gray account is never affirmed. Thresholds cited in a memo
are the same constants this module compares against.
"""

from decimal import Decimal

from credit_surveillance.authority import SUSPEND_APPROVAL_THRESHOLD
from credit_surveillance.exposure import DRIFT_SIGNAL_DAYS
from credit_surveillance.formatting import days, money, ratio
from credit_surveillance.models import ExposureFacts, Recommendation

AFFIRM_MAX_UTILIZATION = Decimal("0.80")
AFFIRM_MAX_PAST_DUE_RATIO = Decimal("0.05")
AFFIRM_MAX_DRIFT_DAYS = DRIFT_SIGNAL_DAYS
AFFIRM_MAX_LATE_RATE = Decimal("0.20")

REDUCE_MIN_DRIFT_DAYS = Decimal("10")
REDUCE_MIN_LATE_RATE = Decimal("0.40")
REDUCE_MAX_PAST_DUE_RATIO = Decimal("0.25")
REDUCE_MAX_BROKEN_PROMISES = 1
REDUCE_BASE_PENALTY = Decimal("0.15")
REDUCE_DRIFT_STEP = Decimal("0.01")
REDUCE_LATE_WEIGHT = Decimal("0.10")
REDUCE_PENALTY_CAP = Decimal("0.40")
LIMIT_STEP = Decimal("1000")

SUSPEND_MIN_BROKEN = 2
SUSPEND_MIN_PAST_DUE_RATIO = Decimal("0.25")
SUSPEND_MIN_DRIFT_DAYS = Decimal("15")
SUSPEND_SEVERE_PAST_DUE_RATIO = Decimal("0.40")
SUSPEND_SEVERE_MAX_DPD = 60
SUSPEND_REPEAT_BROKEN = 3

CONDITIONS_TERMS_DAYS = 15
CONDITIONS_UTILIZATION_TARGET = Decimal("0.90")


def compute_reduced_limit(
    credit_limit: Decimal,
    payment_drift_days: Decimal,
    late_payment_rate: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return ``(proposed_limit, penalty_rate)``.

    Penalty is 15% plus 1% for each drift day past 10, plus 10% of the late
    rate, capped at 40%. The raw limit is then stepped down to the nearest
    thousand.
    """
    extra_days = payment_drift_days - REDUCE_MIN_DRIFT_DAYS
    if extra_days < 0:
        extra_days = Decimal("0")
    penalty = (
        REDUCE_BASE_PENALTY
        + (extra_days * REDUCE_DRIFT_STEP)
        + (late_payment_rate * REDUCE_LATE_WEIGHT)
    )
    if penalty > REDUCE_PENALTY_CAP:
        penalty = REDUCE_PENALTY_CAP
    penalty = ratio_value(penalty)
    raw = money_value(credit_limit * (Decimal("1") - penalty))
    stepped = money_value((raw // LIMIT_STEP) * LIMIT_STEP)
    if stepped >= credit_limit:
        stepped = money_value(credit_limit - LIMIT_STEP)
    if stepped < 0:
        stepped = money_value(Decimal("0"))
    return stepped, penalty


def decide(
    facts: ExposureFacts,
    requested_limit: Decimal | None = None,
) -> Recommendation:
    """Choose the periodic-review action from already-computed facts."""
    suspend_codes = _suspend_codes(facts)
    if suspend_codes:
        recommendation = _suspend(facts, suspend_codes)
    elif _is_reduce(facts):
        recommendation = _reduce(facts)
    else:
        condition_codes = _condition_codes(facts)
        if condition_codes:
            recommendation = _conditions(facts, condition_codes)
        elif _is_affirm(facts):
            recommendation = _affirm(facts)
        else:
            recommendation = _conditions(facts, ("conditions_outside_affirm_band",))

    if (
        requested_limit is not None
        and requested_limit > facts.credit_limit
        and recommendation.action == "affirm"
    ):
        return _increase(facts, money_value(requested_limit))
    return recommendation


def ratio_value(value: Decimal) -> Decimal:
    from credit_surveillance.formatting import q_ratio

    return q_ratio(value)


def money_value(value: Decimal) -> Decimal:
    from credit_surveillance.formatting import q_money

    return q_money(value)


def _suspend_codes(facts: ExposureFacts) -> tuple[str, ...]:
    codes: list[str] = []
    if (
        facts.broken_promise_count >= SUSPEND_MIN_BROKEN
        and facts.past_due_ratio >= SUSPEND_MIN_PAST_DUE_RATIO
        and facts.payment_drift_days >= SUSPEND_MIN_DRIFT_DAYS
    ):
        codes.append("suspend_broken_promises")
    if (
        facts.past_due_ratio >= SUSPEND_SEVERE_PAST_DUE_RATIO
        and facts.max_days_past_due >= SUSPEND_SEVERE_MAX_DPD
    ):
        codes.append("suspend_severe_delinquency")
    if facts.broken_promise_count >= SUSPEND_REPEAT_BROKEN:
        codes.append("suspend_repeated_promises")
    return tuple(codes)


def _is_reduce(facts: ExposureFacts) -> bool:
    return (
        facts.payment_drift_days >= REDUCE_MIN_DRIFT_DAYS
        and facts.late_payment_rate >= REDUCE_MIN_LATE_RATE
        and facts.past_due_ratio < REDUCE_MAX_PAST_DUE_RATIO
        and facts.broken_promise_count <= REDUCE_MAX_BROKEN_PROMISES
    )


def _condition_codes(facts: ExposureFacts) -> tuple[str, ...]:
    codes: list[str] = []
    if facts.over_limit_amount > 0:
        codes.append("conditions_over_limit")
    if facts.broken_promise_count >= 1:
        codes.append("conditions_broken_promise")
    return tuple(codes)


def _is_affirm(facts: ExposureFacts) -> bool:
    return (
        facts.utilization <= AFFIRM_MAX_UTILIZATION
        and facts.past_due_ratio <= AFFIRM_MAX_PAST_DUE_RATIO
        and facts.payment_drift_days <= AFFIRM_MAX_DRIFT_DAYS
        and facts.broken_promise_count == 0
        and facts.over_limit_amount == 0
        and facts.late_payment_rate <= AFFIRM_MAX_LATE_RATE
    )


def _account_figures(facts: ExposureFacts, proposed_limit: Decimal) -> dict[str, str]:
    return {
        "credit_limit": money(facts.credit_limit),
        "proposed_limit": money(proposed_limit),
        "accounts_receivable": money(facts.accounts_receivable),
        "current_ar": money(facts.current_ar),
        "past_due_ar": money(facts.past_due_ar),
        "past_due_ratio": ratio(facts.past_due_ratio),
        "open_orders": money(facts.open_orders),
        "exposure": money(facts.exposure),
        "over_limit_amount": money(facts.over_limit_amount),
        "utilization": ratio(facts.utilization),
        "terms_days": str(facts.terms_days),
        "avg_days_to_pay_baseline": days(facts.avg_days_to_pay_baseline),
        "avg_days_to_pay_recent": days(facts.avg_days_to_pay_recent),
        "payment_drift_days": days(facts.payment_drift_days),
        "late_payment_rate": ratio(facts.late_payment_rate),
        "invoices_paid_recent": str(facts.invoices_paid_recent),
        "invoices_late_recent": str(facts.invoices_late_recent),
        "broken_promise_count": str(facts.broken_promise_count),
        "broken_promise_amount": money(facts.broken_promise_amount),
        "max_days_past_due": str(facts.max_days_past_due),
    }


def _affirm(facts: ExposureFacts) -> Recommendation:
    cited = _account_figures(facts, facts.credit_limit)
    cited.update(
        {
            "affirm_max_utilization": ratio(AFFIRM_MAX_UTILIZATION),
            "affirm_max_past_due_ratio": ratio(AFFIRM_MAX_PAST_DUE_RATIO),
            "affirm_max_drift_days": days(AFFIRM_MAX_DRIFT_DAYS),
            "affirm_max_late_rate": ratio(AFFIRM_MAX_LATE_RATE),
        }
    )
    headline = (
        "Affirm the open account. "
        f"utilization={cited['utilization']} is within affirm_max_utilization={cited['affirm_max_utilization']}, "
        f"past_due_ratio={cited['past_due_ratio']} is within affirm_max_past_due_ratio={cited['affirm_max_past_due_ratio']}, "
        f"payment_drift_days={cited['payment_drift_days']} is within affirm_max_drift_days={cited['affirm_max_drift_days']}, "
        f"and late_payment_rate={cited['late_payment_rate']} is within affirm_max_late_rate={cited['affirm_max_late_rate']}. "
        f"over_limit_amount={cited['over_limit_amount']} and broken_promise_count={cited['broken_promise_count']}. "
        f"Leave proposed_limit={cited['proposed_limit']} equal to credit_limit={cited['credit_limit']}."
    )
    return Recommendation(
        action="affirm",
        rule_codes=("affirm_within_policy",),
        current_limit=facts.credit_limit,
        proposed_limit=facts.credit_limit,
        cited_figures=cited,
        conditions=(),
        headline=headline,
    )


def _reduce(facts: ExposureFacts) -> Recommendation:
    proposed, penalty = compute_reduced_limit(
        facts.credit_limit,
        facts.payment_drift_days,
        facts.late_payment_rate,
    )
    cited = _account_figures(facts, proposed)
    cited.update(
        {
            "penalty_rate": ratio(penalty),
            "reduce_min_drift_days": days(REDUCE_MIN_DRIFT_DAYS),
            "reduce_min_late_rate": ratio(REDUCE_MIN_LATE_RATE),
            "reduce_max_past_due_ratio": ratio(REDUCE_MAX_PAST_DUE_RATIO),
            "reduce_max_broken_promises": str(REDUCE_MAX_BROKEN_PROMISES),
        }
    )
    headline = (
        "Reduce the limit. Payment performance deteriorated while delinquency stays below the suspend bar. "
        f"payment_drift_days={cited['payment_drift_days']} versus reduce_min_drift_days={cited['reduce_min_drift_days']} "
        f"(avg_days_to_pay_recent={cited['avg_days_to_pay_recent']} against avg_days_to_pay_baseline={cited['avg_days_to_pay_baseline']}), "
        f"late_payment_rate={cited['late_payment_rate']} versus reduce_min_late_rate={cited['reduce_min_late_rate']}, "
        f"past_due_ratio={cited['past_due_ratio']} versus reduce_max_past_due_ratio={cited['reduce_max_past_due_ratio']}, "
        f"broken_promise_count={cited['broken_promise_count']} versus reduce_max_broken_promises={cited['reduce_max_broken_promises']}. "
        f"Apply penalty_rate={cited['penalty_rate']} to credit_limit={cited['credit_limit']} "
        f"and post proposed_limit={cited['proposed_limit']}."
    )
    return Recommendation(
        action="reduce",
        rule_codes=("reduce_payment_drift",),
        current_limit=facts.credit_limit,
        proposed_limit=proposed,
        cited_figures=cited,
        conditions=(),
        headline=headline,
    )


def _conditions(facts: ExposureFacts, codes: tuple[str, ...]) -> Recommendation:
    cited = _account_figures(facts, facts.credit_limit)
    cited.update(
        {
            "conditions_terms_days": str(CONDITIONS_TERMS_DAYS),
            "conditions_utilization_target": ratio(CONDITIONS_UTILIZATION_TARGET),
        }
    )
    lines: list[str] = []
    if "conditions_outside_affirm_band" in codes:
        lines.append(
            "Keep the limit. The account sits outside the affirm band on "
            f"utilization={cited['utilization']}, payment_drift_days={cited['payment_drift_days']}, "
            f"past_due_ratio={cited['past_due_ratio']}, late_payment_rate={cited['late_payment_rate']}."
        )
    if "conditions_over_limit" in codes:
        lines.append(
            "Hold releases that would leave "
            f"exposure={cited['exposure']} above credit_limit={cited['credit_limit']} "
            f"(over_limit_amount={cited['over_limit_amount']})."
        )
    if "conditions_broken_promise" in codes:
        lines.append(
            "Obtain a dated schedule for "
            f"broken_promise_amount={cited['broken_promise_amount']} "
            f"(broken_promise_count={cited['broken_promise_count']}) before releasing "
            f"open_orders={cited['open_orders']}."
        )
    if facts.past_due_ar > 0:
        lines.append(
            f"Collect past_due_ar={cited['past_due_ar']} before the next release."
        )
    lines.append(
        "Bill new invoices on Net "
        f"conditions_terms_days={cited['conditions_terms_days']} instead of Net "
        f"terms_days={cited['terms_days']} until utilization is at or below "
        f"conditions_utilization_target={cited['conditions_utilization_target']}."
    )
    headline = (
        "Add conditions and do not increase the limit. "
        f"proposed_limit={cited['proposed_limit']} stays equal to credit_limit={cited['credit_limit']}."
    )
    return Recommendation(
        action="conditions",
        rule_codes=codes,
        current_limit=facts.credit_limit,
        proposed_limit=facts.credit_limit,
        cited_figures=cited,
        conditions=tuple(lines),
        headline=headline,
    )


def _suspend(facts: ExposureFacts, codes: tuple[str, ...]) -> Recommendation:
    cited = _account_figures(facts, facts.credit_limit)
    if "suspend_broken_promises" in codes:
        cited["suspend_min_broken_promises"] = str(SUSPEND_MIN_BROKEN)
        cited["suspend_min_past_due_ratio"] = ratio(SUSPEND_MIN_PAST_DUE_RATIO)
        cited["suspend_min_drift_days"] = days(SUSPEND_MIN_DRIFT_DAYS)
    if "suspend_severe_delinquency" in codes:
        cited["suspend_severe_past_due_ratio"] = ratio(SUSPEND_SEVERE_PAST_DUE_RATIO)
        cited["suspend_severe_max_days_past_due"] = str(SUSPEND_SEVERE_MAX_DPD)
    if "suspend_repeated_promises" in codes:
        cited["suspend_repeat_broken_promises"] = str(SUSPEND_REPEAT_BROKEN)
    cited["suspend_approval_threshold"] = money(SUSPEND_APPROVAL_THRESHOLD)
    headline = (
        "Suspend the open account. Buying stops; the limit stays on file until a later review. "
        f"broken_promise_count={cited['broken_promise_count']}, "
        f"broken_promise_amount={cited['broken_promise_amount']}, "
        f"past_due_ratio={cited['past_due_ratio']}, "
        f"max_days_past_due={cited['max_days_past_due']}, "
        f"payment_drift_days={cited['payment_drift_days']}, "
        f"exposure={cited['exposure']}."
    )
    return Recommendation(
        action="suspend",
        rule_codes=codes,
        current_limit=facts.credit_limit,
        proposed_limit=facts.credit_limit,
        cited_figures=cited,
        conditions=(),
        headline=headline,
    )


def _increase(facts: ExposureFacts, requested_limit: Decimal) -> Recommendation:
    cited = _account_figures(facts, requested_limit)
    cited["requested_limit"] = money(requested_limit)
    headline = (
        "The account is inside the affirm band and a higher limit was requested. "
        f"Move credit_limit={cited['credit_limit']} to proposed_limit={cited['proposed_limit']} "
        f"(requested_limit={cited['requested_limit']}) only after a named credit manager approves it. "
        f"utilization={cited['utilization']}, past_due_ratio={cited['past_due_ratio']}, "
        f"payment_drift_days={cited['payment_drift_days']}, "
        f"late_payment_rate={cited['late_payment_rate']}, "
        f"broken_promise_count={cited['broken_promise_count']}, "
        f"over_limit_amount={cited['over_limit_amount']}."
    )
    return Recommendation(
        action="increase",
        rule_codes=("increase_requested_within_policy",),
        current_limit=facts.credit_limit,
        proposed_limit=requested_limit,
        cited_figures=cited,
        conditions=(),
        headline=headline,
    )
