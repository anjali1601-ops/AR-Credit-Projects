"""Negotiation agent: turn an audit decision into the artefacts a human sends."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from dra.agents.schemas import (
    AuditDecision,
    DraftedMessage,
    NegotiationOutput,
)
from dra.llm import LLMProvider, LLMRequest, LLMTask, get_llm, parse_json_object
from dra.prompts import (
    CREDIT_MEMO_PROMPT,
    INFO_REQUEST_PROMPT,
    REBUTTAL_PROMPT,
    SUPERVISOR_PROMPT,
)
from dra.settings import get_settings


def _money(value: Any, currency: str = "USD") -> str:
    if value is None:
        return "n/a"
    symbol = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}.get(currency, "")
    return f"{symbol}{Decimal(str(value)):,.2f}"


class NegotiationAgent:
    name = "negotiation"

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm or get_llm()
        self._settings = get_settings()

    # -- shared context -----------------------------------------------------

    def _context(
        self,
        case: dict[str, Any],
        decision: AuditDecision,
        links: dict[str, str],
        memo: dict[str, Any] | None,
    ) -> dict[str, Any]:
        settings = self._settings
        citation = decision.citation
        return {
            "company": {
                "name": settings.company_name,
                "ar_email": settings.company_ar_email,
                "signer_name": "Rowan Ellis",
                "signer_title": "Deductions & Disputes, Accounts Receivable",
            },
            "customer": {
                "name": case.get("customer_name", ""),
                "code": case.get("customer_code", ""),
                "contact_name": case.get("contact_name") or "Accounts Payable team",
                "contact_email": case.get("contact_email", ""),
            },
            "case": {
                "case_number": case["case_number"],
                "invoice_number": case.get("invoice_number"),
                "po_number": case.get("po_number"),
                "disputed_amount": float(case["disputed_amount"])
                if case.get("disputed_amount") is not None
                else None,
                "reason_code": case.get("reason_code", "other"),
                "currency": case.get("currency", "USD"),
            },
            "decision": {
                **decision.to_dict(),
                "clause_ref": citation.clause_ref if citation else None,
                "clause_title": citation.clause_title if citation else None,
                "clause_text": citation.text if citation else None,
                "contract_number": citation.contract_number
                if citation
                else decision.facts.get("contract_number"),
            },
            "links": links,
            "credit_memo": memo or {},
        }

    def _generate(self, task: LLMTask, messages, context: dict[str, Any]) -> dict[str, str]:
        result = self._llm.generate(
            LLMRequest(task=task, messages=messages, context=context)
        )
        payload = parse_json_object(result.text)
        return {
            "subject": str(payload.get("subject", "")).strip(),
            "body": str(payload.get("body", "")).strip(),
        }

    # -- entry point --------------------------------------------------------

    def draft(
        self,
        case: dict[str, Any],
        decision: AuditDecision,
        links: dict[str, str],
        memo_number: str | None = None,
    ) -> NegotiationOutput:
        currency = case.get("currency", "USD")
        memo: dict[str, Any] | None = None
        if decision.is_valid:
            memo = {
                "memo_number": memo_number or "CM-DRAFT",
                "amount": float(decision.recommended_credit),
                "lines": self._memo_lines(case, decision),
            }
        context = self._context(case, decision, links, memo)
        evidence_text = "\n".join(f"- {line}" for line in decision.evidence)
        clause = context["decision"]

        if decision.decision == "valid":
            memo_draft = self._generate(
                LLMTask.DRAFT_CREDIT_MEMO,
                CREDIT_MEMO_PROMPT.format_messages(
                    company_name=self._settings.company_name,
                    memo_number=memo["memo_number"],
                    customer_name=context["customer"]["name"],
                    credit_amount=_money(decision.recommended_credit, currency),
                    invoice_number=case.get("invoice_number"),
                    case_number=case["case_number"],
                    reason_code=case.get("reason_code"),
                    evidence=evidence_text,
                    clause_ref=clause.get("clause_ref") or "n/a",
                    contract_number=clause.get("contract_number") or "the agreement",
                ),
                context,
            )
            supervisor_draft = self._generate(
                LLMTask.DRAFT_SUPERVISOR_EMAIL,
                SUPERVISOR_PROMPT.format_messages(
                    company_name=self._settings.company_name,
                    case_number=case["case_number"],
                    customer_name=context["customer"]["name"],
                    disputed_amount=_money(case.get("disputed_amount"), currency),
                    invoice_number=case.get("invoice_number"),
                    reason_code=case.get("reason_code"),
                    confidence=f"{decision.confidence:.0%}",
                    credit_amount=_money(decision.recommended_credit, currency),
                    memo_number=memo["memo_number"],
                    evidence=evidence_text,
                    checks="\n".join(
                        f"- [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}"
                        for c in decision.checks
                    ),
                    approve_url=links.get("approve_url", ""),
                    reject_url=links.get("reject_url", ""),
                    case_url=links.get("case_url", ""),
                ),
                context,
            )
            return NegotiationOutput(
                drafts=[
                    DraftedMessage(
                        kind="credit_memo",
                        recipient=context["customer"]["contact_email"],
                        subject=memo_draft["subject"],
                        body=memo_draft["body"],
                    ),
                    DraftedMessage(
                        kind="supervisor_email",
                        recipient=self._settings.supervisor_email,
                        subject=supervisor_draft["subject"],
                        body=supervisor_draft["body"],
                    ),
                ],
                credit_memo_number=memo["memo_number"],
                credit_amount=decision.recommended_credit,
                requires_approval=True,
                summary=(
                    f"Claim upheld. Credit memo {memo['memo_number']} for "
                    f"{_money(decision.recommended_credit, currency)} drafted and sent to "
                    f"{self._settings.supervisor_email} for one-click approval."
                ),
            )

        if decision.decision == "invalid":
            rebuttal = self._generate(
                LLMTask.DRAFT_REBUTTAL,
                REBUTTAL_PROMPT.format_messages(
                    company_name=self._settings.company_name,
                    case_number=case["case_number"],
                    customer_name=context["customer"]["name"],
                    invoice_number=case.get("invoice_number"),
                    disputed_amount=_money(case.get("disputed_amount"), currency),
                    reason_code=case.get("reason_code"),
                    rationale=decision.rationale,
                    evidence=evidence_text,
                    clause_ref=clause.get("clause_ref") or "n/a",
                    contract_number=clause.get("contract_number") or "the agreement",
                    clause_text=clause.get("clause_text") or "",
                ),
                context,
            )
            return NegotiationOutput(
                drafts=[
                    DraftedMessage(
                        kind="customer_email",
                        recipient=context["customer"]["contact_email"],
                        subject=rebuttal["subject"],
                        body=rebuttal["body"],
                    )
                ],
                requires_approval=True,
                summary=(
                    "Claim declined. A firm rebuttal citing "
                    f"{clause.get('clause_ref') or 'the agreement'} is drafted and held for "
                    "supervisor release."
                ),
            )

        info_request = self._generate(
            LLMTask.DRAFT_INFO_REQUEST,
            INFO_REQUEST_PROMPT.format_messages(
                company_name=self._settings.company_name,
                case_number=case["case_number"],
                customer_name=context["customer"]["name"],
                invoice_number=case.get("invoice_number") or "(not stated)",
                disputed_amount=_money(case.get("disputed_amount"), currency),
                evidence=evidence_text or "- nothing conclusive on file yet",
                missing="\n".join(f"- {m}" for m in decision.missing_information),
            ),
            context,
        )
        return NegotiationOutput(
            drafts=[
                DraftedMessage(
                    kind="customer_email",
                    recipient=context["customer"]["contact_email"],
                    subject=info_request["subject"],
                    body=info_request["body"],
                )
            ],
            requires_approval=True,
            summary="Not enough evidence to rule; an information request is drafted.",
        )

    def _memo_lines(
        self, case: dict[str, Any], decision: AuditDecision
    ) -> list[dict[str, Any]]:
        facts = decision.facts
        reason = case.get("reason_code")
        amount = float(decision.recommended_credit)
        if reason == "damaged_goods" and facts.get("damaged_units"):
            return [
                {
                    "description": (
                        f"{facts['damaged_units']} unit(s) damaged in transit on "
                        f"{facts.get('delivery_date')} — credit at invoiced unit price"
                    ),
                    "amount": amount,
                }
            ]
        if reason == "short_shipment" and facts.get("shortfall_units"):
            return [
                {
                    "description": (
                        f"{facts['shortfall_units']} unit(s) invoiced but not shipped — "
                        f"credit at invoiced unit price"
                    ),
                    "amount": amount,
                }
            ]
        return [{"description": f"Approved dispute credit ({reason})", "amount": amount}]
