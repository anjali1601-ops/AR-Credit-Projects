"""Risk Searcher agent.

Researches the obligor, its sector, and its jurisdiction against the local risk
corpus, classifies every retrieved document, and proposes a notch adjustment to
the financial analyst's standalone rating.

Two things are deliberately not the model's job. Severity decay by document age
and the notch arithmetic are computed here in code, because they decide how much
the rating moves. And the quantitative signals -- customer concentration, country
and industry tier -- come from policy tables, with the retrieved reports cited as
corroboration rather than as the source of the number.
"""

from __future__ import annotations

from datetime import date

from ..evidence import EvidenceRegistry, extract_numbers
from ..llm import LLMRequest
from ..models import (
    BLOCKING_CATEGORIES,
    ConcentrationSignal,
    CreditApplication,
    Direction,
    EvidenceKind,
    NarrativePoint,
    RetrievedDocument,
    RiskAssessment,
    RiskCategory,
    RiskDocument,
    RiskFinding,
    Severity,
    max_severity,
)
from ..retrieval import application_scopes, live_search_enabled, search_adverse_media
from ..retrieval.live_search import LiveSearchUnavailable
from ..risk import (
    ADDITIONAL_FINDING_NOTCHES,
    CONCENTRATION_NOTCHES,
    COUNTRY_TIER_LABELS,
    COUNTRY_TIER_NOTCHES,
    INDUSTRY_TIER_LABELS,
    INDUSTRY_TIER_NOTCHES,
    MAX_RISK_NOTCHES,
    SEVERITY_NOTCHES,
    assess_concentration,
    register_jurisdiction_evidence,
)
from ..state import AgentMessage, UnderwritingState
from .context import GraphContext

AGENT = "risk_searcher"

SYSTEM = (
    "You are a credit risk researcher screening an enterprise B2B credit applicant for "
    "adverse signals. You read one source document at a time and classify it."
)

INSTRUCTIONS = (
    "Classify the document in FACTS. Choose the single risk category that best describes "
    "it, assign a severity, and say whether it is adverse or supportive for the applicant's "
    "creditworthiness. Summarise the document in one sentence drawn from its own wording, "
    "and give a one-sentence rationale for the classification. Do not speculate beyond the "
    "document text."
)

CLASSIFY_SCHEMA = {
    "category": "one of the risk category keys listed in FACTS.allowed_categories",
    "severity": "one of none, low, moderate, high, critical",
    "direction": "one of adverse, supportive, neutral",
    "summary": "single sentence summarising the document",
    "rationale": "single sentence explaining the classification",
}

SYNTHESIS_SCHEMA = {
    "points": "array of objects with keys 'topic' (string) and 'text' (string)",
}

#: Deterministic query set. Fixed queries keep retrieval reproducible; the model
#: is not asked to invent search terms.
RISK_QUERY_TEMPLATES: tuple[str, ...] = (
    "{name} insolvency winding-up petition receivership bankruptcy liquidation",
    "{name} payment default arrears unpaid suppliers placed for collection",
    "{name} lawsuit litigation claim court judgment dispute",
    "{name} covenant breach waiver lender facility fixed charge",
    "{name} auditor resignation restatement material weakness governance",
    "{name} regulatory penalty investigation sanctions customs fine",
    "{name} liens charges registered security over inventory receivables",
    "{name} trade payment experience days beyond terms collection",
    "{name} contract wins renewals backlog expansion good standing guarantee",
    "{industry} sector outlook margin compression demand counterparty failures",
    "{country} country risk currency controls transfer delays recovery rates",
)

#: Severity is reduced one level past this age and two levels past the second
#: threshold: a settled claim from three years ago is not current news.
RECENCY_DECAY_MONTHS = 24.0
RECENCY_DECAY_MONTHS_SEVERE = 42.0
DAYS_PER_MONTH = 30.44


def research(state: UnderwritingState, context: GraphContext) -> dict:
    application = state["application"]
    registry = EvidenceRegistry(state.get("evidence", []))
    country_tier, industry_tier = register_jurisdiction_evidence(application, registry)
    concentration = assess_concentration(application, registry)

    queries = build_queries(application)
    retrieved, live_used = _retrieve(application, queries, context)

    findings: list[RiskFinding] = []
    for hit in retrieved:
        finding = _classify(hit, application, context, registry)
        findings.append(finding)

    overall, notches = aggregate_risk(
        findings, concentration, country_tier, industry_tier
    )
    blockers = [
        f.key
        for f in findings
        if f.severity is Severity.CRITICAL
        and f.direction is Direction.ADVERSE
        and f.category in BLOCKING_CATEGORIES
    ]

    assessment = RiskAssessment(
        applicant_id=application.applicant_id,
        queries=queries,
        retrieved=retrieved,
        findings=findings,
        concentration=concentration,
        country_risk_tier=country_tier,
        industry_risk_tier=industry_tier,
        overall_severity=overall,
        proposed_notches=notches,
        blockers=blockers,
        retrieval_backend=context.index.backend,
        live_search_used=live_used,
    )

    narrative = _synthesise(assessment, application, context)
    assessment = assessment.model_copy(update={"narrative": narrative})

    adverse = assessment.adverse_findings
    return {
        "risk_assessment": assessment,
        "evidence": registry.items(),
        "trace": [
            AgentMessage(
                agent=AGENT,
                action="research_external_risk",
                detail=(
                    f"Ran {len(queries)} queries against the {context.index.backend} index, "
                    f"reviewed {len(retrieved)} documents, and classified "
                    f"{len(adverse)} adverse and {len(assessment.supportive_findings)} supportive "
                    f"findings. Worst severity {overall.value}; proposing {notches:.2f} notches of "
                    "downward adjustment."
                ),
                metrics={
                    "queries": len(queries),
                    "documents_reviewed": len(retrieved),
                    "adverse_findings": len(adverse),
                    "supportive_findings": len(assessment.supportive_findings),
                    "overall_severity": overall.value,
                    "proposed_notches": notches,
                    "blockers": blockers,
                    "country_risk_tier": country_tier,
                    "industry_risk_tier": industry_tier,
                    "concentration_severity": concentration.severity.value,
                    "live_search_used": live_used,
                },
            )
        ],
    }


def build_queries(application: CreditApplication) -> list[str]:
    name = application.trading_name or application.legal_name
    return [
        template.format(
            name=application.legal_name,
            industry=application.industry,
            country=application.country,
            trading_name=name,
        )
        for template in RISK_QUERY_TEMPLATES
    ]


def _retrieve(
    application: CreditApplication, queries: list[str], context: GraphContext
) -> tuple[list[RetrievedDocument], bool]:
    """Union of top-k hits across the query set, best score per document."""
    scopes = application_scopes(application)
    best: dict[str, RetrievedDocument] = {}

    for query in queries:
        hits = context.index.search(query, context.settings.retrieval_top_k, scopes)
        for rank, (document, score) in enumerate(hits, start=1):
            existing = best.get(document.doc_id)
            if existing is None or score > existing.score:
                best[document.doc_id] = RetrievedDocument(
                    document=document, query=query, score=score, rank=rank
                )

    live_used = False
    if live_search_enabled(context.settings.enable_live_search):
        try:
            live_docs = search_adverse_media(
                application.legal_name,
                ["insolvency litigation payment default", "adverse news credit risk"],
                max_results=context.settings.live_search_max_results,
            )
        except (LiveSearchUnavailable, OSError, ValueError):
            # Live search is a supplement; a failure must not stop an underwriting
            # that the local corpus can already support.
            live_docs = []
        for rank, document in enumerate(live_docs, start=1):
            best[document.doc_id] = RetrievedDocument(
                document=document, query="live search", score=0.0, rank=rank
            )
            live_used = True

    return sorted(best.values(), key=lambda r: (-r.score, r.document.doc_id)), live_used


def _classify(
    hit: RetrievedDocument,
    application: CreditApplication,
    context: GraphContext,
    registry: EvidenceRegistry,
) -> RiskFinding:
    document = hit.document
    evidence_id = register_document_evidence(document, registry)

    response = context.provider.generate(
        LLMRequest(
            task="risk_classification",
            system=SYSTEM,
            instructions=INSTRUCTIONS,
            facts={
                "applicant": application.legal_name,
                "doc_id": document.doc_id,
                "title": document.title,
                "source": document.source,
                "doc_type": document.doc_type,
                "published_date": document.published_date,
                "text": document.text,
                "allowed_categories": [c.value for c in RiskCategory],
            },
            output_schema=CLASSIFY_SCHEMA,
            temperature=context.settings.llm_temperature,
        )
    )
    output = response.output

    raw_severity = _parse_severity(output.get("severity"))
    direction = _parse_direction(output.get("direction"))
    category = _parse_category(output.get("category"))
    months_old = document_age_months(document.published_date, context.as_of)
    severity = apply_recency_decay(raw_severity, months_old, direction)

    rationale = str(output.get("rationale", "")).strip()
    if severity is not raw_severity:
        rationale = (
            f"{rationale} Severity reduced from {raw_severity.value} to {severity.value} because "
            f"the document is {months_old:.0f} months old."
        ).strip()

    finding = RiskFinding(
        key=f"risk_{document.doc_id}",
        category=category,
        severity=severity,
        direction=direction,
        summary=str(output.get("summary", document.title)).strip() or document.title,
        rationale=rationale,
        published_date=document.published_date,
        months_old=round(months_old, 1),
        raw_severity=raw_severity,
        evidence_ids=[evidence_id],
    )

    registry.register(
        f"finding:risk:{finding.key}",
        EvidenceKind.COMPUTED_SIGNAL,
        f"External finding — {document.doc_id}",
        source=f"Risk searcher classification of {document.source}",
        display_value=finding.summary,
        detail=(
            f"category={category.value}, severity={severity.value} "
            f"(as classified: {raw_severity.value}), direction={direction.value}, "
            f"document age {months_old:.0f} months. {rationale}"
        ),
        numeric_values=[n.value for n in extract_numbers(finding.summary)],
    )
    return finding


def register_document_evidence(document: RiskDocument, registry: EvidenceRegistry) -> str:
    """Register a corpus document, including every number in its text.

    Recording the document's own figures is what allows a memo claim that quotes
    a retrieved document to pass the citation check.
    """
    return registry.register(
        f"doc:{document.doc_id}",
        EvidenceKind.DOCUMENT,
        document.title,
        source=f"{document.source}, {document.doc_type.replace('_', ' ')}, published {document.published_date}",
        display_value=document.excerpt(),
        detail=document.text,
        numeric_values=[n.value for n in extract_numbers(document.text)],
    )


def document_age_months(published_date: str, as_of: date) -> float:
    try:
        published = date.fromisoformat(published_date)
    except ValueError:
        return 0.0
    return max(0.0, (as_of - published).days / DAYS_PER_MONTH)


def apply_recency_decay(
    severity: Severity, months_old: float, direction: Direction
) -> Severity:
    if direction is not Direction.ADVERSE:
        return severity
    steps = 0
    if months_old > RECENCY_DECAY_MONTHS_SEVERE:
        steps = 2
    elif months_old > RECENCY_DECAY_MONTHS:
        steps = 1
    decayed = severity.de_escalate(steps) if steps else severity
    # An adverse document still deserves a mention even once fully decayed.
    return Severity.LOW if decayed is Severity.NONE else decayed


def aggregate_risk(
    findings: list[RiskFinding],
    concentration: ConcentrationSignal,
    country_tier: int,
    industry_tier: int,
) -> tuple[Severity, float]:
    """Worst severity plus the notch adjustment it implies.

    The worst finding sets the base penalty, and each *additional risk theme* at
    moderate or above adds a quarter notch. Counting distinct categories rather
    than documents matters: three articles about the same covenant waiver are one
    risk, and a portfolio of moderate items should not add up to the same penalty
    as a single critical one.
    """
    adverse = [f for f in findings if f.direction is Direction.ADVERSE]
    overall = max_severity(f.severity for f in adverse)

    material_categories = {
        f.category for f in adverse if f.severity.rank >= Severity.MODERATE.rank
    }
    additional = max(0, len(material_categories) - 1)

    notches = (
        SEVERITY_NOTCHES[overall]
        + ADDITIONAL_FINDING_NOTCHES * additional
        + CONCENTRATION_NOTCHES[concentration.severity]
        + COUNTRY_TIER_NOTCHES.get(country_tier, 0.0)
        + INDUSTRY_TIER_NOTCHES.get(industry_tier, 0.0)
    )
    notches = min(notches, MAX_RISK_NOTCHES)
    return overall, round(notches * 4) / 4


def _synthesise(
    assessment: RiskAssessment, application: CreditApplication, context: GraphContext
) -> list[NarrativePoint]:
    adverse = assessment.adverse_findings
    supportive = assessment.supportive_findings
    material = [f for f in adverse if f.severity.rank >= Severity.MODERATE.rank]

    topics: list[dict] = [
        {
            "topic": "search_coverage",
            "observations": [
                f"{len(assessment.queries)} standing risk queries returned "
                f"{len(assessment.retrieved)} in-scope documents from the "
                f"{assessment.retrieval_backend} index"
                + (", supplemented by live search" if assessment.live_search_used else "")
            ],
            "evidence_ids": [f"doc:{r.document.doc_id}" for r in assessment.retrieved],
        }
    ]

    if material:
        # Only the headline and the single worst item belong here; each finding is
        # written up individually further down the memo, so repeating every
        # summary would double the section for no extra information.
        worst = max(material, key=lambda f: f.severity.rank)
        categories = sorted({f.category.value.replace("_", " ") for f in material})
        topics.append(
            {
                "topic": "adverse_signals",
                "observations": [
                    f"{len(material)} material adverse findings were identified across "
                    f"{len(categories)} risk themes ({', '.join(categories)}), the most serious "
                    f"at {assessment.overall_severity.value} severity",
                    worst.summary,
                ],
                "evidence_ids": [
                    *worst.evidence_ids,
                    f"finding:risk:{worst.key}",
                    *[
                        eid
                        for f in material
                        for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                    ],
                ],
            }
        )
    else:
        topics.append(
            {
                "topic": "adverse_signals",
                "observations": [
                    "No material adverse findings were identified across the searched sources"
                ],
                "evidence_ids": [f"doc:{r.document.doc_id}" for r in assessment.retrieved],
            }
        )

    if supportive:
        topics.append(
            {
                "topic": "supportive_signals",
                "observations": [f.summary for f in supportive[:4]],
                "evidence_ids": [
                    eid for f in supportive for eid in (*f.evidence_ids, f"finding:risk:{f.key}")
                ],
            }
        )

    topics.append(
        {
            "topic": "concentration",
            "observations": [assessment.concentration.statement],
            "evidence_ids": ["signal:concentration"],
        }
    )
    topics.append(
        {
            "topic": "country_and_industry",
            "observations": [
                f"{application.country} sits at country risk tier "
                f"{assessment.country_risk_tier} of 5 "
                f"({COUNTRY_TIER_LABELS[assessment.country_risk_tier]}) and "
                f"{application.industry} at industry tier {assessment.industry_risk_tier} of 5 "
                f"({INDUSTRY_TIER_LABELS[assessment.industry_risk_tier]})"
            ],
            "evidence_ids": ["policy:country_tier", "policy:industry_tier"],
        }
    )
    topics.append(
        {
            "topic": "risk_conclusion",
            "observations": [
                f"External research supports {assessment.proposed_notches:.2f} notches of "
                f"downward adjustment to the standalone grade"
                + (
                    f", and raises {len(assessment.blockers)} finding(s) that policy treats as "
                    "a decline trigger regardless of the spread"
                    if assessment.blockers
                    else ""
                )
            ],
            "evidence_ids": [
                f"finding:risk:{key}" for key in assessment.blockers
            ]
            or ["policy:country_tier"],
        }
    )

    response = context.provider.generate(
        LLMRequest(
            task="risk_synthesis",
            system=SYSTEM,
            instructions=(
                "Summarise the external risk picture topic by topic for an underwriting memo. "
                "Use only the observations supplied. Return one entry per topic."
            ),
            facts={
                "applicant": application.legal_name,
                "overall_severity": assessment.overall_severity.value,
                "proposed_notches": assessment.proposed_notches,
                "topics": [{k: v for k, v in t.items() if k != "evidence_ids"} for t in topics],
            },
            output_schema=SYNTHESIS_SCHEMA,
            temperature=context.settings.llm_temperature,
        )
    )

    evidence_by_topic = {t["topic"]: list(dict.fromkeys(t["evidence_ids"])) for t in topics}
    return [
        NarrativePoint(
            topic=str(point["topic"]),
            text=str(point["text"]),
            evidence_ids=evidence_by_topic.get(str(point["topic"]), []),
        )
        for point in response.output.get("points", [])
        if str(point.get("text", "")).strip()
    ]


def _parse_severity(value: object) -> Severity:
    try:
        return Severity(str(value).strip().lower())
    except ValueError:
        return Severity.MODERATE


def _parse_direction(value: object) -> Direction:
    try:
        return Direction(str(value).strip().lower())
    except ValueError:
        return Direction.ADVERSE


def _parse_category(value: object) -> RiskCategory:
    try:
        return RiskCategory(str(value).strip().lower())
    except ValueError:
        return RiskCategory.OPERATIONS
