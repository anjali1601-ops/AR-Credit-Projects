"""The LangGraph state machine wiring the collections agents together.

    START -> ingest -> (profiler | sentiment) -> strategy
                                                   |
                                  approval_gate <--+--> communications
                                        |                    |
                                        +--> communications ->+
                                                              v
                                                            review --(issues)--> communications
                                                              |
                                                            (clean)
                                                              v
                                                           finalize -> END

Profiler and sentiment fan out in parallel from the same shared state and fan
back in at `strategy`; `review` can push drafts back to `communications` with
compliance feedback, so the graph really does contain a cycle.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .agents.communications import draft_sequence
from .agents.profiler import build_profile
from .agents.review import review_sequence
from .agents.sentiment_agent import assess_sentiment
from .agents.strategy import decide_strategy
from .config import Settings, get_settings
from .data.repository import AccountRepository
from .domain import RunResult
from .providers.llm import LLMProvider, get_llm_provider
from .providers.sentiment import SentimentProvider, get_sentiment_provider
from .providers.tracing import BaseTracer, RunHandle, get_tracer
from .state import DunningState


@dataclass
class GraphDependencies:
    """Everything the nodes need, so providers stay swappable and testable."""

    settings: Settings
    repository: AccountRepository
    llm: LLMProvider
    sentiment: SentimentProvider
    tracer: BaseTracer

    @classmethod
    def build(cls, settings: Settings | None = None, **overrides) -> "GraphDependencies":
        settings = settings or get_settings()
        return cls(
            settings=settings,
            repository=overrides.get("repository") or AccountRepository(settings),
            llm=overrides.get("llm") or get_llm_provider(settings),
            sentiment=overrides.get("sentiment") or get_sentiment_provider(settings),
            tracer=overrides.get("tracer") or get_tracer(settings),
        )


@contextmanager
def _span(config: RunnableConfig, name: str, payload: dict[str, Any] | None = None) -> Iterator[Any]:
    handle: RunHandle | None = (config or {}).get("configurable", {}).get("run_handle")
    if handle is None:
        yield None
        return
    with handle.span(name, input=payload or {}) as record:
        yield record


def _update(span: Any, output: dict[str, Any]) -> None:
    if span is not None:
        span.update(output=output)


def build_graph(deps: GraphDependencies):
    def ingest(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        with _span(config, "ingest", {"account_id": state["account_id"]}) as span:
            snapshot = deps.repository.snapshot(state["account_id"])
            _update(
                span,
                {
                    "customer": snapshot.customer.name,
                    "invoices": int(len(snapshot.invoices)),
                    "emails": int(len(snapshot.emails)),
                    "as_of": snapshot.as_of.isoformat(),
                },
            )
        return {
            "snapshot": snapshot,
            "revisions": 0,
            "feedback": [],
            "agent_log": [f"ingest: loaded {len(snapshot.invoices)} invoices and {len(snapshot.emails)} emails"],
        }

    def profiler(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        snapshot = state["snapshot"]
        with _span(config, "profiler_agent", {"account_id": snapshot.customer.account_id}) as span:
            profile = build_profile(snapshot, deps.llm)
            _update(
                span,
                {
                    "archetype": profile.archetype,
                    "risk_score": profile.risk_score,
                    "risk_band": profile.risk_band,
                    "signals": profile.signals,
                },
            )
        return {
            "profile": profile,
            "agent_log": [
                f"profiler: {profile.archetype} / risk {profile.risk_score:.0f} ({profile.risk_band}), "
                f"expected {profile.expected_days_late:.0f} days late"
            ],
        }

    def sentiment(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        snapshot = state["snapshot"]
        with _span(config, "sentiment_agent", {"account_id": snapshot.customer.account_id}) as span:
            assessment = assess_sentiment(snapshot, deps.sentiment, deps.llm)
            _update(
                span,
                {
                    "relationship_label": assessment.relationship_label,
                    "relationship_health": assessment.relationship_health,
                    "polarity": assessment.polarity_score,
                    "backend": assessment.backend,
                },
            )
        return {
            "sentiment": assessment,
            "agent_log": [
                f"sentiment[{assessment.backend}]: {assessment.relationship_label} "
                f"(health {assessment.relationship_health:.0f}, engagement {assessment.engagement_trend})"
            ],
        }

    def strategy(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        with _span(
            config,
            "strategy_agent",
            {
                "archetype": state["profile"].archetype,
                "risk_score": state["profile"].risk_score,
                "relationship": state["sentiment"].relationship_label,
            },
        ) as span:
            decision = decide_strategy(state["snapshot"], state["profile"], state["sentiment"])
            _update(
                span,
                {
                    "stage": decision.stage,
                    "tone": decision.tone,
                    "channels": decision.channels,
                    "steps": len(decision.plan),
                    "policy_trace": decision.policy_trace,
                },
            )
        return {
            "strategy": decision,
            "agent_log": [
                f"strategy: {decision.stage} / {decision.tone} across {', '.join(decision.channels)} "
                f"({len(decision.plan)} steps, urgency {decision.urgency})"
            ],
        }

    def approval_gate(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        decision = state["strategy"]
        approver = "Collections Manager" if decision.legal_referral else state["snapshot"].customer.ar_owner
        note = f"held for human approval by {approver} before any message is sent"
        with _span(config, "approval_gate", {"stage": decision.stage, "legal_referral": decision.legal_referral}) as span:
            decision = decision.model_copy(update={"policy_trace": decision.policy_trace + [f"governance: {note}"]})
            _update(span, {"approver": approver})
        return {"strategy": decision, "agent_log": [f"approval_gate: {note}"]}

    def communications(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        feedback = state.get("feedback") or []
        with _span(
            config,
            "communications_agent",
            {"stage": state["strategy"].stage, "tone": state["strategy"].tone, "feedback": feedback},
        ) as span:
            sequence = draft_sequence(
                state["snapshot"], state["profile"], state["sentiment"], state["strategy"], deps.llm, feedback
            )
            _update(
                span,
                {
                    "steps": [
                        {"step": s.step_number, "channel": s.channel, "day": s.day_offset, "subject": s.subject}
                        for s in sequence.steps
                    ],
                    "generated_by": sequence.generated_by,
                },
            )
        revision = state.get("revisions", 0)
        log = f"communications: drafted {len(sequence.steps)} steps" + (f" (revision {revision})" if revision else "")
        return {"sequence": sequence, "agent_log": [log]}

    def review(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        with _span(config, "compliance_review", {"steps": len(state["sequence"].steps)}) as span:
            result = review_sequence(state["sequence"], state["strategy"])
            _update(span, {"passed": result.passed, "issues": result.issues, "checks_run": result.checks_run})
        revisions = state.get("revisions", 0)
        update: dict[str, Any] = {
            "review": result,
            "agent_log": [
                f"review: {result.checks_run} checks, "
                + ("passed" if result.passed else f"{len(result.issues)} issue(s): {'; '.join(result.issues)}")
            ],
        }
        if not result.passed and revisions < deps.settings.max_draft_revisions:
            update["feedback"] = result.issues
            update["revisions"] = revisions + 1
        return update

    def finalize(state: DunningState, config: RunnableConfig) -> dict[str, Any]:
        sequence = state["sequence"]
        with _span(config, "finalize", {"steps": len(sequence.steps)}) as span:
            summary = (
                f"{sequence.stage} sequence of {len(sequence.steps)} steps over "
                f"{sequence.steps[-1].day_offset if sequence.steps else 0} days"
            )
            _update(span, {"summary": summary, "approved": state["review"].passed})
        return {"agent_log": [f"finalize: {summary}"]}

    def route_after_strategy(state: DunningState) -> str:
        decision = state["strategy"]
        return "approval_gate" if (decision.human_approval_required or decision.legal_referral) else "communications"

    def route_after_review(state: DunningState) -> str:
        result = state["review"]
        if result.passed or state.get("revisions", 0) >= deps.settings.max_draft_revisions:
            return "finalize"
        return "communications"

    builder = StateGraph(DunningState)
    builder.add_node("ingest", ingest)
    builder.add_node("profiler", profiler)
    builder.add_node("sentiment", sentiment)
    builder.add_node("strategy", strategy)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("communications", communications)
    builder.add_node("review", review)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "profiler")
    builder.add_edge("ingest", "sentiment")
    builder.add_edge("profiler", "strategy")
    builder.add_edge("sentiment", "strategy")
    builder.add_conditional_edges(
        "strategy", route_after_strategy, {"approval_gate": "approval_gate", "communications": "communications"}
    )
    builder.add_edge("approval_gate", "communications")
    builder.add_edge("communications", "review")
    builder.add_conditional_edges(
        "review", route_after_review, {"communications": "communications", "finalize": "finalize"}
    )
    builder.add_edge("finalize", END)
    return builder.compile()


def run_account(
    account_id: str,
    deps: GraphDependencies | None = None,
    *,
    run_id: str | None = None,
) -> RunResult:
    """Execute the full agent graph for one account and return the assembled result."""
    deps = deps or GraphDependencies.build()
    graph = build_graph(deps)
    run_id = run_id or uuid.uuid4().hex[:12]

    with deps.tracer.run(
        "dunning_sequence",
        run_id=run_id,
        metadata={
            "account_id": account_id,
            "llm_provider": getattr(deps.llm, "name", "unknown"),
            "sentiment_backend": getattr(deps.sentiment, "name", "unknown"),
            "as_of": deps.settings.as_of.isoformat(),
        },
    ) as handle:
        final = graph.invoke(
            {"account_id": account_id},
            config={"configurable": {"run_handle": handle, "thread_id": run_id}},
        )
        trace_reference = handle.reference

    snapshot = final["snapshot"]
    return RunResult(
        account_id=account_id,
        run_id=run_id,
        as_of=snapshot.as_of,
        customer=snapshot.customer,
        profile=final["profile"],
        sentiment=final["sentiment"],
        strategy=final["strategy"],
        sequence=final["sequence"],
        review=final["review"],
        revisions=final.get("revisions", 0),
        agent_log=final.get("agent_log", []),
        trace_reference=trace_reference,
    )
