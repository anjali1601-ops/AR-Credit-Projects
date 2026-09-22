"""Country, industry, and concentration policy tables.

These are deterministic lookups and thresholds, not model output. The risk
searcher cites the corresponding country or industry report as the evidence
behind a tier, but the tier itself comes from this table so it cannot drift
between runs.
"""

from __future__ import annotations

from ..models import Severity

#: 1 is the strongest tier, 5 the weakest. Unlisted jurisdictions default to 3.
COUNTRY_RISK_TIERS: dict[str, int] = {
    "US": 1,
    "CA": 1,
    "GB": 1,
    "DE": 1,
    "FR": 2,
    "MX": 3,
    "BR": 3,
    "ZB": 5,
}
DEFAULT_COUNTRY_TIER = 3

#: Sector cyclicality and loss experience. 1 is the most resilient.
INDUSTRY_RISK_TIERS: dict[str, int] = {
    "NAICS-332710": 2,
    "NAICS-484121": 4,
    "NAICS-423510": 4,
}
DEFAULT_INDUSTRY_TIER = 3

COUNTRY_TIER_LABELS: dict[int, str] = {
    1: "lowest country risk",
    2: "low country risk",
    3: "moderate country risk",
    4: "elevated country risk",
    5: "high country risk",
}

INDUSTRY_TIER_LABELS: dict[int, str] = {
    1: "defensive sector",
    2: "resilient sector",
    3: "average sector cyclicality",
    4: "cyclical sector",
    5: "highly cyclical sector",
}

#: Notch penalties added to the standalone grade.
COUNTRY_TIER_NOTCHES: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.25, 4: 0.5, 5: 1.0}
INDUSTRY_TIER_NOTCHES: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.25, 5: 0.5}

#: Largest-single-customer share of revenue, as a percentage.
CONCENTRATION_BANDS: tuple[tuple[float, Severity], ...] = (
    (40.0, Severity.CRITICAL),
    (25.0, Severity.HIGH),
    (15.0, Severity.MODERATE),
    (10.0, Severity.LOW),
    (0.0, Severity.NONE),
)

CONCENTRATION_NOTCHES: dict[Severity, float] = {
    Severity.NONE: 0.0,
    Severity.LOW: 0.0,
    Severity.MODERATE: 0.25,
    Severity.HIGH: 0.5,
    Severity.CRITICAL: 0.75,
}

#: Proportional reduction applied to the approved limit.
CONCENTRATION_HAIRCUT: dict[Severity, float] = {
    Severity.NONE: 0.0,
    Severity.LOW: 0.0,
    Severity.MODERATE: 0.05,
    Severity.HIGH: 0.15,
    Severity.CRITICAL: 0.25,
}

#: Notch penalty contributed by the worst adverse external finding.
SEVERITY_NOTCHES: dict[Severity, float] = {
    Severity.NONE: 0.0,
    Severity.LOW: 0.0,
    Severity.MODERATE: 0.5,
    Severity.HIGH: 1.0,
    Severity.CRITICAL: 2.0,
}

#: Each additional adverse finding at moderate or above adds this much.
ADDITIONAL_FINDING_NOTCHES = 0.25

#: Ceiling on total adverse notching from external research.
MAX_RISK_NOTCHES = 3.0


def country_tier(country_code: str) -> int:
    return COUNTRY_RISK_TIERS.get(country_code.upper(), DEFAULT_COUNTRY_TIER)


def industry_tier(industry_code: str) -> int:
    return INDUSTRY_RISK_TIERS.get(industry_code.upper(), DEFAULT_INDUSTRY_TIER)


def concentration_severity(largest_share_percent: float) -> Severity:
    for threshold, severity in CONCENTRATION_BANDS:
        if largest_share_percent >= threshold:
            return severity
    return Severity.NONE
