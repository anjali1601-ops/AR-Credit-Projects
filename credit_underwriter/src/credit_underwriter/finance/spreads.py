"""Spreading: normalise submitted statements and check them for internal consistency.

Spreading is the step a human analyst does first, and it is where data-quality
problems surface. Two checks run here:

* the balance sheet must balance (assets == liabilities + equity), and
* equity must roll forward as prior equity + net income - distributions.

A statement that fails either check is still analysed, but the exception is
recorded and surfaces in the memo as a data-quality finding.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry, extract_numbers
from ..models import (
    AuditOpinion,
    CreditApplication,
    EvidenceKind,
    FinancialStatement,
    Severity,
    SpreadException,
    format_currency,
)

#: Rounding slack, in currency units, before a consistency check is called a failure.
BALANCE_TOLERANCE = 1.0

#: Distributions are often estimated in submitted packages, so the roll-forward
#: check uses a wider band before it complains.
ROLLFORWARD_TOLERANCE = 5_000.0

_INCOME_LINES: tuple[tuple[str, str], ...] = (
    ("revenue", "Revenue"),
    ("cost_of_goods_sold", "Cost of goods sold"),
    ("gross_profit", "Gross profit"),
    ("operating_expenses", "Operating expenses"),
    ("ebitda", "EBITDA"),
    ("depreciation_amortization", "Depreciation & amortisation"),
    ("ebit", "EBIT"),
    ("interest_expense", "Interest expense"),
    ("pretax_income", "Pre-tax income"),
    ("net_income", "Net income"),
)

_BALANCE_LINES: tuple[tuple[str, str], ...] = (
    ("cash_and_equivalents", "Cash & equivalents"),
    ("accounts_receivable", "Accounts receivable"),
    ("inventory", "Inventory"),
    ("total_current_assets", "Total current assets"),
    ("net_property_plant_equipment", "Net PP&E"),
    ("intangible_assets", "Intangible assets"),
    ("total_assets", "Total assets"),
    ("accounts_payable", "Accounts payable"),
    ("short_term_debt", "Short-term debt"),
    ("total_current_liabilities", "Total current liabilities"),
    ("long_term_debt", "Long-term debt"),
    ("total_liabilities", "Total liabilities"),
    ("total_debt", "Total debt"),
    ("net_debt", "Net debt"),
    ("working_capital", "Working capital"),
    ("total_equity", "Total equity"),
    ("tangible_net_worth", "Tangible net worth"),
)

_CASH_FLOW_LINES: tuple[tuple[str, str], ...] = (
    ("cash_from_operations", "Cash from operations"),
    ("capital_expenditures", "Capital expenditures"),
    ("free_cash_flow", "Free cash flow"),
    ("dividends_and_distributions", "Dividends & distributions"),
)

_OPINION_LABEL = {
    AuditOpinion.AUDITED: "Audited",
    AuditOpinion.REVIEWED: "Reviewed",
    AuditOpinion.MANAGEMENT_PREPARED: "Management-prepared (unaudited)",
}


def statement_line_id(period: str, section: str, line: str) -> str:
    return f"stmt:{period}:{section}.{line}"


def statement_source(statement: FinancialStatement) -> str:
    return f"{_OPINION_LABEL[statement.opinion]} {statement.period_label} financial statements"


def register_statement_evidence(
    application: CreditApplication, registry: EvidenceRegistry
) -> None:
    """Register every spread line as citable evidence."""
    for statement in application.ordered_statements:
        period = statement.period_label
        source = statement_source(statement)
        for section, lines, obj in (
            ("income_statement", _INCOME_LINES, statement.income_statement),
            ("balance_sheet", _BALANCE_LINES, statement.balance_sheet),
            ("cash_flow", _CASH_FLOW_LINES, statement.cash_flow),
        ):
            for key, label in lines:
                value = float(getattr(obj, key))
                registry.register(
                    evidence_id=statement_line_id(period, section, key),
                    kind=EvidenceKind.STATEMENT_LINE,
                    label=f"{period} {label}",
                    source=source,
                    display_value=format_currency(value, statement.currency),
                    numeric_values=[value],
                )

    _register_application_evidence(application, registry)


def _register_application_evidence(
    application: CreditApplication, registry: EvidenceRegistry
) -> None:
    source = f"Credit application submitted {application.submitted_at}"
    registry.register(
        "app:requested_limit",
        EvidenceKind.APPLICATION_FIELD,
        "Requested credit limit",
        source,
        display_value=format_currency(application.requested_limit, application.currency),
        numeric_values=[application.requested_limit],
    )
    registry.register(
        "app:requested_terms",
        EvidenceKind.APPLICATION_FIELD,
        "Requested payment terms",
        source,
        display_value=f"Net {application.requested_terms_days}",
        numeric_values=[float(application.requested_terms_days)],
    )
    registry.register(
        "app:years_in_business",
        EvidenceKind.APPLICATION_FIELD,
        "Years in business",
        source,
        display_value=f"{application.years_in_business:.0f} years",
        numeric_values=[float(application.years_in_business)],
    )
    registry.register(
        "app:purpose",
        EvidenceKind.APPLICATION_FIELD,
        "Stated purpose of the facility",
        source,
        display_value=application.purpose,
        numeric_values=[n.value for n in extract_numbers(application.purpose)],
    )
    registry.register(
        "app:industry",
        EvidenceKind.APPLICATION_FIELD,
        "Industry",
        source,
        display_value=f"{application.industry} ({application.industry_code})",
    )
    registry.register(
        "app:country",
        EvidenceKind.APPLICATION_FIELD,
        "Country of domicile",
        source,
        display_value=f"{application.country} ({application.country_code})",
    )
    registry.register(
        "app:disclosures",
        EvidenceKind.APPLICATION_FIELD,
        "Application disclosures",
        source,
        display_value=_disclosure_summary(application),
        detail=application.disclosures.notes or None,
    )
    for ref in application.trade_references:
        registry.register(
            f"app:trade_reference:{_slug(ref.supplier_name)}",
            EvidenceKind.APPLICATION_FIELD,
            f"Trade reference — {ref.supplier_name}",
            source,
            display_value=f"{ref.days_beyond_terms:.0f} days beyond terms on {ref.terms}",
            detail=ref.comment,
            numeric_values=[ref.days_beyond_terms, ref.high_credit],
        )
    for enhancement in application.credit_enhancements:
        registry.register(
            f"app:enhancement:{enhancement.key}",
            EvidenceKind.APPLICATION_FIELD,
            f"Credit enhancement — {enhancement.kind.replace('_', ' ')}",
            source,
            display_value=enhancement.description,
            numeric_values=[
                v
                for v in (enhancement.coverage_amount, enhancement.coverage_percent)
                if v is not None
            ],
        )
    if application.prior_relationship_months:
        registry.register(
            "app:prior_relationship",
            EvidenceKind.APPLICATION_FIELD,
            "Prior trading relationship",
            source,
            display_value=(
                f"{application.prior_relationship_months} months, worst position "
                f"{application.prior_worst_days_beyond_terms:.0f} days beyond terms"
                if application.prior_worst_days_beyond_terms is not None
                else f"{application.prior_relationship_months} months"
            ),
            numeric_values=[
                float(application.prior_relationship_months),
                *(
                    [application.prior_worst_days_beyond_terms]
                    if application.prior_worst_days_beyond_terms is not None
                    else []
                ),
            ],
        )


def _disclosure_summary(application: CreditApplication) -> str:
    d = application.disclosures
    declared = [
        name
        for name, flag in (
            ("material litigation", d.material_litigation),
            ("prior insolvency", d.prior_insolvency_or_bankruptcy),
            ("covenant breach in last 24 months", d.covenant_breach_last_24m),
            ("tax or regulatory penalties", d.tax_or_regulatory_penalties),
            ("pending change of control", d.change_of_control_pending),
        )
        if flag
    ]
    if not declared:
        return "Applicant declared no material litigation, insolvency history, covenant breach, or penalties"
    return "Applicant declared " + "; ".join(declared)


def spread_statements(application: CreditApplication) -> list[SpreadException]:
    """Run the internal-consistency checks and return whatever failed."""
    exceptions: list[SpreadException] = []
    statements = application.ordered_statements

    for statement in statements:
        bs = statement.balance_sheet
        delta = bs.total_assets - (bs.total_liabilities + bs.total_equity)
        if abs(delta) > BALANCE_TOLERANCE:
            exceptions.append(
                SpreadException(
                    period=statement.period_label,
                    check="balance_sheet_balances",
                    detail=(
                        f"Total assets of {format_currency(bs.total_assets, statement.currency)} "
                        f"do not equal liabilities plus equity; difference of "
                        f"{format_currency(delta, statement.currency)}."
                    ),
                    severity=Severity.HIGH,
                    delta=delta,
                )
            )

    for prior, current in zip(statements, statements[1:], strict=False):
        expected = (
            prior.balance_sheet.total_equity
            + current.income_statement.net_income
            - current.cash_flow.dividends_and_distributions
        )
        delta = current.balance_sheet.total_equity - expected
        if abs(delta) > ROLLFORWARD_TOLERANCE:
            exceptions.append(
                SpreadException(
                    period=current.period_label,
                    check="equity_roll_forward",
                    detail=(
                        f"Closing equity differs from opening equity plus net income less "
                        f"distributions by {format_currency(delta, current.currency)}; "
                        "an unexplained equity movement requires management comment."
                    ),
                    severity=Severity.MODERATE,
                    delta=delta,
                )
            )

    if len(statements) < 2:
        exceptions.append(
            SpreadException(
                period=statements[-1].period_label if statements else "n/a",
                check="minimum_periods",
                detail="Fewer than two fiscal years were submitted, so no trend analysis is possible.",
                severity=Severity.HIGH,
                delta=0.0,
            )
        )

    return exceptions


def _slug(value: str) -> str:
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")
