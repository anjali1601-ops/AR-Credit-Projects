"""Quantitative risk signals computed in code rather than retrieved.

Customer concentration is a number the applicant supplied, so it is measured
here and cited as a computed signal. The risk searcher pairs it with whatever the
corpus says about the same customers.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry
from ..models import (
    ConcentrationSignal,
    CreditApplication,
    EvidenceKind,
    Severity,
)
from .policy import (
    COUNTRY_TIER_LABELS,
    INDUSTRY_TIER_LABELS,
    concentration_severity,
    country_tier,
    industry_tier,
)


def assess_concentration(
    application: CreditApplication, registry: EvidenceRegistry | None = None
) -> ConcentrationSignal:
    customers = sorted(
        application.customer_concentration,
        key=lambda c: c.percent_of_revenue,
        reverse=True,
    )
    if not customers:
        signal = ConcentrationSignal(
            largest_customer=None,
            largest_share_percent=0.0,
            top3_share_percent=0.0,
            herfindahl_index=0.0,
            severity=Severity.NONE,
            statement="No customer concentration data was submitted, so concentration could not be measured.",
            evidence_ids=["app:disclosures"],
        )
    else:
        largest = customers[0]
        top3 = sum(c.percent_of_revenue for c in customers[:3])
        hhi = sum(c.percent_of_revenue**2 for c in customers)
        severity = concentration_severity(largest.percent_of_revenue)
        expiry = (
            f", contracted to {largest.contract_expiry}"
            if largest.contract_expiry
            else " with no contracted end date"
        )
        signal = ConcentrationSignal(
            largest_customer=largest.customer_name,
            largest_share_percent=largest.percent_of_revenue,
            top3_share_percent=top3,
            herfindahl_index=hhi,
            severity=severity,
            statement=(
                f"{largest.customer_name} accounts for {largest.percent_of_revenue:.0f}% of revenue"
                f"{expiry}, and the three largest customers account for {top3:.0f}% between them."
            ),
            evidence_ids=["signal:concentration"],
        )

    if registry is not None:
        registry.register(
            "signal:concentration",
            EvidenceKind.COMPUTED_SIGNAL,
            "Customer concentration",
            source=f"Computed from customer schedule in application {application.applicant_id}",
            display_value=signal.statement,
            detail=(
                f"Partial Herfindahl index across disclosed customers: "
                f"{signal.herfindahl_index:.0f}; severity {signal.severity.value}"
            ),
            numeric_values=[
                signal.largest_share_percent,
                signal.top3_share_percent,
                signal.herfindahl_index,
            ],
        )
    return signal


def register_jurisdiction_evidence(
    application: CreditApplication, registry: EvidenceRegistry
) -> tuple[int, int]:
    """Register the country and industry tiers, returning both."""
    c_tier = country_tier(application.country_code)
    i_tier = industry_tier(application.industry_code)

    registry.register(
        "policy:country_tier",
        EvidenceKind.POLICY_RULE,
        "Country risk tier",
        source="Underwriting policy — country risk table",
        display_value=(
            f"{application.country} is tier {c_tier} of 5 ({COUNTRY_TIER_LABELS[c_tier]})"
        ),
        numeric_values=[float(c_tier)],
    )
    registry.register(
        "policy:industry_tier",
        EvidenceKind.POLICY_RULE,
        "Industry risk tier",
        source="Underwriting policy — industry risk table",
        display_value=(
            f"{application.industry} is tier {i_tier} of 5 ({INDUSTRY_TIER_LABELS[i_tier]})"
        ),
        numeric_values=[float(i_tier)],
    )
    return c_tier, i_tier
