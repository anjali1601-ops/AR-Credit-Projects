"""The supervisor-led underwriting graph.

    START
      |
      v
  supervisor_plan ------------------+
      |                             |
      v                             v
 financial_analyst            risk_searcher        (fan-out, run concurrently)
      |                             |
      +-------------+---------------+
                    v
           supervisor_reconcile                    (fan-in, resolves disagreement)
                    |
                    v
              memo_writer <------------+
                    |                  |
                    v                  | revise, bounded by max_memo_revisions
               memo_critic ------------+
                    |
                    v
                   END

The two specialists are independent, so both edges leave ``supervisor_plan`` and
LangGraph runs them in the same superstep; their writes to ``evidence`` and
``trace`` merge through the reducers declared on the state. The critic's
conditional edge is what closes the revise loop, and the revision counter on the
state is what bounds it.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .agents import critic, financial_analyst, memo_writer, reconciler, risk_searcher, supervisor
from .agents.context import GraphContext
from .state import UnderwritingState

NODE_SUPERVISOR_PLAN = "supervisor_plan"
NODE_FINANCIAL_ANALYST = "financial_analyst"
NODE_RISK_SEARCHER = "risk_searcher"
NODE_RECONCILE = "supervisor_reconcile"
NODE_MEMO_WRITER = "memo_writer"
NODE_MEMO_CRITIC = "memo_critic"


def should_revise(state: UnderwritingState, max_revisions: int) -> str:
    """Route the critic's verdict.

    A failed memo goes back to the writer until the revision ceiling is reached,
    at which point the run completes with the outstanding issues recorded rather
    than looping forever.
    """
    critique = state.get("critique")
    if critique is None or critique.passed:
        return END
    if int(state.get("revision", 0)) >= max_revisions:
        return END
    return NODE_MEMO_WRITER


def build_graph(context: GraphContext):
    """Compile the underwriting graph for a given context."""
    builder = StateGraph(UnderwritingState)

    builder.add_node(NODE_SUPERVISOR_PLAN, partial(_run, supervisor.plan, context))
    builder.add_node(NODE_FINANCIAL_ANALYST, partial(_run, financial_analyst.analyse, context))
    builder.add_node(NODE_RISK_SEARCHER, partial(_run, risk_searcher.research, context))
    builder.add_node(NODE_RECONCILE, partial(_run, reconciler.reconcile, context))
    builder.add_node(NODE_MEMO_WRITER, partial(_run, memo_writer.write, context))
    builder.add_node(NODE_MEMO_CRITIC, partial(_run, critic.review, context))

    builder.add_edge(START, NODE_SUPERVISOR_PLAN)

    # Fan-out: both specialists depend only on the plan, so they run together.
    builder.add_edge(NODE_SUPERVISOR_PLAN, NODE_FINANCIAL_ANALYST)
    builder.add_edge(NODE_SUPERVISOR_PLAN, NODE_RISK_SEARCHER)

    # Fan-in: reconciliation waits for both.
    builder.add_edge(NODE_FINANCIAL_ANALYST, NODE_RECONCILE)
    builder.add_edge(NODE_RISK_SEARCHER, NODE_RECONCILE)

    builder.add_edge(NODE_RECONCILE, NODE_MEMO_WRITER)
    builder.add_edge(NODE_MEMO_WRITER, NODE_MEMO_CRITIC)
    builder.add_conditional_edges(
        NODE_MEMO_CRITIC,
        partial(should_revise, max_revisions=context.settings.max_memo_revisions),
        {NODE_MEMO_WRITER: NODE_MEMO_WRITER, END: END},
    )

    return builder.compile()


def _run(node, context: GraphContext, state: UnderwritingState) -> dict:
    return node(state, context)
