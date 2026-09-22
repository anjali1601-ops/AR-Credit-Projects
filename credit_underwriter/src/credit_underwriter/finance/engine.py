"""The financial engine entry point.

``analyse_financials`` runs the whole deterministic pipeline -- spread, ratios,
trends, scorecard, limit capacity -- and derives rule-based findings from policy
thresholds. The financial analyst agent consumes this output and writes prose over
it; it never recomputes or restates a number.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry, extract_numbers
from ..models import (
    AuditOpinion,
    CreditApplication,
    Direction,
    EvidenceKind,
    FinancialAnalysis,
    FinancialFinding,
    Ratio,
    RatioCategory,
    Severity,
    Trend,
    TrendDirection,
    format_currency,
)
from .ratios import compute_ratios, find_ratio, ratio_evidence_id
from .rating import assign_rating, recommend_limit
from .spreads import register_statement_evidence, spread_statements, statement_line_id
from .trends import compute_trends

# Policy thresholds. Kept as module constants so a finding can cite the number it
# was tested against, and so the test suite pins them.
CURRENT_RATIO_FLOOR = 1.0
CURRENT_RATIO_WATCH = 1.2
CURRENT_RATIO_STRONG = 2.0
QUICK_RATIO_FLOOR = 0.8
LEVERAGE_CEILING = 4.5
LEVERAGE_WATCH = 3.5
LEVERAGE_STRONG = 1.5
COVERAGE_FLOOR = 1.5
COVERAGE_WATCH = 2.5
COVERAGE_STRONG = 6.0
EBITDA_MARGIN_FLOOR = 3.0
EBITDA_MARGIN_STRONG = 10.0
CASH_CONVERSION_CYCLE_WATCH = 90.0
DSO_DETERIORATION_DAYS = 10.0
BROAD_DETERIORATION_COUNT = 3
MIN_OPERATING_HISTORY_YEARS = 5.0


def analyse_financials(
    application: CreditApplication, registry: EvidenceRegistry
) -> FinancialAnalysis:
    statements = application.ordered_statements
    register_statement_evidence(application, registry)

    exceptions = spread_statements(application)
    ratios = compute_ratios(statements, registry)
    trends = compute_trends(statements, ratios, registry)
    rating = assign_rating(application, ratios, trends, registry)
    limit_guidance = recommend_limit(application, rating, registry)
    findings = derive_findings(application, ratios, trends, registry)

    for exception in exceptions:
        findings.append(
            FinancialFinding(
                key=f"spread_exception_{exception.check}_{exception.period}",
                category="data_quality",
                severity=exception.severity,
                direction=Direction.ADVERSE,
                statement=exception.detail,
                evidence_ids=["app:disclosures"],
            )
        )

    return FinancialAnalysis(
        applicant_id=application.applicant_id,
        periods=[s.period_label for s in statements],
        currency=application.currency,
        ratios=ratios,
        trends=trends,
        rating=rating,
        limit_guidance=limit_guidance,
        findings=findings,
        spread_exceptions=exceptions,
    )


def derive_findings(
    application: CreditApplication,
    ratios: list[Ratio],
    trends: list[Trend],
    registry: EvidenceRegistry | None = None,
) -> list[FinancialFinding]:
    """Apply policy thresholds to the spread. Pure rules, no LLM."""
    latest = application.latest_statement
    period = latest.period_label
    currency = application.currency
    findings: list[FinancialFinding] = []

    def add(
        key: str,
        category: RatioCategory | str,
        severity: Severity,
        direction: Direction,
        statement: str,
        evidence_ids: list[str],
    ) -> None:
        findings.append(
            FinancialFinding(
                key=key,
                category=category,  # type: ignore[arg-type]
                severity=severity,
                direction=direction,
                statement=statement,
                evidence_ids=evidence_ids,
            )
        )

    def r(key: str) -> Ratio | None:
        return find_ratio(ratios, key, period)

    def rid(key: str) -> str:
        return ratio_evidence_id(period, key)

    # -- liquidity -------------------------------------------------------------
    current = r("current_ratio")
    if current and current.value is not None:
        if current.value < CURRENT_RATIO_FLOOR:
            add(
                "liquidity_below_floor",
                RatioCategory.LIQUIDITY,
                Severity.HIGH,
                Direction.ADVERSE,
                f"The {period} current ratio of {current.display} is below the {CURRENT_RATIO_FLOOR:.1f}x "
                "policy floor, so current liabilities exceed current assets.",
                [rid("current_ratio")],
            )
        elif current.value < CURRENT_RATIO_WATCH:
            add(
                "liquidity_thin",
                RatioCategory.LIQUIDITY,
                Severity.MODERATE,
                Direction.ADVERSE,
                f"The {period} current ratio of {current.display} leaves only a thin margin over "
                f"the {CURRENT_RATIO_FLOOR:.1f}x policy floor.",
                [rid("current_ratio")],
            )
        elif current.value >= CURRENT_RATIO_STRONG:
            add(
                "liquidity_strong",
                RatioCategory.LIQUIDITY,
                Severity.NONE,
                Direction.SUPPORTIVE,
                f"The {period} current ratio of {current.display} is comfortably above the "
                f"{CURRENT_RATIO_STRONG:.1f}x level policy treats as strong.",
                [rid("current_ratio")],
            )

    quick = r("quick_ratio")
    if quick and quick.value is not None and quick.value < QUICK_RATIO_FLOOR:
        add(
            "quick_ratio_low",
            RatioCategory.LIQUIDITY,
            Severity.MODERATE,
            Direction.ADVERSE,
            f"Excluding inventory, the {period} quick ratio of {quick.display} is below the "
            f"{QUICK_RATIO_FLOOR:.1f}x floor, so near-term obligations depend on inventory conversion.",
            [rid("quick_ratio")],
        )

    # -- leverage and capital structure ---------------------------------------
    leverage = r("net_debt_to_ebitda")
    if leverage is not None:
        if leverage.value is None:
            add(
                "leverage_not_measurable",
                RatioCategory.LEVERAGE,
                Severity.CRITICAL,
                Direction.ADVERSE,
                f"Leverage cannot be expressed as a multiple for {period} because EBITDA of "
                f"{format_currency(latest.income_statement.ebitda, currency)} will not service "
                f"net debt of {format_currency(latest.balance_sheet.net_debt, currency)}.",
                [rid("net_debt_to_ebitda"), statement_line_id(period, "income_statement", "ebitda")],
            )
        elif leverage.value > LEVERAGE_CEILING:
            add(
                "leverage_above_ceiling",
                RatioCategory.LEVERAGE,
                Severity.HIGH,
                Direction.ADVERSE,
                f"Net debt of {leverage.display} EBITDA exceeds the {LEVERAGE_CEILING:.1f}x policy ceiling.",
                [rid("net_debt_to_ebitda")],
            )
        elif leverage.value > LEVERAGE_WATCH:
            add(
                "leverage_elevated",
                RatioCategory.LEVERAGE,
                Severity.MODERATE,
                Direction.ADVERSE,
                f"Net debt of {leverage.display} EBITDA is above the {LEVERAGE_WATCH:.1f}x watch level.",
                [rid("net_debt_to_ebitda")],
            )
        elif leverage.value <= LEVERAGE_STRONG:
            add(
                "leverage_conservative",
                RatioCategory.LEVERAGE,
                Severity.NONE,
                Direction.SUPPORTIVE,
                f"Net debt of {leverage.display} EBITDA is a conservative capital structure.",
                [rid("net_debt_to_ebitda")],
            )

    if latest.balance_sheet.total_equity <= 0:
        add(
            "negative_equity",
            "structure",
            Severity.CRITICAL,
            Direction.ADVERSE,
            f"{period} equity of {format_currency(latest.balance_sheet.total_equity, currency)} is "
            "negative, so the balance sheet carries no loss-absorbing capital.",
            [statement_line_id(period, "balance_sheet", "total_equity")],
        )
    elif latest.balance_sheet.tangible_net_worth <= 0:
        add(
            "negative_tangible_net_worth",
            "structure",
            Severity.HIGH,
            Direction.ADVERSE,
            f"Tangible net worth of "
            f"{format_currency(latest.balance_sheet.tangible_net_worth, currency)} is negative once "
            "intangibles are deducted from equity.",
            [statement_line_id(period, "balance_sheet", "tangible_net_worth")],
        )

    # -- coverage --------------------------------------------------------------
    coverage = r("ebitda_interest_coverage")
    if coverage and coverage.value is not None:
        if coverage.value < COVERAGE_FLOOR:
            add(
                "coverage_below_floor",
                RatioCategory.COVERAGE,
                Severity.HIGH,
                Direction.ADVERSE,
                f"EBITDA covers interest only {coverage.display}, below the {COVERAGE_FLOOR:.1f}x policy floor.",
                [rid("ebitda_interest_coverage")],
            )
        elif coverage.value < COVERAGE_WATCH:
            add(
                "coverage_thin",
                RatioCategory.COVERAGE,
                Severity.MODERATE,
                Direction.ADVERSE,
                f"Interest coverage of {coverage.display} is thin against the {COVERAGE_WATCH:.1f}x watch level.",
                [rid("ebitda_interest_coverage")],
            )
        elif coverage.value >= COVERAGE_STRONG:
            add(
                "coverage_strong",
                RatioCategory.COVERAGE,
                Severity.NONE,
                Direction.SUPPORTIVE,
                f"EBITDA covers interest {coverage.display}, well clear of the {COVERAGE_STRONG:.1f}x "
                "level policy treats as strong.",
                [rid("ebitda_interest_coverage")],
            )

    dscr = r("debt_service_coverage")
    if dscr and dscr.value is not None and dscr.value < 1.0:
        add(
            "debt_service_shortfall",
            RatioCategory.COVERAGE,
            Severity.HIGH,
            Direction.ADVERSE,
            f"Debt service coverage of {dscr.display} means {period} EBITDA does not cover interest "
            "plus the current portion of debt.",
            [rid("debt_service_coverage")],
        )

    # -- profitability ---------------------------------------------------------
    net_margin = r("net_margin")
    if net_margin and net_margin.value is not None and net_margin.value < 0:
        add(
            "net_loss",
            RatioCategory.PROFITABILITY,
            Severity.HIGH,
            Direction.ADVERSE,
            f"{period} closed with a net loss of "
            f"{format_currency(latest.income_statement.net_income, currency)}, a net margin of "
            f"{net_margin.display}.",
            [rid("net_margin"), statement_line_id(period, "income_statement", "net_income")],
        )

    margin = r("ebitda_margin")
    if margin and margin.value is not None:
        if margin.value < EBITDA_MARGIN_FLOOR:
            add(
                "margin_below_floor",
                RatioCategory.PROFITABILITY,
                Severity.HIGH,
                Direction.ADVERSE,
                f"An EBITDA margin of {margin.display} is below the {EBITDA_MARGIN_FLOOR:.1f}% policy "
                "floor and leaves almost no absorption for cost shocks.",
                [rid("ebitda_margin")],
            )
        elif margin.value >= EBITDA_MARGIN_STRONG:
            add(
                "margin_strong",
                RatioCategory.PROFITABILITY,
                Severity.NONE,
                Direction.SUPPORTIVE,
                f"An EBITDA margin of {margin.display} is above the {EBITDA_MARGIN_STRONG:.1f}% level "
                "policy treats as strong for this sector.",
                [rid("ebitda_margin")],
            )

    # -- cash flow -------------------------------------------------------------
    statements = application.ordered_statements
    if latest.cash_flow.cash_from_operations < 0:
        add(
            "negative_operating_cash_flow",
            "cash_flow",
            Severity.HIGH,
            Direction.ADVERSE,
            f"Operations consumed "
            f"{format_currency(abs(latest.cash_flow.cash_from_operations), currency)} of cash in {period}.",
            [statement_line_id(period, "cash_flow", "cash_from_operations")],
        )
    negative_fcf_years = [
        s.period_label for s in statements if s.cash_flow.free_cash_flow < 0
    ]
    if latest.cash_flow.free_cash_flow < 0:
        consecutive = len(negative_fcf_years) >= 2
        add(
            "negative_free_cash_flow",
            "cash_flow",
            Severity.HIGH if consecutive else Severity.MODERATE,
            Direction.ADVERSE,
            f"Free cash flow was negative in {period} at "
            f"{format_currency(latest.cash_flow.free_cash_flow, currency)}"
            + (
                f", the {len(negative_fcf_years)} of {len(statements)} submitted years to show a "
                "cash outflow after capital expenditure."
                if consecutive
                else "."
            ),
            [statement_line_id(period, "cash_flow", "free_cash_flow")],
        )

    # -- working capital efficiency -------------------------------------------
    ccc = r("cash_conversion_cycle")
    if ccc and ccc.value is not None and ccc.value > CASH_CONVERSION_CYCLE_WATCH:
        add(
            "long_cash_conversion_cycle",
            RatioCategory.EFFICIENCY,
            Severity.MODERATE,
            Direction.ADVERSE,
            f"A cash conversion cycle of {ccc.display} ties up working capital well beyond the "
            f"{CASH_CONVERSION_CYCLE_WATCH:.0f}-day watch level.",
            [rid("cash_conversion_cycle")],
        )

    dso_trend = next((t for t in trends if t.key == "dso"), None)
    if dso_trend and dso_trend.change > DSO_DETERIORATION_DAYS:
        add(
            "receivables_stretching",
            RatioCategory.EFFICIENCY,
            Severity.MODERATE,
            Direction.ADVERSE,
            f"Days sales outstanding lengthened by {dso_trend.change:.0f} days between "
            f"{dso_trend.first_period} and {dso_trend.last_period}, which points to slower collection.",
            ["trend:dso"],
        )

    # -- trend composite -------------------------------------------------------
    deteriorating = [t for t in trends if t.direction is TrendDirection.DETERIORATING]
    improving = [t for t in trends if t.direction is TrendDirection.IMPROVING]
    if len(deteriorating) >= BROAD_DETERIORATION_COUNT:
        add(
            "broad_deterioration",
            "structure",
            Severity.HIGH,
            Direction.ADVERSE,
            f"{len(deteriorating)} of the {len(trends)} tracked metrics deteriorated across "
            f"{trends[0].first_period}–{trends[0].last_period}: "
            + ", ".join(t.label for t in deteriorating)
            + ".",
            [f"trend:{t.key}" for t in deteriorating],
        )
    elif improving and not deteriorating:
        add(
            "broad_improvement",
            "structure",
            Severity.NONE,
            Direction.SUPPORTIVE,
            f"All {len(improving)} tracked metrics improved across "
            f"{trends[0].first_period}–{trends[0].last_period}.",
            [f"trend:{t.key}" for t in improving],
        )

    # -- data quality and structure -------------------------------------------
    if latest.opinion is AuditOpinion.MANAGEMENT_PREPARED:
        add(
            "unaudited_statements",
            "data_quality",
            Severity.MODERATE,
            Direction.ADVERSE,
            f"The {period} statements are management-prepared, so the spread rests on unverified figures.",
            [statement_line_id(period, "income_statement", "revenue")],
        )
    elif latest.opinion is AuditOpinion.AUDITED:
        add(
            "audited_statements",
            "data_quality",
            Severity.NONE,
            Direction.SUPPORTIVE,
            f"The {period} statements carry an audit opinion.",
            [statement_line_id(period, "income_statement", "revenue")],
        )

    if application.years_in_business < MIN_OPERATING_HISTORY_YEARS:
        add(
            "limited_operating_history",
            "structure",
            Severity.MODERATE,
            Direction.ADVERSE,
            f"At {application.years_in_business:.0f} years the applicant has less than the "
            f"{MIN_OPERATING_HISTORY_YEARS:.0f} years of operating history policy expects for "
            "unsecured open account.",
            ["app:years_in_business"],
        )

    if registry is not None:
        for finding in findings:
            # A finding is the citable source for its own assertion: it also
            # carries the policy threshold it was tested against, which is not a
            # statement line and so lives nowhere else in the registry. Its
            # ``inputs`` keep the chain down to the underlying ratios walkable.
            registry.register(
                f"finding:financial:{finding.key}",
                EvidenceKind.COMPUTED_SIGNAL,
                f"Financial finding — {finding.key.replace('_', ' ')}",
                source="Financial engine policy threshold test",
                display_value=finding.statement,
                detail=(
                    f"severity={finding.severity.value}, direction={finding.direction.value}, "
                    f"derived from {', '.join(finding.evidence_ids)}"
                ),
                numeric_values=[n.value for n in extract_numbers(finding.statement)],
            )

    return findings
