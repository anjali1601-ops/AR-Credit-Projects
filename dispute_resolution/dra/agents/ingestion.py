"""Ingestion agent: watch the inbox, turn email into a structured claim."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from dra.agents.schemas import DisputeExtraction
from dra.llm import LLMProvider, LLMRequest, LLMTask, get_llm, parse_json_object
from dra.prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

VALID_REASON_CODES = frozenset(
    {
        "damaged_goods",
        "short_shipment",
        "unauthorized_discount",
        "pricing_discrepancy",
        "duplicate_billing",
        "service_quality",
        "other",
        "not_a_dispute",
    }
)


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


class IngestionAgent:
    name = "ingestion"

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm or get_llm()

    def extract(self, sender: str, subject: str, body: str) -> DisputeExtraction:
        messages = EXTRACTION_PROMPT.format_messages(
            sender=sender, subject=subject, body=body
        )
        request = LLMRequest(
            task=LLMTask.EXTRACT_DISPUTE,
            messages=messages,
            context={"sender": sender, "subject": subject, "body": body},
        )
        payload = parse_json_object(self._llm.generate(request).text)

        reason_code = str(payload.get("reason_code") or "other")
        if reason_code not in VALID_REASON_CODES:
            logger.warning("model returned unknown reason_code %r, coercing", reason_code)
            reason_code = "other"

        invoice_number = payload.get("invoice_number")
        extraction = DisputeExtraction(
            is_dispute=bool(payload.get("is_dispute")),
            invoice_number=str(invoice_number).upper() if invoice_number else None,
            po_number=(str(payload["po_number"]).upper() if payload.get("po_number") else None),
            reason_code=reason_code,
            reason_text=str(payload.get("reason_text") or "").strip(),
            disputed_amount=_to_decimal(payload.get("disputed_amount")),
            currency=str(payload.get("currency") or "USD"),
            customer_email=str(payload.get("customer_email") or sender),
            confidence=float(payload.get("confidence") or 0.0),
            triage_note=str(payload.get("triage_note") or ""),
            keywords=list(payload.get("keywords") or []),
        )

        # A dispute we cannot tie to an invoice is not actionable downstream.
        if extraction.is_dispute and not extraction.invoice_number:
            extraction.is_dispute = False
            extraction.triage_note = "no invoice number found in the message"
        return extraction
