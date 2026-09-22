"""Shared graph state. Every agent reads from it and writes back its own slice."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from .domain import (
    AccountSnapshot,
    CommunicationSequence,
    ReviewResult,
    RiskProfile,
    SentimentAssessment,
    StrategyDecision,
)


class DunningState(TypedDict, total=False):
    account_id: str
    snapshot: AccountSnapshot
    profile: RiskProfile
    sentiment: SentimentAssessment
    strategy: StrategyDecision
    sequence: CommunicationSequence
    review: ReviewResult
    # Compliance issues handed back to the communications agent on a revision loop.
    feedback: list[str]
    revisions: int
    # Appended to by every node, so the CLI and traces can replay the run.
    agent_log: Annotated[list[str], operator.add]
