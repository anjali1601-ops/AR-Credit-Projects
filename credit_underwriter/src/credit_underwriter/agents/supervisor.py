"""Supervisor: intake, delegation, and the fan-in reconciliation handoff.

The supervisor does not analyse anything itself. It establishes the shared facts
both specialists work from -- the application fields, the country and industry
tiers -- writes the delegation plan onto the state, and then hands the two
specialist outputs to the reconciler.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry
from ..finance.spreads import register_statement_evidence
from ..risk import register_jurisdiction_evidence
from ..state import AgentMessage, UnderwritingState
from .context import GraphContext

AGENT = "supervisor"

DELEGATION_PLAN: list[str] = [
    "financial_analyst: spread the submitted statements, compute ratios and trends, "
    "and return a standalone internal rating with a limit capacity test",
    "risk_searcher: research external and adverse signals over the risk corpus, "
    "classify each retrieved document, and return a proposed notch adjustment",
    "supervisor: reconcile the two views, resolving any disagreement under the "
    "documented precedence rules, and settle the limit and terms",
    "memo_writer: draft the underwriting memo with a citation on every claim",
    "memo_critic: check completeness and citations, and send the memo back for "
    "revision if it fails",
]


def plan(state: UnderwritingState, context: GraphContext) -> dict:
    application = state["application"]
    registry = EvidenceRegistry(state.get("evidence", []))

    register_statement_evidence(application, registry)
    country_tier, industry_tier = register_jurisdiction_evidence(application, registry)

    detail = (
        f"Accepted application from {application.legal_name} "
        f"({application.country}, {application.industry}) requesting "
        f"{application.currency} {application.requested_limit:,.0f} on net "
        f"{application.requested_terms_days} day terms. Delegating to the financial "
        "analyst and the risk searcher in parallel."
    )

    return {
        "plan": DELEGATION_PLAN,
        "evidence": registry.items(),
        "revision": 0,
        "trace": [
            AgentMessage(
                agent=AGENT,
                action="delegate",
                detail=detail,
                metrics={
                    "statements_submitted": len(application.statements),
                    "country_risk_tier": country_tier,
                    "industry_risk_tier": industry_tier,
                    "corpus_documents": len(context.documents),
                    "retrieval_backend": context.index.backend,
                    "llm_provider": f"{context.provider.name}:{context.provider.model}",
                },
            )
        ],
    }
