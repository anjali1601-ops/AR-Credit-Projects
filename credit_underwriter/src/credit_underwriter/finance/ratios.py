"""Ratio computation.

Each ratio carries its formula and the evidence ids of the statement lines it was
built from, so a claim in the memo that cites ``ratio:FY2025:net_debt_to_ebitda``
can be expanded all the way down to the individual submitted lines.

Ratios that are arithmetically computable but not interpretable -- debt/equity on
negative equity, coverage on zero interest -- return ``None`` with an explicit
``not_meaningful_reason`` rather than a misleading number.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..evidence import EvidenceRegistry
from ..models import (
    EvidenceKind,
    FinancialStatement,
    Ratio,
    RatioCategory,
    format_ratio_value,
)
from .spreads import statement_line_id, statement_source

DAYS_IN_YEAR = 365.0


@dataclass(frozen=True)
class RatioSpec:
    key: str
    label: str
    category: RatioCategory
    unit: str
    formula: str
    inputs: tuple[tuple[str, str], ...]
    compute: Callable[[FinancialStatement], tuple[float | None, str | None]]
    higher_is_better: bool = True


def _safe_div(
    numerator: float, denominator: float, reason: str, min_abs: float = 1e-9
) -> tuple[float | None, str | None]:
    if abs(denominator) < min_abs:
        return None, reason
    return numerator / denominator, None


def _current_ratio(s: FinancialStatement) -> tuple[float | None, str | None]:
    bs = s.balance_sheet
    return _safe_div(
        bs.total_current_assets, bs.total_current_liabilities, "no current liabilities reported"
    )


def _quick_ratio(s: FinancialStatement) -> tuple[float | None, str | None]:
    bs = s.balance_sheet
    return _safe_div(
        bs.cash_and_equivalents + bs.accounts_receivable,
        bs.total_current_liabilities,
        "no current liabilities reported",
    )


def _cash_ratio(s: FinancialStatement) -> tuple[float | None, str | None]:
    bs = s.balance_sheet
    return _safe_div(
        bs.cash_and_equivalents, bs.total_current_liabilities, "no current liabilities reported"
    )


def _working_capital(s: FinancialStatement) -> tuple[float | None, str | None]:
    return s.balance_sheet.working_capital, None


def _debt_to_equity(s: FinancialStatement) -> tuple[float | None, str | None]:
    bs = s.balance_sheet
    if bs.total_equity <= 0:
        return None, "equity is negative or nil, so the ratio is not meaningful"
    return bs.total_debt / bs.total_equity, None


def _net_debt_to_ebitda(s: FinancialStatement) -> tuple[float | None, str | None]:
    ebitda = s.income_statement.ebitda
    if ebitda <= 0:
        return None, "EBITDA is negative or nil, so leverage cannot be expressed as a multiple"
    return s.balance_sheet.net_debt / ebitda, None


def _liabilities_to_assets(s: FinancialStatement) -> tuple[float | None, str | None]:
    bs = s.balance_sheet
    value, reason = _safe_div(bs.total_liabilities, bs.total_assets, "no assets reported")
    return (None, reason) if value is None else (value * 100, None)


def _equity_ratio(s: FinancialStatement) -> tuple[float | None, str | None]:
    bs = s.balance_sheet
    value, reason = _safe_div(bs.total_equity, bs.total_assets, "no assets reported")
    return (None, reason) if value is None else (value * 100, None)


def _ebitda_interest_coverage(s: FinancialStatement) -> tuple[float | None, str | None]:
    return _safe_div(
        s.income_statement.ebitda, s.income_statement.interest_expense, "no interest expense reported"
    )


def _ebit_interest_coverage(s: FinancialStatement) -> tuple[float | None, str | None]:
    return _safe_div(
        s.income_statement.ebit, s.income_statement.interest_expense, "no interest expense reported"
    )


def _debt_service_coverage(s: FinancialStatement) -> tuple[float | None, str | None]:
    """EBITDA over interest plus the current portion of debt."""
    service = s.income_statement.interest_expense + s.balance_sheet.short_term_debt
    return _safe_div(s.income_statement.ebitda, service, "no debt service obligation reported")


def _fcf_to_total_debt(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.cash_flow.free_cash_flow, s.balance_sheet.total_debt, "no debt reported"
    )
    return (None, reason) if value is None else (value * 100, None)


def _gross_margin(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.income_statement.gross_profit, s.income_statement.revenue, "no revenue reported"
    )
    return (None, reason) if value is None else (value * 100, None)


def _ebitda_margin(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.income_statement.ebitda, s.income_statement.revenue, "no revenue reported"
    )
    return (None, reason) if value is None else (value * 100, None)


def _net_margin(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.income_statement.net_income, s.income_statement.revenue, "no revenue reported"
    )
    return (None, reason) if value is None else (value * 100, None)


def _return_on_assets(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.income_statement.net_income, s.balance_sheet.total_assets, "no assets reported"
    )
    return (None, reason) if value is None else (value * 100, None)


def _asset_turnover(s: FinancialStatement) -> tuple[float | None, str | None]:
    return _safe_div(s.income_statement.revenue, s.balance_sheet.total_assets, "no assets reported")


def _dso(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.balance_sheet.accounts_receivable, s.income_statement.revenue, "no revenue reported"
    )
    return (None, reason) if value is None else (value * DAYS_IN_YEAR, None)


def _dio(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.balance_sheet.inventory, s.income_statement.cost_of_goods_sold, "no cost of goods sold reported"
    )
    return (None, reason) if value is None else (value * DAYS_IN_YEAR, None)


def _dpo(s: FinancialStatement) -> tuple[float | None, str | None]:
    value, reason = _safe_div(
        s.balance_sheet.accounts_payable,
        s.income_statement.cost_of_goods_sold,
        "no cost of goods sold reported",
    )
    return (None, reason) if value is None else (value * DAYS_IN_YEAR, None)


def _cash_conversion_cycle(s: FinancialStatement) -> tuple[float | None, str | None]:
    dso, r1 = _dso(s)
    dio, r2 = _dio(s)
    dpo, r3 = _dpo(s)
    if dso is None or dio is None or dpo is None:
        return None, r1 or r2 or r3
    return dso + dio - dpo, None


RATIO_SPECS: tuple[RatioSpec, ...] = (
    RatioSpec(
        "current_ratio",
        "Current ratio",
        RatioCategory.LIQUIDITY,
        "x",
        "total current assets / total current liabilities",
        (
            ("balance_sheet", "total_current_assets"),
            ("balance_sheet", "total_current_liabilities"),
        ),
        _current_ratio,
    ),
    RatioSpec(
        "quick_ratio",
        "Quick ratio",
        RatioCategory.LIQUIDITY,
        "x",
        "(cash + accounts receivable) / total current liabilities",
        (
            ("balance_sheet", "cash_and_equivalents"),
            ("balance_sheet", "accounts_receivable"),
            ("balance_sheet", "total_current_liabilities"),
        ),
        _quick_ratio,
    ),
    RatioSpec(
        "cash_ratio",
        "Cash ratio",
        RatioCategory.LIQUIDITY,
        "x",
        "cash / total current liabilities",
        (
            ("balance_sheet", "cash_and_equivalents"),
            ("balance_sheet", "total_current_liabilities"),
        ),
        _cash_ratio,
    ),
    RatioSpec(
        "working_capital",
        "Working capital",
        RatioCategory.LIQUIDITY,
        "currency",
        "total current assets - total current liabilities",
        (
            ("balance_sheet", "total_current_assets"),
            ("balance_sheet", "total_current_liabilities"),
        ),
        _working_capital,
    ),
    RatioSpec(
        "debt_to_equity",
        "Debt / equity",
        RatioCategory.LEVERAGE,
        "x",
        "total debt / total equity",
        (("balance_sheet", "total_debt"), ("balance_sheet", "total_equity")),
        _debt_to_equity,
        higher_is_better=False,
    ),
    RatioSpec(
        "net_debt_to_ebitda",
        "Net debt / EBITDA",
        RatioCategory.LEVERAGE,
        "x",
        "(total debt - cash) / EBITDA",
        (("balance_sheet", "net_debt"), ("income_statement", "ebitda")),
        _net_debt_to_ebitda,
        higher_is_better=False,
    ),
    RatioSpec(
        "liabilities_to_assets",
        "Total liabilities / total assets",
        RatioCategory.LEVERAGE,
        "%",
        "total liabilities / total assets",
        (("balance_sheet", "total_liabilities"), ("balance_sheet", "total_assets")),
        _liabilities_to_assets,
        higher_is_better=False,
    ),
    RatioSpec(
        "equity_ratio",
        "Equity / total assets",
        RatioCategory.LEVERAGE,
        "%",
        "total equity / total assets",
        (("balance_sheet", "total_equity"), ("balance_sheet", "total_assets")),
        _equity_ratio,
    ),
    RatioSpec(
        "ebitda_interest_coverage",
        "EBITDA / interest",
        RatioCategory.COVERAGE,
        "x",
        "EBITDA / interest expense",
        (("income_statement", "ebitda"), ("income_statement", "interest_expense")),
        _ebitda_interest_coverage,
    ),
    RatioSpec(
        "ebit_interest_coverage",
        "EBIT / interest",
        RatioCategory.COVERAGE,
        "x",
        "EBIT / interest expense",
        (("income_statement", "ebit"), ("income_statement", "interest_expense")),
        _ebit_interest_coverage,
    ),
    RatioSpec(
        "debt_service_coverage",
        "Debt service coverage",
        RatioCategory.COVERAGE,
        "x",
        "EBITDA / (interest expense + short-term debt)",
        (
            ("income_statement", "ebitda"),
            ("income_statement", "interest_expense"),
            ("balance_sheet", "short_term_debt"),
        ),
        _debt_service_coverage,
    ),
    RatioSpec(
        "fcf_to_total_debt",
        "Free cash flow / total debt",
        RatioCategory.COVERAGE,
        "%",
        "free cash flow / total debt",
        (("cash_flow", "free_cash_flow"), ("balance_sheet", "total_debt")),
        _fcf_to_total_debt,
    ),
    RatioSpec(
        "gross_margin",
        "Gross margin",
        RatioCategory.PROFITABILITY,
        "%",
        "gross profit / revenue",
        (("income_statement", "gross_profit"), ("income_statement", "revenue")),
        _gross_margin,
    ),
    RatioSpec(
        "ebitda_margin",
        "EBITDA margin",
        RatioCategory.PROFITABILITY,
        "%",
        "EBITDA / revenue",
        (("income_statement", "ebitda"), ("income_statement", "revenue")),
        _ebitda_margin,
    ),
    RatioSpec(
        "net_margin",
        "Net margin",
        RatioCategory.PROFITABILITY,
        "%",
        "net income / revenue",
        (("income_statement", "net_income"), ("income_statement", "revenue")),
        _net_margin,
    ),
    RatioSpec(
        "return_on_assets",
        "Return on assets",
        RatioCategory.PROFITABILITY,
        "%",
        "net income / total assets",
        (("income_statement", "net_income"), ("balance_sheet", "total_assets")),
        _return_on_assets,
    ),
    RatioSpec(
        "asset_turnover",
        "Asset turnover",
        RatioCategory.EFFICIENCY,
        "x",
        "revenue / total assets",
        (("income_statement", "revenue"), ("balance_sheet", "total_assets")),
        _asset_turnover,
    ),
    RatioSpec(
        "dso",
        "Days sales outstanding",
        RatioCategory.EFFICIENCY,
        "days",
        "accounts receivable / revenue x 365",
        (("balance_sheet", "accounts_receivable"), ("income_statement", "revenue")),
        _dso,
        higher_is_better=False,
    ),
    RatioSpec(
        "dio",
        "Days inventory outstanding",
        RatioCategory.EFFICIENCY,
        "days",
        "inventory / cost of goods sold x 365",
        (("balance_sheet", "inventory"), ("income_statement", "cost_of_goods_sold")),
        _dio,
        higher_is_better=False,
    ),
    RatioSpec(
        "dpo",
        "Days payable outstanding",
        RatioCategory.EFFICIENCY,
        "days",
        "accounts payable / cost of goods sold x 365",
        (("balance_sheet", "accounts_payable"), ("income_statement", "cost_of_goods_sold")),
        _dpo,
        higher_is_better=False,
    ),
    RatioSpec(
        "cash_conversion_cycle",
        "Cash conversion cycle",
        RatioCategory.EFFICIENCY,
        "days",
        "days sales outstanding + days inventory outstanding - days payable outstanding",
        (
            ("balance_sheet", "accounts_receivable"),
            ("balance_sheet", "inventory"),
            ("balance_sheet", "accounts_payable"),
        ),
        _cash_conversion_cycle,
        higher_is_better=False,
    ),
)

RATIO_SPEC_BY_KEY: dict[str, RatioSpec] = {spec.key: spec for spec in RATIO_SPECS}


def ratio_evidence_id(period: str, key: str) -> str:
    return f"ratio:{period}:{key}"


def compute_ratios(
    statements: list[FinancialStatement], registry: EvidenceRegistry | None = None
) -> list[Ratio]:
    """Compute every ratio for every submitted period, registering each as evidence."""
    ratios: list[Ratio] = []
    for statement in statements:
        period = statement.period_label
        for spec in RATIO_SPECS:
            value, reason = spec.compute(statement)
            inputs = [
                statement_line_id(period, section, line) for section, line in spec.inputs
            ]
            ratio = Ratio(
                key=spec.key,
                label=spec.label,
                category=spec.category,
                period=period,
                value=value,
                unit=spec.unit,  # type: ignore[arg-type]
                formula=spec.formula,
                inputs=inputs,
                not_meaningful_reason=reason,
            )
            ratios.append(ratio)
            if registry is not None:
                display = (
                    format_ratio_value(value, spec.unit)
                    if spec.unit != "currency"
                    else format_ratio_value(value, "currency")
                )
                registry.register(
                    evidence_id=ratio_evidence_id(period, spec.key),
                    kind=EvidenceKind.RATIO,
                    label=f"{period} {spec.label}",
                    source=f"Computed from {statement_source(statement)}",
                    display_value=display if value is not None else f"n.m. — {reason}",
                    detail=f"{spec.label} = {spec.formula}",
                    numeric_values=[value] if value is not None else [],
                )
    return ratios


def find_ratio(ratios: list[Ratio], key: str, period: str) -> Ratio | None:
    return next((r for r in ratios if r.key == key and r.period == period), None)
