"""The dispute case state machine.

Every transition is validated and recorded as a ``CaseEvent`` with the agent
handing off, so the full history of a case is replayable from the database.
"""

from __future__ import annotations

from enum import Enum


class CaseState(str, Enum):
    RECEIVED = "received"
    INGESTED = "ingested"
    AUDITED = "audited"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    NEEDS_INFO = "needs_info"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    DISMISSED = "dismissed"


class Agent(str, Enum):
    INGESTION = "ingestion"
    AUDITOR = "auditor"
    NEGOTIATION = "negotiation"
    SUPERVISOR = "supervisor"
    SYSTEM = "system"


TERMINAL_STATES = frozenset(
    {CaseState.RESOLVED, CaseState.REJECTED, CaseState.DISMISSED}
)

ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.RECEIVED: frozenset(
        {CaseState.INGESTED, CaseState.NEEDS_INFO, CaseState.DISMISSED}
    ),
    CaseState.INGESTED: frozenset(
        {CaseState.AUDITED, CaseState.NEEDS_INFO, CaseState.DISMISSED}
    ),
    CaseState.AUDITED: frozenset({CaseState.DRAFTED, CaseState.NEEDS_INFO}),
    CaseState.DRAFTED: frozenset(
        {CaseState.AWAITING_APPROVAL, CaseState.RESOLVED, CaseState.NEEDS_INFO}
    ),
    CaseState.AWAITING_APPROVAL: frozenset(
        {CaseState.RESOLVED, CaseState.REJECTED, CaseState.NEEDS_INFO}
    ),
    CaseState.NEEDS_INFO: frozenset(
        {CaseState.AUDITED, CaseState.RESOLVED, CaseState.DISMISSED}
    ),
    CaseState.RESOLVED: frozenset(),
    CaseState.REJECTED: frozenset(),
    CaseState.DISMISSED: frozenset(),
}

# Which agent owns a case while it sits in a given state.
STATE_OWNER: dict[CaseState, Agent] = {
    CaseState.RECEIVED: Agent.INGESTION,
    CaseState.INGESTED: Agent.AUDITOR,
    CaseState.AUDITED: Agent.NEGOTIATION,
    CaseState.DRAFTED: Agent.NEGOTIATION,
    CaseState.AWAITING_APPROVAL: Agent.SUPERVISOR,
    CaseState.NEEDS_INFO: Agent.NEGOTIATION,
    CaseState.RESOLVED: Agent.SYSTEM,
    CaseState.REJECTED: Agent.SYSTEM,
    CaseState.DISMISSED: Agent.SYSTEM,
}


class InvalidTransitionError(RuntimeError):
    pass


def assert_transition(current: CaseState | str, target: CaseState | str) -> None:
    current_state = CaseState(current)
    target_state = CaseState(target)
    if target_state not in ALLOWED_TRANSITIONS[current_state]:
        raise InvalidTransitionError(
            f"cannot move a case from {current_state.value} to {target_state.value}"
        )
