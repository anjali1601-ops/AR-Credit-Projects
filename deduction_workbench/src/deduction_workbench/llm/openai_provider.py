"""Hosted narrator. Used only when DEDUCTION_LLM_PROVIDER=openai and a key is set.

The prompt tells the model not to change the outcome, the queue, or the
citation. Callers still persist the deterministic decision, not the prose.
"""

from __future__ import annotations

import json
import urllib.request

from deduction_workbench.llm.base import Narrator
from deduction_workbench.models import NarrativeBrief


class OpenAINarrator(Narrator):
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def narrate(self, brief: NarrativeBrief) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You write a short note for an accounts-receivable clerk. "
                        "Use only the decision in the user message. Do not change the "
                        "outcome, the queue, or the citation. Do not suggest sending "
                        "email, posting a credit, or contacting the customer. "
                        "State that nothing has been sent. 90 to 130 words."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "customer": brief.customer_name,
                            "debit_memo": brief.debit_memo,
                            "invoice": brief.invoice_number,
                            "amount": brief.claimed_amount,
                            "reason": brief.reason_label,
                            "outcome": brief.outcome,
                            "queue": brief.queue_label,
                            "citation_id": brief.citation_id,
                            "citation": brief.citation_text,
                            "checks": [
                                {"name": check.name, "passed": check.passed, "detail": check.detail}
                                for check in brief.checks
                            ],
                        }
                    ),
                },
            ],
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"].strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty narrative")
        return text
