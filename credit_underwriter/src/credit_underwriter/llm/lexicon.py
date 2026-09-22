"""Risk lexicon used by the offline LLM provider to classify documents.

This is the offline provider's substitute for model judgment: a severity-weighted
phrase lexicon with negation and mitigation handling. It is genuinely doing the
classification work -- reading the document text and scoring it -- rather than
reading an answer out of corpus metadata, which the seeded corpus deliberately
does not carry.

Switching ``CREDIT_UNDERWRITER_LLM_PROVIDER=openai`` replaces this module's
judgment with a real model's, while the rest of the pipeline is unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Direction, RiskCategory, Severity


@dataclass(frozen=True)
class LexiconEntry:
    phrase: str
    category: RiskCategory
    severity: Severity
    direction: Direction = Direction.ADVERSE


#: Order matters only for readability; matching is exhaustive.
ADVERSE_LEXICON: tuple[LexiconEntry, ...] = (
    # insolvency
    LexiconEntry("winding-up petition", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    LexiconEntry("winding up petition", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    LexiconEntry("insolvency", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    LexiconEntry("receivership", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    LexiconEntry("bankruptcy", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    LexiconEntry("liquidation", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    LexiconEntry("statutory demand", RiskCategory.INSOLVENCY, Severity.HIGH),
    LexiconEntry("going concern", RiskCategory.INSOLVENCY, Severity.HIGH),
    LexiconEntry("ceased trading", RiskCategory.INSOLVENCY, Severity.CRITICAL),
    # payment default
    LexiconEntry("payment default", RiskCategory.PAYMENT_DEFAULT, Severity.CRITICAL),
    LexiconEntry("placed for collection", RiskCategory.PAYMENT_DEFAULT, Severity.CRITICAL),
    LexiconEntry("referred to a collection agency", RiskCategory.PAYMENT_DEFAULT, Severity.CRITICAL),
    LexiconEntry("write-off", RiskCategory.PAYMENT_DEFAULT, Severity.HIGH),
    LexiconEntry("arrears", RiskCategory.PAYMENT_DEFAULT, Severity.HIGH),
    LexiconEntry("suspended shipments", RiskCategory.PAYMENT_DEFAULT, Severity.HIGH),
    LexiconEntry("shipments suspended", RiskCategory.PAYMENT_DEFAULT, Severity.HIGH),
    LexiconEntry("unpaid invoice", RiskCategory.PAYMENT_DEFAULT, Severity.HIGH),
    LexiconEntry("overdue", RiskCategory.PAYMENT_DEFAULT, Severity.MODERATE),
    LexiconEntry("unpaid", RiskCategory.PAYMENT_DEFAULT, Severity.MODERATE),
    LexiconEntry("extend payment terms", RiskCategory.PAYMENT_DEFAULT, Severity.MODERATE),
    # governance
    LexiconEntry("auditor resigned", RiskCategory.GOVERNANCE, Severity.HIGH),
    LexiconEntry("auditor withdraws", RiskCategory.GOVERNANCE, Severity.HIGH),
    LexiconEntry("material weakness", RiskCategory.GOVERNANCE, Severity.HIGH),
    LexiconEntry("restatement", RiskCategory.GOVERNANCE, Severity.HIGH),
    LexiconEntry("resigns with immediate effect", RiskCategory.GOVERNANCE, Severity.HIGH),
    LexiconEntry("resigned with immediate effect", RiskCategory.GOVERNANCE, Severity.HIGH),
    LexiconEntry("fraud", RiskCategory.GOVERNANCE, Severity.CRITICAL),
    LexiconEntry("misappropriation", RiskCategory.GOVERNANCE, Severity.CRITICAL),
    LexiconEntry("management-prepared", RiskCategory.GOVERNANCE, Severity.MODERATE),
    LexiconEntry("inventory valuation review", RiskCategory.GOVERNANCE, Severity.HIGH),
    # covenant
    LexiconEntry("covenant breach", RiskCategory.COVENANT, Severity.HIGH),
    LexiconEntry("covenant waiver", RiskCategory.COVENANT, Severity.HIGH),
    LexiconEntry("missed its fixed-charge coverage covenant", RiskCategory.COVENANT, Severity.HIGH),
    LexiconEntry("covenant test", RiskCategory.COVENANT, Severity.MODERATE),
    LexiconEntry("pricing step-up", RiskCategory.COVENANT, Severity.MODERATE),
    # litigation
    LexiconEntry("lawsuit", RiskCategory.LITIGATION, Severity.MODERATE),
    LexiconEntry("litigation", RiskCategory.LITIGATION, Severity.MODERATE),
    LexiconEntry("judgment lien", RiskCategory.LITIGATION, Severity.HIGH),
    LexiconEntry("unsatisfied judgment", RiskCategory.LITIGATION, Severity.HIGH),
    LexiconEntry("claims totalling", RiskCategory.LITIGATION, Severity.HIGH),
    LexiconEntry("supplier claims", RiskCategory.LITIGATION, Severity.MODERATE),
    LexiconEntry("claim", RiskCategory.LITIGATION, Severity.MODERATE),
    LexiconEntry("dispute", RiskCategory.LITIGATION, Severity.MODERATE),
    # regulatory
    LexiconEntry("regulatory penalty", RiskCategory.REGULATORY, Severity.HIGH),
    LexiconEntry("customs authority has assessed", RiskCategory.REGULATORY, Severity.HIGH),
    LexiconEntry("penalty", RiskCategory.REGULATORY, Severity.HIGH),
    LexiconEntry("sanctions", RiskCategory.REGULATORY, Severity.CRITICAL),
    LexiconEntry("misdeclared", RiskCategory.REGULATORY, Severity.MODERATE),
    LexiconEntry("annual return is overdue", RiskCategory.REGULATORY, Severity.MODERATE),
    LexiconEntry("licence revoked", RiskCategory.REGULATORY, Severity.CRITICAL),
    # security and liens
    LexiconEntry("floating charge", RiskCategory.SECURITY_AND_LIENS, Severity.HIGH),
    LexiconEntry("lien over receivables", RiskCategory.SECURITY_AND_LIENS, Severity.HIGH),
    LexiconEntry("charges have been registered", RiskCategory.SECURITY_AND_LIENS, Severity.HIGH),
    LexiconEntry("new charges", RiskCategory.SECURITY_AND_LIENS, Severity.MODERATE),
    LexiconEntry("borrowing-base advance rates", RiskCategory.SECURITY_AND_LIENS, Severity.MODERATE),
    # country
    LexiconEntry("capital controls", RiskCategory.COUNTRY, Severity.HIGH),
    LexiconEntry("currency controls", RiskCategory.COUNTRY, Severity.HIGH),
    LexiconEntry("transfer delays", RiskCategory.COUNTRY, Severity.HIGH),
    LexiconEntry("recovery rates on unsecured claims are low", RiskCategory.COUNTRY, Severity.HIGH),
    LexiconEntry("weaker end of our scale", RiskCategory.COUNTRY, Severity.HIGH),
    LexiconEntry("enforcement of trade debts through the commercial courts is slow", RiskCategory.COUNTRY, Severity.MODERATE),
    # industry
    LexiconEntry("margin compression", RiskCategory.INDUSTRY, Severity.MODERATE),
    LexiconEntry("counterparty failures", RiskCategory.INDUSTRY, Severity.HIGH),
    LexiconEntry("overcapacity", RiskCategory.INDUSTRY, Severity.MODERATE),
    LexiconEntry("rates declined", RiskCategory.INDUSTRY, Severity.MODERATE),
    LexiconEntry("spot rates declined", RiskCategory.INDUSTRY, Severity.MODERATE),
    LexiconEntry("profit warning", RiskCategory.INDUSTRY, Severity.HIGH),
    LexiconEntry("layoffs", RiskCategory.OPERATIONS, Severity.MODERATE),
    LexiconEntry("recall", RiskCategory.OPERATIONS, Severity.MODERATE),
    # concentration
    LexiconEntry("single-customer concentration", RiskCategory.CONCENTRATION, Severity.MODERATE),
    LexiconEntry("concentration", RiskCategory.CONCENTRATION, Severity.MODERATE),
    # trade payment ("days beyond terms" is handled by QUANTIFIED_RULES, because
    # 2 days beyond terms and 46 days beyond terms are not the same signal)
    LexiconEntry("past due", RiskCategory.TRADE_PAYMENT, Severity.MODERATE),
    LexiconEntry("deteriorating", RiskCategory.TRADE_PAYMENT, Severity.MODERATE),
    LexiconEntry("reduced or withdrawn open-account lines", RiskCategory.TRADE_PAYMENT, Severity.HIGH),
)

SUPPORTIVE_LEXICON: tuple[LexiconEntry, ...] = (
    LexiconEntry("multi-year", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("renewed", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("renewal", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("record backlog", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("backlog", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("expansion", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("without new borrowing", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("remains strong", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("favours incumbent", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("good standing", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("filed on time", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("within terms", RiskCategory.TRADE_PAYMENT, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("pays within terms", RiskCategory.TRADE_PAYMENT, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("unconditional guarantee", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("unrestricted", RiskCategory.COUNTRY, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("strongest country risk category", RiskCategory.COUNTRY, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("in line with historical norms", RiskCategory.COUNTRY, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("predictable", RiskCategory.COUNTRY, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("fully performing", RiskCategory.POSITIVE_MOMENTUM, Severity.NONE, Direction.SUPPORTIVE),
    LexiconEntry("below the manufacturing average", RiskCategory.INDUSTRY, Severity.NONE, Direction.SUPPORTIVE),
)

@dataclass(frozen=True)
class QuantifiedRule:
    """A phrase whose severity depends on the number attached to it."""

    pattern: str
    category: RiskCategory
    #: Ascending thresholds mapped to severity; the last band at or below the
    #: measured value wins.
    bands: tuple[tuple[float, Severity], ...]
    label: str


#: Payment slippage is only meaningful with its magnitude, so it is read as a
#: number rather than as a keyword.
QUANTIFIED_RULES: tuple[QuantifiedRule, ...] = (
    QuantifiedRule(
        pattern=r"(\d+(?:\.\d+)?)\s*days beyond terms",
        category=RiskCategory.TRADE_PAYMENT,
        bands=(
            (30.0, Severity.HIGH),
            (15.0, Severity.MODERATE),
            (8.0, Severity.LOW),
            (0.0, Severity.NONE),
        ),
        label="days beyond terms",
    ),
)

_QUANTIFIED_PATTERNS: dict[str, re.Pattern[str]] = {
    rule.pattern: re.compile(rule.pattern, re.IGNORECASE) for rule in QUANTIFIED_RULES
}

#: Systemic sources cannot evidence an obligor-level event. An industry report
#: noting that "sector insolvency rates are below average" must not be read as
#: an insolvency finding against the applicant, so adverse matches from these
#: document types are restricted to categories the source can actually speak to.
DOC_TYPE_ALLOWED_CATEGORIES: dict[str, frozenset[RiskCategory]] = {
    "industry_report": frozenset(
        {RiskCategory.INDUSTRY, RiskCategory.OPERATIONS, RiskCategory.CONCENTRATION}
    ),
    "country_report": frozenset({RiskCategory.COUNTRY, RiskCategory.REGULATORY}),
    "trade_reference": frozenset(
        {RiskCategory.TRADE_PAYMENT, RiskCategory.PAYMENT_DEFAULT}
    ),
}

#: Cues that flip an adverse match off. Checked in the text immediately before a match.
NEGATION_CUES: tuple[str, ...] = (
    "no ",
    "not ",
    "never ",
    "without ",
    "nor ",
    "neither ",
    "free of ",
    "absent ",
)
NEGATION_WINDOW = 28

#: Cues that reduce an adverse match by one severity level when present anywhere
#: in the document.
MITIGATION_CUES: tuple[str, ...] = (
    "settled and dismissed",
    "dismissed with prejudice",
    "immaterial",
    "no admission of liability",
    "claim withdrawn",
    "petition withdrawn",
)

#: Applied after doc-type capping: systemic sources describe a sector or a
#: jurisdiction, not the obligor, so they cannot carry obligor-level severity.
DOC_TYPE_SEVERITY_CAP: dict[str, Severity] = {
    "industry_report": Severity.MODERATE,
    "country_report": Severity.HIGH,
    "trade_reference": Severity.HIGH,
}

#: Systemic sources are always filed under their own category.
DOC_TYPE_FORCED_CATEGORY: dict[str, RiskCategory] = {
    "industry_report": RiskCategory.INDUSTRY,
    "country_report": RiskCategory.COUNTRY,
    "trade_reference": RiskCategory.TRADE_PAYMENT,
}

#: Tie-break order when two categories score equally.
CATEGORY_PRIORITY: tuple[RiskCategory, ...] = (
    RiskCategory.INSOLVENCY,
    RiskCategory.PAYMENT_DEFAULT,
    RiskCategory.GOVERNANCE,
    RiskCategory.REGULATORY,
    RiskCategory.COVENANT,
    RiskCategory.SECURITY_AND_LIENS,
    RiskCategory.LITIGATION,
    RiskCategory.COUNTRY,
    RiskCategory.CONCENTRATION,
    RiskCategory.TRADE_PAYMENT,
    RiskCategory.INDUSTRY,
    RiskCategory.OPERATIONS,
    RiskCategory.POSITIVE_MOMENTUM,
)

_PHRASE_PATTERNS: dict[str, re.Pattern[str]] = {
    entry.phrase: re.compile(rf"\b{re.escape(entry.phrase)}", re.IGNORECASE)
    for entry in (*ADVERSE_LEXICON, *SUPPORTIVE_LEXICON)
}


@dataclass(frozen=True)
class LexiconMatch:
    phrase: str
    category: RiskCategory
    severity: Severity
    direction: Direction
    position: int


def _is_negated(text: str, start: int) -> bool:
    window = text[max(0, start - NEGATION_WINDOW) : start].lower()
    return any(cue in window for cue in NEGATION_CUES)


def find_matches(text: str) -> list[LexiconMatch]:
    """All non-negated lexicon and quantified-rule hits in ``text``."""
    matches: list[LexiconMatch] = []
    for entry in (*ADVERSE_LEXICON, *SUPPORTIVE_LEXICON):
        pattern = _PHRASE_PATTERNS[entry.phrase]
        for m in pattern.finditer(text):
            if entry.direction is Direction.ADVERSE and _is_negated(text, m.start()):
                continue
            matches.append(
                LexiconMatch(
                    phrase=entry.phrase,
                    category=entry.category,
                    severity=entry.severity,
                    direction=entry.direction,
                    position=m.start(),
                )
            )

    for rule in QUANTIFIED_RULES:
        for m in _QUANTIFIED_PATTERNS[rule.pattern].finditer(text):
            value = float(m.group(1))
            severity = next(
                (sev for threshold, sev in rule.bands if value >= threshold), Severity.NONE
            )
            if severity is Severity.NONE:
                # Comfortably inside terms is a supportive payment signal.
                matches.append(
                    LexiconMatch(
                        phrase=f"{value:g} {rule.label}",
                        category=rule.category,
                        severity=Severity.NONE,
                        direction=Direction.SUPPORTIVE,
                        position=m.start(),
                    )
                )
                continue
            matches.append(
                LexiconMatch(
                    phrase=f"{value:g} {rule.label}",
                    category=rule.category,
                    severity=severity,
                    direction=Direction.ADVERSE,
                    position=m.start(),
                )
            )

    return sorted(matches, key=lambda m: (m.position, m.phrase))


def has_mitigation(text: str) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in MITIGATION_CUES)


@dataclass(frozen=True)
class Classification:
    category: RiskCategory
    severity: Severity
    direction: Direction
    matched_phrases: tuple[str, ...]
    mitigated: bool
    capped_by_doc_type: bool


def classify(text: str, doc_type: str) -> Classification:
    """Score a document into a category, severity, and direction.

    Category selection takes the category with the highest single severity and
    breaks ties on aggregate weight, so a document that mentions a lawsuit twice
    and the word "unpaid" once is filed as litigation rather than as a default.
    """
    matches = find_matches(text)
    allowed = DOC_TYPE_ALLOWED_CATEGORIES.get(doc_type)
    adverse = [
        m
        for m in matches
        if m.direction is Direction.ADVERSE
        and (allowed is None or m.category in allowed)
    ]
    supportive = [m for m in matches if m.direction is Direction.SUPPORTIVE]

    if not adverse:
        category = DOC_TYPE_FORCED_CATEGORY.get(
            doc_type,
            supportive[0].category if supportive else RiskCategory.OPERATIONS,
        )
        return Classification(
            category=category,
            severity=Severity.NONE,
            direction=Direction.SUPPORTIVE if supportive else Direction.NEUTRAL,
            matched_phrases=tuple(m.phrase for m in (supportive or matches)),
            mitigated=False,
            capped_by_doc_type=False,
        )

    by_category: dict[RiskCategory, tuple[Severity, int]] = {}
    for m in adverse:
        current_max, weight = by_category.get(m.category, (Severity.NONE, 0))
        by_category[m.category] = (
            m.severity if m.severity.rank > current_max.rank else current_max,
            weight + m.severity.rank,
        )

    def sort_key(item: tuple[RiskCategory, tuple[Severity, int]]) -> tuple[int, int, int]:
        category, (max_severity, weight) = item
        return (max_severity.rank, weight, -CATEGORY_PRIORITY.index(category))

    category, (severity, _) = max(by_category.items(), key=sort_key)

    mitigated = has_mitigation(text)
    if mitigated:
        severity = severity.de_escalate()

    capped = False
    cap = DOC_TYPE_SEVERITY_CAP.get(doc_type)
    if cap is not None and severity.rank > cap.rank:
        severity = cap
        capped = True
    forced = DOC_TYPE_FORCED_CATEGORY.get(doc_type)
    if forced is not None:
        category = forced

    # An adverse document is always worth at least a mention in the memo.
    if severity is Severity.NONE:
        severity = Severity.LOW

    return Classification(
        category=category,
        severity=severity,
        direction=Direction.ADVERSE,
        matched_phrases=tuple(dict.fromkeys(m.phrase for m in adverse)),
        mitigated=mitigated,
        capped_by_doc_type=capped,
    )
