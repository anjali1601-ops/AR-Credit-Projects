"""Financial Analyst agent.

Runs the deterministic engine, then asks the LLM to interpret its output. The
division of labour is strict: the engine produces every number and every
threshold test, and the model only writes the interpretation. Each narrative
point carries the evidence ids of the ratios and findings it was built from, so
nothing the analyst says enters the memo uncited.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry
from ..finance import analyse_financials
from ..llm import LLMRequest
from ..models import (
    Direction,
    FinancialAnalysis,
    FinancialFinding,
    NarrativePoint,
    Ratio,
    RatioCategory,
    Severity,
    format_currency,
    max_severity,
)
from ..state import AgentMessage, UnderwritingState
from .context import GraphContext

AGENT = "financial_analyst"

SYSTEM = (
    "You are a senior credit analyst writing the financial analysis section of an "
    "underwriting memo for an enterprise B2B trade credit facility. You interpret a "
    "spread that has already been computed; you never recompute or restate figures."
)

INSTRUCTIONS = (
    "For each topic in FACTS, write one short paragraph of underwriting interpretation. "
    "State what the metrics mean for the applicant's ability to pay a trade credit "
    "balance on the requested terms. Use only the figures given for that topic and do "
    "not introduce any other number. Return one entry per topic, preserving the topic key."
)

OUTPUT_SCHEMA = {
    "points": "array of objects with keys 'topic' (string) and 'text' (string)",
}

#: Which ratios headline each narrative topic.
TOPIC_RATIOS: dict[str, tuple[str, ...]] = {
    "liquidity": ("current_ratio", "quick_ratio", "working_capital"),
    "leverage": ("net_debt_to_ebitda", "debt_to_equity", "equity_ratio"),
    "coverage": ("ebitda_interest_coverage", "debt_service_coverage"),
    "profitability": ("ebitda_margin", "net_margin", "gross_margin"),
    "cash_flow": ("fcf_to_total_debt",),
}

#: Which finding categories feed each narrative topic.
TOPIC_FINDING_CATEGORIES: dict[str, tuple[str, ...]] = {
    "liquidity": (RatioCategory.LIQUIDITY.value,),
    "leverage": (RatioCategory.LEVERAGE.value, "structure"),
    "coverage": (RatioCategory.COVERAGE.value,),
    "profitability": (RatioCategory.PROFITABILITY.value,),
    "cash_flow": ("cash_flow",),
    "trend": (RatioCategory.EFFICIENCY.value,),
    "data_quality": ("data_quality",),
}

NARRATIVE_TOPICS: tuple[str, ...] = (
    "liquidity",
    "leverage",
    "coverage",
    "profitability",
    "cash_flow",
    "trend",
    "data_quality",
)


def analyse(state: UnderwritingState, context: GraphContext) -> dict:
    application = state["application"]
    registry = EvidenceRegistry(state.get("evidence", []))

    analysis = analyse_financials(application, registry)
    topics = _build_topics(analysis)

    response = context.provider.generate(
        LLMRequest(
            task="financial_narrative",
            system=SYSTEM,
            instructions=INSTRUCTIONS,
            facts={
                "applicant": application.legal_name,
                "currency": application.currency,
                "latest_period": analysis.periods[-1],
                "requested": {
                    "limit": format_currency(application.requested_limit, application.currency),
                    "terms_days": application.requested_terms_days,
                },
                "rating": {
                    "grade": analysis.rating.grade,
                    "band": analysis.rating.band_label,
                    "composite_score": analysis.rating.composite_score,
                },
                "topics": [t["facts"] for t in topics],
            },
            output_schema=OUTPUT_SCHEMA,
            temperature=context.settings.llm_temperature,
        )
    )

    evidence_by_topic = {t["facts"]["topic"]: t["evidence_ids"] for t in topics}
    narrative = [
        NarrativePoint(
            topic=str(point["topic"]),
            text=str(point["text"]),
            evidence_ids=evidence_by_topic.get(str(point["topic"]), []),
        )
        for point in response.output.get("points", [])
        if str(point.get("text", "")).strip()
    ]
    analysis = analysis.model_copy(update={"narrative": narrative})

    adverse = [f for f in analysis.findings if f.direction is Direction.ADVERSE]
    worst = max_severity(f.severity for f in adverse)

    return {
        "financial_analysis": analysis,
        "evidence": registry.items(),
        "trace": [
            AgentMessage(
                agent=AGENT,
                action="spread_and_rate",
                detail=(
                    f"Spread {len(analysis.periods)} fiscal years, computed "
                    f"{len(analysis.ratios)} ratios and {len(analysis.trends)} trends, and "
                    f"assigned standalone grade {analysis.rating.grade} "
                    f"({analysis.rating.band_label}) on a composite score of "
                    f"{analysis.rating.composite_score:.1f}. Indicative limit "
                    f"{format_currency(analysis.limit_guidance.indicative_limit, analysis.currency)} "
                    f"constrained by {analysis.limit_guidance.binding_constraint}."
                ),
                metrics={
                    "standalone_grade": analysis.rating.grade,
                    "composite_score": analysis.rating.composite_score,
                    "ratios_computed": len(analysis.ratios),
                    "findings": len(analysis.findings),
                    "adverse_findings": len(adverse),
                    "worst_finding_severity": worst.value,
                    "spread_exceptions": len(analysis.spread_exceptions),
                    "indicative_limit": analysis.limit_guidance.indicative_limit,
                },
            )
        ],
    }


def _polarity(findings: list[FinancialFinding]) -> str:
    adverse = [f for f in findings if f.direction is Direction.ADVERSE]
    supportive = [f for f in findings if f.direction is Direction.SUPPORTIVE]
    if any(f.severity.rank >= Severity.HIGH.rank for f in adverse):
        return "adverse"
    if adverse:
        return "mixed"
    if supportive:
        return "supportive"
    return "mixed"


def _build_topics(analysis: FinancialAnalysis) -> list[dict]:
    """Assemble per-topic fact bundles plus the evidence ids behind each."""
    period = analysis.periods[-1]
    topics: list[dict] = []

    for topic in NARRATIVE_TOPICS:
        metrics: list[dict[str, str]] = []
        evidence_ids: list[str] = []

        for ratio_key in TOPIC_RATIOS.get(topic, ()):
            ratio = analysis.ratio(ratio_key, period)
            if ratio is None:
                continue
            metrics.append({"label": f"{period} {ratio.label}", "display": _display(ratio)})
            evidence_ids.append(f"ratio:{period}:{ratio_key}")

        if topic == "trend":
            for trend in analysis.trends:
                if trend.key in {"revenue", "ebitda_margin", "net_debt_to_ebitda"}:
                    metrics.append(
                        {
                            "label": f"{trend.label} {trend.first_period}–{trend.last_period}",
                            "display": (
                                f"{_fmt(trend.first_value, trend.unit, analysis.currency)} to "
                                f"{_fmt(trend.last_value, trend.unit, analysis.currency)} "
                                f"({trend.direction.value})"
                            ),
                        }
                    )
                    evidence_ids.append(f"trend:{trend.key}")

        categories = TOPIC_FINDING_CATEGORIES.get(topic, ())
        topic_findings = [f for f in analysis.findings if str(f.category) in categories]
        if topic == "trend":
            topic_findings += [
                f for f in analysis.findings if f.key in {"broad_deterioration", "broad_improvement"}
            ]

        observations = [f.statement for f in topic_findings]
        evidence_ids += [f"finding:financial:{f.key}" for f in topic_findings]
        for finding in topic_findings:
            evidence_ids += finding.evidence_ids

        if not metrics and not observations:
            continue

        topics.append(
            {
                "facts": {
                    "topic": topic,
                    "polarity": _polarity(topic_findings),
                    "metrics": metrics,
                    "observations": observations,
                },
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
            }
        )

    return topics


def _display(ratio: Ratio) -> str:
    if ratio.value is None:
        return f"not meaningful ({ratio.not_meaningful_reason})"
    return ratio.display


def _fmt(value: float, unit: str, currency: str) -> str:
    from ..models import format_ratio_value

    if unit == "currency":
        return format_currency(value, currency)
    return format_ratio_value(value, unit)
