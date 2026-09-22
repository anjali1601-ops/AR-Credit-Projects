"""Internal rating scorecard and credit-limit capacity test.

The scorecard is a weighted set of factors, each mapped from its raw value to a
0-100 sub-score by piecewise-linear interpolation over published breakpoints. The
weighted sum lands in a rating band. All of it is arithmetic: given the same
statements, the same grade comes out every time, and each factor records the
evidence ids behind its raw value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..evidence import EvidenceRegistry
from ..models import (
    CreditApplication,
    EvidenceKind,
    FinancialStatement,
    InternalRating,
    LimitGuidance,
    Ratio,
    ScorecardFactor,
    Trend,
    TrendDirection,
    format_currency,
    format_ratio_value,
)
from .ratios import find_ratio, ratio_evidence_id
from .spreads import statement_line_id

Breakpoints = Sequence[tuple[float, float]]

# --------------------------------------------------------------------------------------
# Scorecard definition
# --------------------------------------------------------------------------------------

LEVERAGE_BREAKPOINTS: Breakpoints = (
    (0.5, 100),
    (1.5, 88),
    (2.5, 74),
    (3.5, 58),
    (4.5, 42),
    (6.0, 24),
    (8.0, 10),
    (12.0, 0),
)
COVERAGE_BREAKPOINTS: Breakpoints = (
    (0.0, 0),
    (1.0, 8),
    (1.5, 20),
    (2.5, 40),
    (4.0, 58),
    (6.0, 74),
    (10.0, 88),
    (15.0, 100),
)
CURRENT_RATIO_BREAKPOINTS: Breakpoints = (
    (0.0, 0),
    (0.8, 8),
    (1.0, 25),
    (1.2, 42),
    (1.5, 60),
    (2.0, 80),
    (2.5, 92),
    (3.0, 100),
)
EBITDA_MARGIN_BREAKPOINTS: Breakpoints = (
    (0.0, 0),
    (2.0, 8),
    (4.0, 20),
    (6.0, 35),
    (9.0, 52),
    (12.0, 70),
    (16.0, 86),
    (20.0, 100),
)
EQUITY_RATIO_BREAKPOINTS: Breakpoints = (
    (0.0, 0),
    (10.0, 20),
    (20.0, 40),
    (30.0, 58),
    (40.0, 75),
    (50.0, 88),
    (60.0, 100),
)
REVENUE_SCALE_BREAKPOINTS: Breakpoints = (
    (0.0, 0),
    (5_000_000.0, 20),
    (15_000_000.0, 40),
    (30_000_000.0, 55),
    (50_000_000.0, 70),
    (100_000_000.0, 85),
    (250_000_000.0, 100),
)
TENURE_BREAKPOINTS: Breakpoints = (
    (0.0, 0),
    (2.0, 10),
    (4.0, 30),
    (7.0, 50),
    (10.0, 65),
    (15.0, 80),
    (20.0, 95),
    (25.0, 100),
)

FACTOR_WEIGHTS: dict[str, float] = {
    "leverage": 0.22,
    "coverage": 0.18,
    "liquidity": 0.15,
    "profitability": 0.15,
    "trend": 0.12,
    "balance_sheet_strength": 0.10,
    "scale_and_tenure": 0.08,
}

#: Trends that feed the composite trend factor.
TREND_FACTOR_KEYS: tuple[str, ...] = (
    "revenue",
    "ebitda_margin",
    "net_debt_to_ebitda",
    "ebitda_interest_coverage",
    "free_cash_flow",
)

RATING_BANDS: tuple[tuple[float, int, str], ...] = (
    (88.0, 1, "Exceptional"),
    (78.0, 2, "Strong"),
    (68.0, 3, "Good"),
    (58.0, 4, "Satisfactory"),
    (48.0, 5, "Acceptable"),
    (38.0, 6, "Watch"),
    (29.0, 7, "Substandard"),
    (21.0, 8, "Weak"),
    (13.0, 9, "Doubtful"),
    (0.0, 10, "Impaired"),
)

BAND_LABELS: dict[int, str] = {grade: label for _, grade, label in RATING_BANDS}

ESTIMATED_PD_PERCENT: dict[int, float] = {
    1: 0.05,
    2: 0.12,
    3: 0.28,
    4: 0.55,
    5: 1.10,
    6: 2.40,
    7: 5.50,
    8: 11.00,
    9: 22.00,
    10: 45.00,
}

#: Grade 1 is reserved for obligors of investment-grade scale. A sub-scale private
#: company can score into band 1 on ratios alone, so policy floors it at grade 2.
GRADE_1_MIN_REVENUE = 250_000_000.0

# --------------------------------------------------------------------------------------
# Limit policy
# --------------------------------------------------------------------------------------

TANGIBLE_NET_WORTH_ADVANCE = 0.10
EBITDA_ADVANCE = 0.25
WORKING_CAPITAL_ADVANCE = 0.35

RATING_LIMIT_MULTIPLIER: dict[int, float] = {
    1: 1.00,
    2: 1.00,
    3: 0.85,
    4: 0.70,
    5: 0.55,
    6: 0.30,
    7: 0.15,
    8: 0.00,
    9: 0.00,
    10: 0.00,
}

#: Maximum open-account terms by grade. ``0`` means no open account.
RATING_MAX_TERMS_DAYS: dict[int, int] = {
    1: 90,
    2: 75,
    3: 60,
    4: 45,
    5: 30,
    6: 30,
    7: 15,
    8: 0,
    9: 0,
    10: 0,
}

REVIEW_FREQUENCY_MONTHS: dict[int, int] = {
    1: 12,
    2: 12,
    3: 12,
    4: 6,
    5: 6,
    6: 3,
    7: 3,
    8: 1,
    9: 1,
    10: 1,
}

LIMIT_ROUNDING = 5_000.0


def interpolate_score(value: float, breakpoints: Breakpoints) -> float:
    """Piecewise-linear map from a raw metric to a 0-100 sub-score.

    ``breakpoints`` is ordered by ascending threshold; the score column may
    ascend (higher is better) or descend (lower is better).
    """
    first_threshold, first_score = breakpoints[0]
    last_threshold, last_score = breakpoints[-1]
    if value <= first_threshold:
        return float(first_score)
    if value >= last_threshold:
        return float(last_score)
    for (low_t, low_s), (high_t, high_s) in zip(breakpoints, breakpoints[1:], strict=False):
        if low_t <= value <= high_t:
            span = high_t - low_t
            if span <= 0:
                return float(high_s)
            ratio = (value - low_t) / span
            return float(low_s + ratio * (high_s - low_s))
    return float(last_score)


def band_for_score(score: float) -> tuple[int, str]:
    for threshold, grade, label in RATING_BANDS:
        if score >= threshold:
            return grade, label
    return 10, BAND_LABELS[10]


@dataclass(frozen=True)
class _FactorInput:
    key: str
    label: str
    unit: str
    raw_value: float | None
    breakpoints: Breakpoints
    inputs: list[str]
    not_meaningful_reason: str | None = None


def _trend_factor_score(trends: list[Trend]) -> tuple[float, list[str], str]:
    relevant = [t for t in trends if t.key in TREND_FACTOR_KEYS]
    if not relevant:
        return 50.0, [], "no trend data"
    net = sum(
        1
        if t.direction is TrendDirection.IMPROVING
        else -1
        if t.direction is TrendDirection.DETERIORATING
        else 0
        for t in relevant
    )
    score = 50.0 + (net / len(relevant)) * 50.0
    improving = sum(1 for t in relevant if t.direction is TrendDirection.IMPROVING)
    deteriorating = sum(1 for t in relevant if t.direction is TrendDirection.DETERIORATING)
    label = f"{improving} improving / {deteriorating} deteriorating of {len(relevant)} tracked trends"
    return score, [f"trend:{t.key}" for t in relevant], label


def assign_rating(
    application: CreditApplication,
    ratios: list[Ratio],
    trends: list[Trend],
    registry: EvidenceRegistry | None = None,
) -> InternalRating:
    latest = application.latest_statement
    period = latest.period_label

    factor_inputs: list[_FactorInput] = []

    leverage = find_ratio(ratios, "net_debt_to_ebitda", period)
    factor_inputs.append(
        _FactorInput(
            "leverage",
            "Leverage (net debt / EBITDA)",
            "x",
            leverage.value if leverage else None,
            LEVERAGE_BREAKPOINTS,
            [ratio_evidence_id(period, "net_debt_to_ebitda")],
            leverage.not_meaningful_reason if leverage else "ratio unavailable",
        )
    )

    coverage = find_ratio(ratios, "ebitda_interest_coverage", period)
    factor_inputs.append(
        _FactorInput(
            "coverage",
            "Coverage (EBITDA / interest)",
            "x",
            coverage.value if coverage else None,
            COVERAGE_BREAKPOINTS,
            [ratio_evidence_id(period, "ebitda_interest_coverage")],
            coverage.not_meaningful_reason if coverage else "ratio unavailable",
        )
    )

    liquidity = find_ratio(ratios, "current_ratio", period)
    factor_inputs.append(
        _FactorInput(
            "liquidity",
            "Liquidity (current ratio)",
            "x",
            liquidity.value if liquidity else None,
            CURRENT_RATIO_BREAKPOINTS,
            [ratio_evidence_id(period, "current_ratio")],
            liquidity.not_meaningful_reason if liquidity else "ratio unavailable",
        )
    )

    margin = find_ratio(ratios, "ebitda_margin", period)
    factor_inputs.append(
        _FactorInput(
            "profitability",
            "Profitability (EBITDA margin)",
            "%",
            margin.value if margin else None,
            EBITDA_MARGIN_BREAKPOINTS,
            [ratio_evidence_id(period, "ebitda_margin")],
            margin.not_meaningful_reason if margin else "ratio unavailable",
        )
    )

    equity_ratio = find_ratio(ratios, "equity_ratio", period)
    factor_inputs.append(
        _FactorInput(
            "balance_sheet_strength",
            "Balance sheet strength (equity / assets)",
            "%",
            equity_ratio.value if equity_ratio else None,
            EQUITY_RATIO_BREAKPOINTS,
            [ratio_evidence_id(period, "equity_ratio")],
            equity_ratio.not_meaningful_reason if equity_ratio else "ratio unavailable",
        )
    )

    factors: list[ScorecardFactor] = []
    for fi in factor_inputs:
        if fi.raw_value is None:
            score = 0.0
            band_label = f"not meaningful — {fi.not_meaningful_reason or 'unavailable'}; scored 0"
        else:
            score = interpolate_score(fi.raw_value, fi.breakpoints)
            band_label = f"{format_ratio_value(fi.raw_value, fi.unit)} scores {score:.0f}/100"
        factors.append(
            ScorecardFactor(
                key=fi.key,
                label=fi.label,
                weight=FACTOR_WEIGHTS[fi.key],
                raw_value=fi.raw_value,
                unit=fi.unit,  # type: ignore[arg-type]
                score=score,
                band_label=band_label,
                inputs=fi.inputs,
            )
        )

    trend_score, trend_inputs, trend_label = _trend_factor_score(trends)
    factors.append(
        ScorecardFactor(
            key="trend",
            label="Performance trend",
            weight=FACTOR_WEIGHTS["trend"],
            raw_value=trend_score,
            unit="%",
            score=trend_score,
            band_label=trend_label,
            inputs=trend_inputs,
        )
    )

    revenue = latest.income_statement.revenue
    revenue_score = interpolate_score(revenue, REVENUE_SCALE_BREAKPOINTS)
    tenure_score = interpolate_score(application.years_in_business, TENURE_BREAKPOINTS)
    scale_score = (revenue_score + tenure_score) / 2
    factors.append(
        ScorecardFactor(
            key="scale_and_tenure",
            label="Scale and operating history",
            weight=FACTOR_WEIGHTS["scale_and_tenure"],
            raw_value=revenue,
            unit="currency",
            score=scale_score,
            band_label=(
                f"{format_currency(revenue, latest.currency)} revenue and "
                f"{application.years_in_business:.0f} years trading"
            ),
            inputs=[
                statement_line_id(period, "income_statement", "revenue"),
                "app:years_in_business",
            ],
        )
    )

    composite = sum(f.weighted_score for f in factors)
    grade, band_label = band_for_score(composite)
    if grade == 1 and revenue < GRADE_1_MIN_REVENUE:
        grade, band_label = 2, BAND_LABELS[2]

    rating = InternalRating(
        grade=grade,
        band_label=band_label,
        composite_score=round(composite, 2),
        estimated_pd_percent=ESTIMATED_PD_PERCENT[grade],
        factors=factors,
    )

    if registry is not None:
        registry.register(
            "rating:standalone",
            EvidenceKind.RATING,
            "Standalone internal rating (financial scorecard)",
            source=f"Scorecard applied to {period} spread",
            display_value=f"Grade {grade} ({band_label}), composite score {composite:.1f}/100",
            detail="; ".join(
                f"{f.label} weight {f.weight:.0%} score {f.score:.0f}" for f in factors
            ),
            numeric_values=[float(grade), round(composite, 2), ESTIMATED_PD_PERCENT[grade]],
        )
        for factor in factors:
            registry.register(
                f"rating:factor:{factor.key}",
                EvidenceKind.RATING,
                f"Scorecard factor — {factor.label}",
                source=f"Scorecard applied to {period} spread",
                display_value=factor.band_label,
                numeric_values=[
                    v for v in (factor.raw_value, factor.score) if v is not None
                ],
            )

    return rating


def _capacity(value: float, advance: float) -> float:
    """Advance rates never produce negative capacity."""
    return max(0.0, value * advance)


def round_limit(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(int(value / LIMIT_ROUNDING) * LIMIT_ROUNDING)


def recommend_limit(
    application: CreditApplication,
    rating: InternalRating,
    registry: EvidenceRegistry | None = None,
) -> LimitGuidance:
    """Capacity test on the standalone rating, before any risk adjustment."""
    latest: FinancialStatement = application.latest_statement
    period = latest.period_label
    bs = latest.balance_sheet

    tnw_capacity = _capacity(bs.tangible_net_worth, TANGIBLE_NET_WORTH_ADVANCE)
    cash_flow_capacity = _capacity(latest.income_statement.ebitda, EBITDA_ADVANCE)
    working_capital_capacity = _capacity(bs.working_capital, WORKING_CAPITAL_ADVANCE)

    candidates = {
        "tangible net worth": tnw_capacity,
        "cash flow": cash_flow_capacity,
        "working capital": working_capital_capacity,
    }
    binding_constraint = min(candidates, key=lambda k: candidates[k])
    base_capacity = candidates[binding_constraint]

    multiplier = RATING_LIMIT_MULTIPLIER[rating.grade]
    indicative = round_limit(min(application.requested_limit, base_capacity * multiplier))
    terms_cap = RATING_MAX_TERMS_DAYS[rating.grade]
    indicative_terms = min(application.requested_terms_days, terms_cap)

    guidance = LimitGuidance(
        requested_limit=application.requested_limit,
        tangible_net_worth_capacity=tnw_capacity,
        cash_flow_capacity=cash_flow_capacity,
        working_capital_capacity=working_capital_capacity,
        binding_constraint=binding_constraint,
        rating_multiplier=multiplier,
        indicative_limit=indicative,
        indicative_terms_days=indicative_terms,
        inputs=[
            statement_line_id(period, "balance_sheet", "tangible_net_worth"),
            statement_line_id(period, "income_statement", "ebitda"),
            statement_line_id(period, "balance_sheet", "working_capital"),
            "rating:standalone",
            "app:requested_limit",
        ],
    )

    if registry is not None:
        registry.register(
            "policy:limit_capacity",
            EvidenceKind.POLICY_RULE,
            "Credit limit capacity test",
            source="Underwriting policy — limit capacity test",
            display_value=(
                f"Binding constraint is {binding_constraint} at "
                f"{format_currency(base_capacity, application.currency)}; grade "
                f"{rating.grade} multiplier {multiplier:.2f} gives an indicative limit of "
                f"{format_currency(indicative, application.currency)}"
            ),
            detail=(
                f"{TANGIBLE_NET_WORTH_ADVANCE:.0%} of tangible net worth = "
                f"{format_currency(tnw_capacity, application.currency)}; "
                f"{EBITDA_ADVANCE:.0%} of EBITDA = "
                f"{format_currency(cash_flow_capacity, application.currency)}; "
                f"{WORKING_CAPITAL_ADVANCE:.0%} of working capital = "
                f"{format_currency(working_capital_capacity, application.currency)}"
            ),
            numeric_values=[
                tnw_capacity,
                cash_flow_capacity,
                working_capital_capacity,
                base_capacity,
                multiplier,
                indicative,
            ],
        )
        registry.register(
            "policy:terms_cap",
            EvidenceKind.POLICY_RULE,
            "Maximum open-account terms by grade",
            source="Underwriting policy — terms matrix",
            display_value=(
                f"Grade {rating.grade} permits up to net {terms_cap}; requested net "
                f"{application.requested_terms_days}"
            ),
            numeric_values=[float(terms_cap), float(application.requested_terms_days)],
        )

    return guidance
