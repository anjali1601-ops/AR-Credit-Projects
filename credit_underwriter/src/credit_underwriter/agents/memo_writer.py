"""Memo Writer agent.

Assembles the underwriting memo from the reconciled decision. The writer decides
*which* facts belong in each section and attaches the evidence ids; the LLM only
phrases them. That ordering matters: because the citations are bound to the point
before the model sees it, a claim cannot end up with a citation that does not
support it.

On a revision pass the writer is given the critic's issues. Claims whose numbers
could not be traced are repaired -- re-cited where the number does exist
elsewhere in the registry, and otherwise rewritten without the untraceable
figure -- rather than being left in the memo.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry, extract_numbers
from ..llm import LLMRequest
from ..models import (
    Claim,
    CreditApplication,
    CreditDecision,
    Critique,
    CritiqueIssue,
    FinancialAnalysis,
    MemoSection,
    RiskAssessment,
    Severity,
    UnderwritingMemo,
    format_currency,
)
from ..state import AgentMessage, UnderwritingState
from .context import GraphContext

AGENT = "memo_writer"

SYSTEM = (
    "You are writing the final credit underwriting memo for an enterprise B2B trade credit "
    "application. Your reader is a credit committee that will approve or reject on this "
    "document alone."
)

INSTRUCTIONS = (
    "Write each point in FACTS as one clear sentence of memo prose. Keep every figure exactly "
    "as given and introduce no figure that is not in the point you are writing. Preserve the "
    "point_id so each sentence can be matched back to its evidence."
)

OUTPUT_SCHEMA = {
    "claims": "array of objects with keys 'point_id' (string) and 'text' (string)",
}

#: Section order is the memo's order. ``required`` sections must be non-empty or
#: the critic fails the memo.
SECTION_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("recommendation", "Recommendation and terms", True),
    ("applicant", "Applicant and request", True),
    ("financial_analysis", "Financial analysis", True),
    ("external_risk", "External and adverse risk findings", True),
    ("reconciliation", "Reconciliation of specialist views", True),
    ("strengths", "Key strengths", True),
    ("mitigants", "Mitigants", False),
    ("risk_factors", "Key risk factors", True),
    ("conditions", "Conditions precedent and ongoing", True),
)

REQUIRED_SECTIONS: tuple[str, ...] = tuple(key for key, _, required in SECTION_SPECS if required)


def write(state: UnderwritingState, context: GraphContext) -> dict:
    application = state["application"]
    analysis = state["financial_analysis"]
    risk = state["risk_assessment"]
    decision = state["decision"]
    registry = EvidenceRegistry(state.get("evidence", []))
    critique = state.get("critique")
    revision = int(state.get("revision", 0))
    if critique is not None and not critique.passed:
        revision += 1

    sections: list[MemoSection] = []
    for key, heading, required in SECTION_SPECS:
        points = _points_for(key, application, analysis, risk, decision)
        if not points and required:
            # A required section with nothing to say must still say so: a decline
            # memo legitimately has no credit strengths, and silently dropping the
            # heading would read as an omission rather than a finding.
            points = [_nil_point(key, decision)]
        if not points:
            continue
        claims = _draft_claims(key, heading, points, application, context, critique)
        claims = [
            _repair_claim(claim, points_by_id(points), registry) for claim in claims
        ]
        claims = [c for c in claims if c.text.strip()]
        sections.append(MemoSection(key=key, heading=heading, claims=claims))

    memo = UnderwritingMemo(
        applicant_id=application.applicant_id,
        legal_name=application.legal_name,
        recommendation=decision.recommendation,
        approved_limit=decision.approved_limit,
        approved_terms_days=decision.approved_terms_days,
        final_grade=decision.final_grade,
        final_band_label=decision.final_band_label,
        currency=application.currency,
        as_of_date=context.settings.as_of_date,
        sections=sections,
        revision=revision,
    )

    action = "draft_memo" if revision == 0 else "revise_memo"
    detail = (
        f"Drafted {len(sections)} sections containing {len(memo.claims)} cited claims."
        if revision == 0
        else (
            f"Revision {revision}: redrafted {len(sections)} sections "
            f"({len(memo.claims)} claims) to address "
            f"{len(critique.issues) if critique else 0} completeness issue(s)."
        )
    )

    return {
        "memo": memo,
        "revision": revision,
        "evidence": registry.items(),
        "trace": [
            AgentMessage(
                agent=AGENT,
                action=action,
                detail=detail,
                metrics={
                    "sections": len(sections),
                    "claims": len(memo.claims),
                    "revision": revision,
                },
            )
        ],
    }


_NIL_TEXT: dict[str, str] = {
    "strengths": (
        "No material credit strengths were identified. Neither the spread nor the external "
        "research produced a supportive finding."
    ),
    "risk_factors": "No material risk factors were identified at or above moderate severity.",
    "conditions": "No conditions are proposed beyond the standard periodic review.",
    "external_risk": (
        "External research returned no in-scope documents for this applicant, its sector, or "
        "its jurisdiction."
    ),
    "reconciliation": (
        "No disagreement arose between the financial analysis and the external risk review."
    ),
}


def _nil_point(section_key: str, decision: CreditDecision) -> dict:
    return {
        "point_id": "none_identified",
        "kind": "restate",
        "text": _NIL_TEXT.get(
            section_key, f"No items were identified for the {section_key} section."
        ),
        "evidence_ids": ["decision:recommendation"],
    }


def points_by_id(points: list[dict]) -> dict[str, dict]:
    return {str(p["point_id"]): p for p in points}


def _draft_claims(
    section_key: str,
    heading: str,
    points: list[dict],
    application: CreditApplication,
    context: GraphContext,
    critique: Critique | None,
) -> list[Claim]:
    prior_issues = (
        [
            {"code": i.code, "detail": i.detail, "claim_id": i.claim_id}
            for i in critique.issues
            if i.section_key in (None, section_key)
        ]
        if critique is not None and not critique.passed
        else []
    )

    response = context.provider.generate(
        LLMRequest(
            task="memo_section",
            system=SYSTEM,
            instructions=INSTRUCTIONS,
            facts={
                "section_key": section_key,
                "heading": heading,
                "applicant": application.legal_name,
                "currency": application.currency,
                "points": [
                    {k: v for k, v in point.items() if k != "evidence_ids"}
                    for point in points
                ],
                "prior_issues": prior_issues,
            },
            output_schema=OUTPUT_SCHEMA,
            temperature=context.settings.llm_temperature,
        )
    )

    evidence_by_point = {str(p["point_id"]): p.get("evidence_ids", []) for p in points}
    claims: list[Claim] = []
    for entry in response.output.get("claims", []):
        point_id = str(entry.get("point_id", ""))
        text = str(entry.get("text", "")).strip()
        if not text or point_id not in evidence_by_point:
            continue
        claims.append(
            Claim(
                claim_id=f"{section_key}:{point_id}",
                text=text,
                evidence_ids=list(dict.fromkeys(evidence_by_point[point_id])),
            )
        )
    return claims


def _repair_claim(
    claim: Claim, points: dict[str, dict], registry: EvidenceRegistry
) -> Claim:
    """Make a claim citable, or drop the part of it that is not.

    Runs on every claim, not only on revisions, because it is the writer's own
    guard: a provider that introduces an untraceable figure should never get as
    far as the memo.
    """
    supported = registry.numbers_for(claim.evidence_ids)
    unsupported = [n for n in extract_numbers(claim.text) if not n.is_supported_by(supported)]
    if not unsupported:
        return claim

    # Try to find the missing numbers elsewhere in the registry before rewriting.
    extra_ids: list[str] = []
    still_missing = []
    for number in unsupported:
        match = next(
            (
                item.evidence_id
                for item in registry
                if number.is_supported_by(item.numeric_values)
            ),
            None,
        )
        if match is not None:
            extra_ids.append(match)
        else:
            still_missing.append(number)

    if not still_missing:
        return claim.model_copy(
            update={"evidence_ids": list(dict.fromkeys([*claim.evidence_ids, *extra_ids]))}
        )

    fallback = points.get(claim.claim_id.split(":", 1)[1], {}).get("text")
    if fallback:
        fallback_unsupported = [
            n for n in extract_numbers(str(fallback)) if not n.is_supported_by(supported)
        ]
        if not fallback_unsupported:
            return claim.model_copy(
                update={
                    "text": str(fallback),
                    "evidence_ids": list(dict.fromkeys([*claim.evidence_ids, *extra_ids])),
                }
            )

    # Last resort: strip the untraceable figures rather than assert them.
    text = claim.text
    for number in still_missing:
        text = text.replace(number.text, "an amount not evidenced in the file")
    return claim.model_copy(
        update={
            "text": text,
            "evidence_ids": list(dict.fromkeys([*claim.evidence_ids, *extra_ids])),
        }
    )


# --------------------------------------------------------------------------------------
# Section content
# --------------------------------------------------------------------------------------


def _points_for(
    section_key: str,
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    builder = _SECTION_BUILDERS.get(section_key)
    return builder(application, analysis, risk, decision) if builder else []


def _recommendation_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    currency = application.currency
    points = [
        {
            "point_id": "headline",
            "kind": "recommendation",
            "text": (
                f"{decision.recommendation.label} a "
                f"{format_currency(decision.approved_limit, currency)} facility on net "
                f"{decision.approved_terms_days} day terms."
            ),
            "fields": {
                "recommendation": decision.recommendation.label,
                "currency_limit": format_currency(decision.approved_limit, currency),
                "terms": decision.approved_terms_days,
                "grade": decision.final_grade,
                "band": decision.final_band_label,
                "currency_requested": format_currency(decision.requested_limit, currency),
                "requested_terms": decision.requested_terms_days,
            },
            "evidence_ids": [
                "decision:recommendation",
                "app:requested_limit",
                "app:requested_terms",
                "policy:terms_cap",
            ],
        },
        {
            "point_id": "rating",
            "kind": "rating_basis",
            "text": (
                f"The final internal grade is {decision.final_grade} "
                f"({decision.final_band_label})."
            ),
            "fields": {
                "score": f"{analysis.rating.composite_score:.1f}",
                "standalone_grade": decision.standalone_grade,
                "notches": f"{decision.applied_notches:+.2f}",
                "grade": decision.final_grade,
                "band": decision.final_band_label,
                "pd": f"{_pd_for(decision.final_grade):.2f}",
            },
            "evidence_ids": ["rating:standalone", "decision:recommendation"],
        },
        {
            "point_id": "limit",
            "kind": "limit_basis",
            "text": (
                f"The approved limit of "
                f"{format_currency(decision.approved_limit, currency)} reflects the "
                f"{analysis.limit_guidance.binding_constraint} capacity test."
            ),
            "fields": {
                "binding_constraint": analysis.limit_guidance.binding_constraint,
                "capacity": format_currency(
                    min(
                        analysis.limit_guidance.tangible_net_worth_capacity,
                        analysis.limit_guidance.cash_flow_capacity,
                        analysis.limit_guidance.working_capital_capacity,
                    ),
                    currency,
                ),
                "grade": decision.final_grade,
                "multiplier": f"{_multiplier_for(decision.final_grade):.2f}",
                "haircut": f"{decision.limit_haircut_percent:.0f}",
                "limit": format_currency(decision.approved_limit, currency),
            },
            "evidence_ids": [
                "policy:limit_capacity",
                "signal:concentration",
                "decision:recommendation",
            ],
        },
        {
            "point_id": "review",
            "kind": "restate",
            "text": (
                f"The account is to be reviewed every {decision.review_frequency_months} months"
                + (
                    ", and the limit is to be supported by security as set out in the conditions."
                    if decision.security_required
                    else ", with no security required."
                )
            ),
            "evidence_ids": ["decision:recommendation", "rating:standalone"],
        },
    ]
    return points


def _applicant_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    latest = application.latest_statement
    return [
        {
            "point_id": "profile",
            "kind": "restate",
            "text": (
                f"{application.legal_name} is a {application.entity_type} in "
                f"{application.industry}, domiciled in {application.country} and trading for "
                f"{application.years_in_business:.0f} years."
            ),
            "evidence_ids": ["app:years_in_business", "app:industry", "app:country"],
        },
        {
            "point_id": "request",
            "kind": "restate",
            "text": (
                f"The applicant has requested a "
                f"{format_currency(application.requested_limit, application.currency)} open-account "
                f"limit on net {application.requested_terms_days} day terms for "
                f"{application.purpose[0].lower() + application.purpose[1:].rstrip('.')}."
            ),
            "evidence_ids": ["app:requested_limit", "app:requested_terms", "app:purpose"],
        },
        {
            "point_id": "scale",
            "kind": "restate",
            "text": (
                f"{latest.period_label} revenue was "
                f"{format_currency(latest.income_statement.revenue, application.currency)} with "
                f"EBITDA of "
                f"{format_currency(latest.income_statement.ebitda, application.currency)}, on "
                f"statements that are {_opinion_phrase(latest.opinion.value)}."
            ),
            "evidence_ids": [
                f"stmt:{latest.period_label}:income_statement.revenue",
                f"stmt:{latest.period_label}:income_statement.ebitda",
            ],
        },
        {
            "point_id": "disclosures",
            "kind": "restate",
            "text": _disclosure_sentence(application),
            "evidence_ids": ["app:disclosures"],
        },
    ]


def _financial_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    points = [
        {
            "point_id": f"narrative_{point.topic}",
            "kind": "restate",
            "text": point.text,
            "evidence_ids": point.evidence_ids,
        }
        for point in analysis.narrative
    ]
    points.append(
        {
            "point_id": "scorecard",
            "kind": "restate",
            "text": (
                "The scorecard weights leverage, coverage, liquidity, profitability, trend, "
                "balance sheet strength, and scale, and returns a composite of "
                f"{analysis.rating.composite_score:.1f} out of 100 for a standalone grade of "
                f"{analysis.rating.grade}."
            ),
            "evidence_ids": ["rating:standalone"],
        }
    )
    for exception in analysis.spread_exceptions:
        points.append(
            {
                "point_id": f"exception_{exception.check}",
                "kind": "restate",
                "text": f"Spreading exception in {exception.period}: {exception.detail}",
                "evidence_ids": [
                    f"finding:financial:spread_exception_{exception.check}_{exception.period}"
                ],
            }
        )
    return points


def _external_risk_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    points = [
        {
            "point_id": f"narrative_{point.topic}",
            "kind": "restate",
            "text": point.text,
            "evidence_ids": point.evidence_ids,
        }
        for point in risk.narrative
    ]
    for finding in risk.adverse_findings:
        if finding.severity.rank < Severity.MODERATE.rank:
            continue
        points.append(
            {
                "point_id": f"finding_{finding.key}",
                "kind": "restate",
                "text": (
                    f"{finding.summary} The searcher graded this {finding.severity.value} "
                    f"{finding.category.value.replace('_', ' ')} risk: "
                    f"{finding.rationale.rstrip('.')}."
                ),
                "evidence_ids": [f"finding:risk:{finding.key}", *finding.evidence_ids],
            }
        )
    return points


def _reconciliation_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    points: list[dict] = []
    for conflict in decision.conflicts:
        # Records where neither specialist was overruled are agreements, not
        # disagreements, and must not be written up as conflicts.
        kind = "concurrence" if conflict.prevailing_side == "both" else "conflict"
        points.append(
            {
                "point_id": f"conflict_{conflict.key}",
                "kind": kind,
                "text": (
                    f"Under the {conflict.rule.replace('_', ' ')} rule, {conflict.resolution}. "
                    f"{conflict.rationale}"
                ),
                "fields": {
                    "subject": conflict.rule.replace("_", " "),
                    "financial_position": _decapitalise(conflict.financial_position),
                    "risk_position": _decapitalise(conflict.risk_position),
                    "rule": f"the {conflict.rule.replace('_', ' ')} rule",
                    "resolution": conflict.resolution,
                },
                "evidence_ids": [f"resolution:{conflict.key}", *conflict.evidence_ids],
            }
        )
    points.append(
        {
            "point_id": "notching",
            "kind": "restate",
            "text": (
                f"Net of external risk notching and mitigant credit, the standalone grade of "
                f"{decision.standalone_grade} moved by {decision.applied_notches:+.2f} to a final "
                f"grade of {decision.final_grade}."
            ),
            "evidence_ids": ["decision:recommendation", "rating:standalone"],
        }
    )
    return points


def _strengths_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    return [
        {
            "point_id": point.key,
            "kind": "restate",
            "text": point.text,
            "evidence_ids": point.evidence_ids,
        }
        for point in decision.strengths
    ]


def _mitigant_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    return [
        {
            "point_id": offset.enhancement_key,
            "kind": "restate",
            "text": (
                f"{offset.description} This is credited {offset.notch_credit:.2f} notches against "
                f"{offset.offsets_finding}."
            ),
            "evidence_ids": [f"mitigant:{offset.enhancement_key}", *offset.evidence_ids],
        }
        for offset in decision.mitigant_offsets
    ]


def _risk_factor_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    return [
        {
            "point_id": point.key,
            "kind": "restate",
            "text": point.text,
            "evidence_ids": point.evidence_ids,
        }
        for point in decision.risk_factors
    ]


def _condition_points(
    application: CreditApplication,
    analysis: FinancialAnalysis,
    risk: RiskAssessment,
    decision: CreditDecision,
) -> list[dict]:
    return [
        {
            "point_id": point.key,
            "kind": "restate",
            "text": point.text,
            "evidence_ids": point.evidence_ids,
        }
        for point in decision.conditions
    ]


_SECTION_BUILDERS = {
    "recommendation": _recommendation_points,
    "applicant": _applicant_points,
    "financial_analysis": _financial_points,
    "external_risk": _external_risk_points,
    "reconciliation": _reconciliation_points,
    "strengths": _strengths_points,
    "mitigants": _mitigant_points,
    "risk_factors": _risk_factor_points,
    "conditions": _condition_points,
}


def _decapitalise(text: str) -> str:
    """Lower the first letter only, so the clause reads mid-sentence."""
    return text[:1].lower() + text[1:] if text else text


def _opinion_phrase(opinion: str) -> str:
    return {
        "audited": "audited",
        "reviewed": "independently reviewed but not audited",
        "management_prepared": "management-prepared and unaudited",
    }.get(opinion, opinion)


def _disclosure_sentence(application: CreditApplication) -> str:
    d = application.disclosures
    declared = [
        label
        for label, flag in (
            ("material litigation", d.material_litigation),
            ("a prior insolvency or bankruptcy", d.prior_insolvency_or_bankruptcy),
            ("a covenant breach in the last 24 months", d.covenant_breach_last_24m),
            ("tax or regulatory penalties", d.tax_or_regulatory_penalties),
            ("a pending change of control", d.change_of_control_pending),
        )
        if flag
    ]
    if declared:
        base = "On the application form the applicant declared " + _oxford(declared) + "."
    else:
        base = (
            "On the application form the applicant declared no material litigation, insolvency "
            "history, covenant breach, or penalties."
        )
    if d.notes:
        base += f" The applicant added: {d.notes}"
    return base


def _oxford(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _pd_for(grade: int) -> float:
    from ..finance.rating import ESTIMATED_PD_PERCENT

    return ESTIMATED_PD_PERCENT[grade]


def _multiplier_for(grade: int) -> float:
    from ..finance.rating import RATING_LIMIT_MULTIPLIER

    return RATING_LIMIT_MULTIPLIER[grade]


def issues_for_section(critique: Critique, section_key: str) -> list[CritiqueIssue]:
    return [i for i in critique.issues if i.section_key == section_key]
