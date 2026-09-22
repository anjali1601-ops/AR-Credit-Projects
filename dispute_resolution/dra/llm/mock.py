"""Deterministic offline provider — the default, and what CI runs against."""

from __future__ import annotations

import json

from dra.llm.base import LLMProvider, LLMRequest, LLMTask
from dra.llm.offline import drafting, extraction, sql_planner


class MockLLM(LLMProvider):
    name = "mock"
    model = "deterministic-offline-v1"

    _HANDLERS = {
        LLMTask.EXTRACT_DISPUTE: extraction.extract_dispute,
        LLMTask.GENERATE_SQL: sql_planner.plan_sql,
        LLMTask.DRAFT_REBUTTAL: drafting.draft_rebuttal,
        LLMTask.DRAFT_CREDIT_MEMO: drafting.draft_credit_memo,
        LLMTask.DRAFT_SUPERVISOR_EMAIL: drafting.draft_supervisor_email,
        LLMTask.DRAFT_INFO_REQUEST: drafting.draft_info_request,
    }

    def _generate(self, request: LLMRequest) -> str:
        handler = self._HANDLERS.get(request.task)
        if handler is None:  # pragma: no cover - guarded by the enum
            raise NotImplementedError(f"offline provider has no handler for {request.task}")
        return json.dumps(handler(request.context), default=str)
