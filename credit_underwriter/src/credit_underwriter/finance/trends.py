"""Trend analysis across the submitted fiscal years.

Direction is polarity-aware: a rising net-debt/EBITDA multiple is deteriorating
while a rising EBITDA margin is improving. The ``STABLE`` band exists so that
noise in the third decimal place does not get written up as a trend.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..evidence import EvidenceRegistry
from ..models import (
    EvidenceKind,
    FinancialStatement,
    Ratio,
    Trend,
    TrendDirection,
    format_ratio_value,
)
from .ratios import find_ratio, ratio_evidence_id
from .spreads import statement_line_id

#: A move smaller than this share of the starting value is reported as stable.
STABLE_BAND = 0.05


@dataclass(frozen=True)
class TrendSpec:
    key: str
    label: str
    unit: str
    higher_is_better: bool
    #: Either a ratio key, or a ("section", "line") statement line.
    ratio_key: str | None = None
    statement_line: tuple[str, str] | None = None


TREND_SPECS: tuple[TrendSpec, ...] = (
    TrendSpec("revenue", "Revenue", "currency", True, statement_line=("income_statement", "revenue")),
    TrendSpec("ebitda", "EBITDA", "currency", True, statement_line=("income_statement", "ebitda")),
    TrendSpec(
        "free_cash_flow",
        "Free cash flow",
        "currency",
        True,
        statement_line=("cash_flow", "free_cash_flow"),
    ),
    TrendSpec(
        "total_equity",
        "Total equity",
        "currency",
        True,
        statement_line=("balance_sheet", "total_equity"),
    ),
    TrendSpec("ebitda_margin", "EBITDA margin", "%", True, ratio_key="ebitda_margin"),
    TrendSpec("gross_margin", "Gross margin", "%", True, ratio_key="gross_margin"),
    TrendSpec("current_ratio", "Current ratio", "x", True, ratio_key="current_ratio"),
    TrendSpec(
        "net_debt_to_ebitda", "Net debt / EBITDA", "x", False, ratio_key="net_debt_to_ebitda"
    ),
    TrendSpec(
        "ebitda_interest_coverage",
        "EBITDA / interest",
        "x",
        True,
        ratio_key="ebitda_interest_coverage",
    ),
    TrendSpec("dso", "Days sales outstanding", "days", False, ratio_key="dso"),
)


def compute_trends(
    statements: list[FinancialStatement],
    ratios: list[Ratio],
    registry: EvidenceRegistry | None = None,
) -> list[Trend]:
    if len(statements) < 2:
        return []

    first, last = statements[0], statements[-1]
    trends: list[Trend] = []

    for spec in TREND_SPECS:
        first_value, first_inputs = _value_for(spec, first, ratios)
        last_value, last_inputs = _value_for(spec, last, ratios)
        if first_value is None or last_value is None:
            continue

        change = last_value - first_value
        percent_change = (
            (change / abs(first_value)) * 100 if abs(first_value) > 1e-9 else None
        )
        cagr = _cagr(first_value, last_value, len(statements) - 1)
        direction = _direction(first_value, change, spec.higher_is_better)

        trend = Trend(
            key=spec.key,
            label=spec.label,
            unit=spec.unit,  # type: ignore[arg-type]
            first_period=first.period_label,
            last_period=last.period_label,
            first_value=first_value,
            last_value=last_value,
            change=change,
            percent_change=percent_change,
            cagr=cagr,
            direction=direction,
            higher_is_better=spec.higher_is_better,
            inputs=[*first_inputs, *last_inputs],
        )
        trends.append(trend)

        if registry is not None:
            registry.register(
                evidence_id=f"trend:{spec.key}",
                kind=EvidenceKind.TREND,
                label=f"{spec.label} trend {first.period_label}–{last.period_label}",
                source=f"Computed from {first.period_label}–{last.period_label} submitted statements",
                display_value=(
                    f"{format_ratio_value(first_value, spec.unit)} → "
                    f"{format_ratio_value(last_value, spec.unit)} ({direction.value})"
                ),
                detail=(
                    f"Change of {format_ratio_value(change, spec.unit)}"
                    + (f", {percent_change:+.1f}%" if percent_change is not None else "")
                    + (f", CAGR {cagr:+.1f}%" if cagr is not None else "")
                ),
                numeric_values=[
                    v
                    for v in (first_value, last_value, change, percent_change, cagr)
                    if v is not None
                ],
            )

    return trends


def _value_for(
    spec: TrendSpec, statement: FinancialStatement, ratios: list[Ratio]
) -> tuple[float | None, list[str]]:
    period = statement.period_label
    if spec.statement_line is not None:
        section, line = spec.statement_line
        obj = getattr(statement, section)
        return float(getattr(obj, line)), [statement_line_id(period, section, line)]
    assert spec.ratio_key is not None
    ratio = find_ratio(ratios, spec.ratio_key, period)
    if ratio is None or ratio.value is None:
        return None, []
    return ratio.value, [ratio_evidence_id(period, spec.ratio_key)]


def _cagr(first: float, last: float, years: int) -> float | None:
    """Compound annual growth rate, only where the sign convention makes it meaningful."""
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def _direction(first: float, change: float, higher_is_better: bool) -> TrendDirection:
    scale = max(abs(first), 1e-9)
    if abs(change) / scale < STABLE_BAND:
        return TrendDirection.STABLE
    improving = (change > 0) == higher_is_better
    return TrendDirection.IMPROVING if improving else TrendDirection.DETERIORATING
