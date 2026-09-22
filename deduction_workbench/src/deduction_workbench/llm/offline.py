"""Deterministic clerk note built only from the decision already made."""

from __future__ import annotations

from deduction_workbench.llm.base import Narrator
from deduction_workbench.models import NarrativeBrief


class OfflineNarrator(Narrator):
    name = "offline"

    def narrate(self, brief: NarrativeBrief) -> str:
        failed = [check for check in brief.checks if not check.passed]
        passed = [check for check in brief.checks if check.passed]
        if brief.outcome == "valid":
            judgement = (
                f"Every policy check passed ({len(passed)} of {len(brief.checks)})."
            )
        elif failed:
            names = ", ".join(check.name for check in failed)
            judgement = f"Policy failed on {names}."
        else:
            judgement = "Policy did not pass."
        excerpt = brief.citation_text.replace("\n", " ").strip()
        if len(excerpt) > 220:
            excerpt = excerpt[:217].rstrip() + "..."
        amount = f"${brief.claimed_amount:,.2f}"
        return (
            f"{brief.customer_name} deducted {amount} on {brief.debit_memo} against "
            f"{brief.invoice_number}. The backup reads as {brief.reason_label}. "
            f"{judgement} Cite {brief.citation_id}: \"{excerpt}\" "
            f"Recommended route: {brief.queue_label}. "
            f"The route is waiting for a clerk. Nothing has been sent."
        )
