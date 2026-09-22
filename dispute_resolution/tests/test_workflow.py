"""The state machine itself: legal transitions and persisted handoffs."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from dra.db.models import DisputeCase
from dra.db.session import session_scope
from dra.workflow import DisputeOrchestrator, InvalidTransitionError, assert_transition
from dra.workflow.states import CaseState


def test_the_happy_path_is_the_only_way_forward() -> None:
    assert_transition(CaseState.RECEIVED, CaseState.INGESTED)
    assert_transition(CaseState.INGESTED, CaseState.AUDITED)
    assert_transition(CaseState.AUDITED, CaseState.DRAFTED)
    assert_transition(CaseState.DRAFTED, CaseState.AWAITING_APPROVAL)
    assert_transition(CaseState.AWAITING_APPROVAL, CaseState.RESOLVED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CaseState.RECEIVED, CaseState.RESOLVED),
        (CaseState.INGESTED, CaseState.AWAITING_APPROVAL),
        (CaseState.AUDITED, CaseState.RESOLVED),
        (CaseState.RESOLVED, CaseState.AWAITING_APPROVAL),
        (CaseState.REJECTED, CaseState.RESOLVED),
    ],
)
def test_shortcuts_and_reopenings_are_refused(current, target) -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)


def test_every_handoff_is_persisted_in_order(fresh_db: None) -> None:
    DisputeOrchestrator().poll_inbox(limit=25)
    with session_scope() as session:
        case = session.scalar(
            select(DisputeCase).where(DisputeCase.invoice_number == "INV-2025-0148")
        )
        trail = [(e.from_state, e.to_state, e.from_agent, e.to_agent) for e in case.events]

    assert trail == [
        ("received", "ingested", "ingestion", "auditor"),
        ("ingested", "audited", "auditor", "negotiation"),
        ("audited", "drafted", "negotiation", "negotiation"),
        ("drafted", "awaiting_approval", "negotiation", "supervisor"),
    ]


def test_a_processed_message_is_not_picked_up_twice(fresh_db: None) -> None:
    orchestrator = DisputeOrchestrator()
    first = orchestrator.poll_inbox(limit=25)
    second = orchestrator.poll_inbox(limit=25)
    assert len(first) == 5
    assert second == []


def test_approval_writes_the_credit_memo_and_closes_the_case(fresh_db: None) -> None:
    orchestrator = DisputeOrchestrator()
    orchestrator.poll_inbox(limit=25)
    with session_scope() as session:
        case_id = session.scalar(
            select(DisputeCase.id).where(DisputeCase.invoice_number == "INV-2025-0148")
        )

    result = orchestrator.approve(case_id=case_id, actor="test-supervisor")
    assert result["state"] == "resolved"
    assert result["credit_memo"].startswith("CM-")

    with session_scope() as session:
        case = session.get(DisputeCase, case_id)
        assert case.credit_memo.status == "issued"
        assert case.credit_memo.issued_by == "test-supervisor"
        # The ERP write happens here, in application code, never via Text-to-SQL.
        assert case.events[-1].event_type == "approved"
