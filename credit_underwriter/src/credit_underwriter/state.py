"""Shared graph state.

The two specialists run concurrently and both write to ``evidence`` and
``trace``, so those channels carry reducers. Everything else is written by
exactly one node, which keeps the merge semantics obvious.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    CreditApplication,
    CreditDecision,
    Critique,
    EvidenceItem,
    FinancialAnalysis,
    RiskAssessment,
    UnderwritingMemo,
)


class AgentMessage(BaseModel):
    """One line of the run trace, used for the audit trail and the CLI output."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    action: str
    detail: str
    metrics: dict[str, Any] = Field(default_factory=dict)


def merge_evidence(
    left: list[EvidenceItem], right: list[EvidenceItem]
) -> list[EvidenceItem]:
    """Union by evidence id, preserving first-seen order.

    Both specialists register the application-level facts they rely on, so the
    same id legitimately arrives from two branches.
    """
    merged: dict[str, EvidenceItem] = {item.evidence_id: item for item in left}
    for item in right:
        existing = merged.get(item.evidence_id)
        if existing is None:
            merged[item.evidence_id] = item
        elif existing != item:
            merged[item.evidence_id] = item.model_copy(
                update={
                    "numeric_values": sorted(
                        {*existing.numeric_values, *item.numeric_values}
                    )
                }
            )
    return list(merged.values())


class UnderwritingState(TypedDict, total=False):
    run_id: str
    application: CreditApplication
    plan: list[str]
    trace: Annotated[list[AgentMessage], operator.add]
    evidence: Annotated[list[EvidenceItem], merge_evidence]
    financial_analysis: FinancialAnalysis
    risk_assessment: RiskAssessment
    decision: CreditDecision
    memo: UnderwritingMemo
    critique: Critique
    critique_history: Annotated[list[Critique], operator.add]
    revision: int
