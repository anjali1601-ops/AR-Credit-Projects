"""Auditor agent: interrogate the ERP with guarded Text-to-SQL, retrieve the
governing contract clause with RAG, and rule on the claim.

The decision itself is deliberately rule-based and evidence-linked rather than
model-generated: every outcome traces back to a row the auditor actually read
and a clause it actually retrieved, and both are stored on the case.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from dra.agents.schemas import (
    AuditDecision,
    ClauseCitation,
    DisputeExtraction,
    PolicyCheck,
)
from dra.llm import LLMProvider, get_llm
from dra.rag.contracts import retrieve_clauses
from dra.sql.text_to_sql import TextToSQLTool, dec

CENT = Decimal("0.01")
TOLERANCE = Decimal("0.01")


def to_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def money(value: Any, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}.get(currency, "")
    return f"{symbol}{dec(value):,.2f}"


def _day_word(days: int) -> str:
    return "day" if abs(days) == 1 else "days"


class AuditorAgent:
    name = "auditor"

    def __init__(
        self,
        llm: LLMProvider | None = None,
        tool: TextToSQLTool | None = None,
    ) -> None:
        self._llm = llm or get_llm()
        self.tool = tool or TextToSQLTool(self._llm)

    # -- evidence gathering -------------------------------------------------

    def gather(self, invoice_number: str) -> dict[str, Any]:
        params = {"invoice_number": invoice_number}
        results = {
            intent: self.tool.run(intent, params)
            for intent in (
                "invoice_snapshot",
                "invoice_lines",
                "payment_history",
                "shipment_records",
                "discount_agreements",
                "contract_terms",
                "duplicate_invoices",
            )
        }
        return results

    def _facts(self, results: dict[str, Any], claim_date: dt.date) -> dict[str, Any]:
        invoice = results["invoice_snapshot"].first or {}
        payments = results["payment_history"].rows
        shipment_rows = results["shipment_records"].rows
        discounts = results["discount_agreements"].rows
        contract = results["contract_terms"].first or {}
        duplicates = results["duplicate_invoices"].rows

        issue_date = to_date(invoice.get("issue_date"))
        delivery_dates = [
            d for d in (to_date(r.get("delivery_date")) for r in shipment_rows) if d
        ]
        last_delivery = max(delivery_dates) if delivery_dates else None

        damaged_units = sum(int(r.get("quantity_damaged") or 0) for r in shipment_rows)
        damaged_value = sum(
            (dec(r.get("quantity_damaged") or 0) * dec(r.get("unit_price") or 0)
             for r in shipment_rows),
            Decimal("0"),
        ).quantize(CENT)
        shortfall_units = sum(
            max(0, int(r.get("quantity_ordered") or 0) - int(r.get("quantity_shipped") or 0))
            for r in shipment_rows
        )
        shortfall_value = sum(
            (
                dec(max(0, int(r.get("quantity_ordered") or 0) - int(r.get("quantity_shipped") or 0)))
                * dec(r.get("unit_price") or 0)
                for r in shipment_rows
            ),
            Decimal("0"),
        ).quantize(CENT)

        deduction_payments = [p for p in payments if dec(p.get("deduction_amount")) > 0]
        last_payment = deduction_payments[-1] if deduction_payments else (
            payments[-1] if payments else None
        )
        payment_date = to_date(last_payment.get("payment_date")) if last_payment else None

        conditions = {str(r.get("condition_on_delivery") or "") for r in shipment_rows}
        exception_notes = sorted(
            {str(r.get("exception_notes") or "").strip() for r in shipment_rows if r.get("exception_notes")}
        )

        return {
            "invoice": invoice,
            "invoice_lines": results["invoice_lines"].rows,
            "payments": payments,
            "last_payment": last_payment,
            "payment_date": payment_date,
            "deduction_amount": dec(last_payment.get("deduction_amount")) if last_payment else Decimal("0"),
            "deduction_code": (last_payment or {}).get("deduction_code"),
            "shipments": shipment_rows,
            "shipment_numbers": sorted({str(r.get("shipment_number")) for r in shipment_rows}),
            "carriers": sorted({str(r.get("carrier")) for r in shipment_rows}),
            "pod_signed_by": next(
                (r.get("pod_signed_by") for r in shipment_rows if r.get("pod_signed_by")), None
            ),
            "conditions": sorted(c for c in conditions if c),
            "exception_notes": exception_notes,
            "damaged_units": damaged_units,
            "damaged_value": damaged_value,
            "shortfall_units": shortfall_units,
            "shortfall_value": shortfall_value,
            "issue_date": issue_date,
            "due_date": to_date(invoice.get("due_date")),
            "delivery_date": last_delivery,
            "days_since_delivery": (claim_date - last_delivery).days if last_delivery else None,
            "days_to_pay": (payment_date - issue_date).days if payment_date and issue_date else None,
            "discounts": discounts,
            "contract": contract,
            "claim_notice_days": int(contract.get("claim_notice_days") or 10),
            "duplicates": duplicates,
            "open_balance": dec(invoice.get("open_balance")),
            "total_amount": dec(invoice.get("total_amount")),
            "currency": str(invoice.get("currency") or "USD"),
            "customer_code": str(invoice.get("customer_code") or ""),
            "customer_name": str(invoice.get("customer_name") or ""),
        }

    # -- clause retrieval ---------------------------------------------------

    def _retrieve(
        self, extraction: DisputeExtraction, facts: dict[str, Any]
    ) -> list[ClauseCitation]:
        query = " ".join(
            part
            for part in (
                extraction.reason_code.replace("_", " "),
                extraction.reason_text,
                "deduction short payment claim credit memorandum",
                "proof of delivery damage" if extraction.reason_code == "damaged_goods" else "",
                "quantity shipped shortage packing list"
                if extraction.reason_code == "short_shipment"
                else "",
                "early payment discount cleared funds unauthorized deduction"
                if extraction.reason_code == "unauthorized_discount"
                else "",
            )
            if part
        )
        hits = retrieve_clauses(
            query,
            customer_code=facts.get("customer_code") or None,
            reason_code=extraction.reason_code,
        )
        return [
            ClauseCitation(
                contract_number=str(h.metadata.get("contract_number", "")),
                clause_ref=h.clause_ref,
                clause_title=h.clause_title,
                text=h.text.split("\n\n", 1)[-1].strip(),
                source_pdf=str(h.metadata.get("source_pdf", "")),
                score=round(float(h.score), 4),
            )
            for h in hits
        ]

    # -- decision -----------------------------------------------------------

    def audit(
        self,
        extraction: DisputeExtraction,
        claim_date: dt.date | None = None,
    ) -> AuditDecision:
        claim_date = claim_date or dt.date.today()

        if not extraction.invoice_number:
            return AuditDecision(
                decision="needs_more_info",
                confidence=0.3,
                rationale="The message does not reference an invoice number, so no ERP "
                "record could be pulled.",
                missing_information=["the invoice number the deduction relates to"],
            )

        results = self.gather(extraction.invoice_number)
        queries = [r.to_audit_dict() for r in results.values()]
        blocked = [q for q in queries if q["status"] != "ok"]

        invoice = results["invoice_snapshot"].first
        if not invoice:
            detail = (
                f"Text-to-SQL lookups did not complete ({blocked[0]['error']})."
                if blocked
                else f"No invoice matching {extraction.invoice_number} exists in the ERP."
            )
            return AuditDecision(
                decision="needs_more_info",
                confidence=0.35,
                rationale=detail,
                queries=queries,
                missing_information=[
                    f"a valid invoice number (we have no record of "
                    f"{extraction.invoice_number})"
                ],
            )

        facts = self._facts(results, claim_date)
        clauses = self._retrieve(extraction, facts)
        currency = facts["currency"]
        claimed = extraction.disputed_amount or facts["deduction_amount"]

        checks: list[PolicyCheck] = []
        evidence: list[str] = [
            f"Invoice {invoice['invoice_number']} for {money(facts['total_amount'], currency)} "
            f"was issued {facts['issue_date']} on PO {invoice.get('po_number') or 'n/a'}; "
            f"{money(facts['open_balance'], currency)} is still open.",
        ]
        if facts["last_payment"]:
            payment = facts["last_payment"]
            evidence.append(
                f"Payment {payment.get('payment_reference')} of "
                f"{money(payment.get('amount'), currency)} settled on {facts['payment_date']} "
                f"with a {money(payment.get('deduction_amount'), currency)} deduction coded "
                f"{payment.get('deduction_code') or 'n/a'}."
            )

        # Does the deduction on the remittance match what the email claims?
        if facts["deduction_amount"] > 0 and claimed is not None:
            matches = abs(facts["deduction_amount"] - claimed) <= TOLERANCE
            checks.append(
                PolicyCheck(
                    name="deduction matches the claim",
                    passed=matches,
                    detail=(
                        f"remittance shows {money(facts['deduction_amount'], currency)}, "
                        f"email claims {money(claimed, currency)}"
                    ),
                    weight=0.5,
                )
            )

        handler = {
            "damaged_goods": self._rule_damaged_goods,
            "short_shipment": self._rule_short_shipment,
            "unauthorized_discount": self._rule_unauthorized_discount,
            "duplicate_billing": self._rule_duplicate_billing,
            "pricing_discrepancy": self._rule_pricing,
        }.get(extraction.reason_code, self._rule_unclassified)

        decision, credit, rationale, missing = handler(
            extraction, facts, claimed, checks, evidence
        )

        decisive = [c for c in checks if c.weight >= 1.0]
        passed = sum(1 for c in decisive if c.passed)
        ratio = (passed / len(decisive)) if decisive else 0.5
        if decision == "valid":
            confidence = 0.6 + 0.35 * ratio
        elif decision == "invalid":
            confidence = 0.6 + 0.35 * (1 - ratio)
        else:
            confidence = 0.4
        confidence = round(min(0.97, confidence * (0.85 + 0.15 * extraction.confidence)), 2)

        citation = clauses[0] if clauses else None
        return AuditDecision(
            decision=decision,
            confidence=confidence,
            rationale=rationale,
            recommended_credit=credit.quantize(CENT),
            evidence=evidence,
            checks=checks,
            citation=citation,
            retrieved_clauses=clauses,
            facts={
                "invoice_number": invoice["invoice_number"],
                "customer_code": facts["customer_code"],
                "customer_name": facts["customer_name"],
                "po_number": invoice.get("po_number"),
                "currency": currency,
                "total_amount": float(facts["total_amount"]),
                "open_balance": float(facts["open_balance"]),
                "deduction_amount": float(facts["deduction_amount"]),
                "damaged_units": facts["damaged_units"],
                "damaged_value": float(facts["damaged_value"]),
                "shortfall_units": facts["shortfall_units"],
                "shortfall_value": float(facts["shortfall_value"]),
                "delivery_date": str(facts["delivery_date"]) if facts["delivery_date"] else None,
                "days_since_delivery": facts["days_since_delivery"],
                "days_to_pay": facts["days_to_pay"],
                "claim_notice_days": facts["claim_notice_days"],
                "contract_number": facts["contract"].get("contract_number"),
                "ar_contact_name": invoice.get("ar_contact_name"),
                "ar_contact_email": invoice.get("ar_contact_email"),
                "duplicate_count": len(facts["duplicates"]),
            },
            queries=queries,
            missing_information=missing,
        )

    # -- per-reason rules ---------------------------------------------------

    def _notice_check(
        self, facts: dict[str, Any], checks: list[PolicyCheck], label: str
    ) -> bool:
        days = facts["days_since_delivery"]
        window = facts["claim_notice_days"]
        contract_number = facts["contract"].get("contract_number", "the agreement")
        if days is None:
            checks.append(
                PolicyCheck(
                    name="claim raised within the contractual notice window",
                    passed=False,
                    detail="no delivery date on file, so the notice window cannot be verified",
                )
            )
            return False
        passed = days <= window
        checks.append(
            PolicyCheck(
                name="claim raised within the contractual notice window",
                passed=passed,
                detail=(
                    f"{label} claim raised {days} {_day_word(days)} after delivery; "
                    f"{contract_number} allows {window}"
                ),
            )
        )
        return passed

    def _rule_damaged_goods(self, extraction, facts, claimed, checks, evidence):
        currency = facts["currency"]
        damage_recorded = (
            facts["damaged_units"] > 0
            or "damage_noted" in facts["conditions"]
            or any("damage" in note.lower() for note in facts["exception_notes"])
        )
        checks.append(
            PolicyCheck(
                name="damage recorded on the proof of delivery",
                passed=damage_recorded,
                detail=(
                    f"{facts['damaged_units']} unit(s) flagged damaged on "
                    f"{', '.join(facts['shipment_numbers']) or 'no shipment'}"
                    + (
                        f"; POD note: {facts['exception_notes'][0]}"
                        if facts["exception_notes"]
                        else "; POD carries no damage annotation"
                    )
                ),
            )
        )
        if facts["exception_notes"]:
            evidence.append(
                f"Shipment {', '.join(facts['shipment_numbers'])} via "
                f"{', '.join(facts['carriers'])} delivered {facts['delivery_date']} and signed "
                f"by {facts['pod_signed_by'] or 'unknown'} — "
                f"{facts['exception_notes'][0]}"
            )
        else:
            evidence.append(
                f"Shipment {', '.join(facts['shipment_numbers']) or 'n/a'} delivered "
                f"{facts['delivery_date']} was signed for in apparent good order with no "
                f"damage annotation and no carrier exception report."
            )

        in_window = self._notice_check(facts, checks, "damage")
        supported_value = facts["damaged_value"]
        amount_ok = claimed is not None and claimed <= supported_value + TOLERANCE
        checks.append(
            PolicyCheck(
                name="claimed amount within the value of the damaged units",
                passed=bool(amount_ok),
                detail=(
                    f"damaged units are worth {money(supported_value, currency)}; "
                    f"claim is {money(claimed, currency)}"
                ),
            )
        )
        if damage_recorded:
            evidence.append(
                f"{facts['damaged_units']} damaged unit(s) at the invoiced unit price come to "
                f"{money(supported_value, currency)}."
            )

        if damage_recorded and in_window and amount_ok:
            credit = min(claimed, supported_value)
            return (
                "valid",
                credit,
                f"The carrier's proof of delivery records the damage, the claim was raised "
                f"{facts['days_since_delivery']} {_day_word(facts['days_since_delivery'] or 0)} "
                f"after delivery (inside the {facts['claim_notice_days']}-day window), and "
                f"{money(claimed, currency)} does not exceed the "
                f"{money(supported_value, currency)} of damaged goods.",
                [],
            )
        if not damage_recorded:
            reason = (
                "our shipping log and the signed proof of delivery record no damage and no "
                "carrier exception for this delivery"
            )
        elif not in_window:
            reason = (
                f"the claim was raised {facts['days_since_delivery']} days after delivery, "
                f"outside the {facts['claim_notice_days']}-day notice window"
            )
        else:
            reason = (
                f"the deduction of {money(claimed, currency)} exceeds the "
                f"{money(supported_value, currency)} value of the units recorded as damaged"
            )
        return "invalid", Decimal("0"), f"The deduction is not allowable because {reason}.", []

    def _rule_short_shipment(self, extraction, facts, claimed, checks, evidence):
        currency = facts["currency"]
        shortage = facts["shortfall_units"] > 0
        checks.append(
            PolicyCheck(
                name="shortage established by our own shipping log",
                passed=shortage,
                detail=(
                    f"{facts['shortfall_units']} unit(s) short across "
                    f"{', '.join(facts['shipment_numbers']) or 'no shipment'}"
                ),
            )
        )
        evidence.append(
            f"Shipping log for {', '.join(facts['shipment_numbers']) or 'n/a'} shows "
            f"{facts['shortfall_units']} unit(s) invoiced but not shipped, worth "
            f"{money(facts['shortfall_value'], currency)}."
            + (f" {facts['exception_notes'][0]}" if facts["exception_notes"] else "")
        )
        in_window = self._notice_check(facts, checks, "shortage")
        amount_ok = claimed is not None and claimed <= facts["shortfall_value"] + TOLERANCE
        checks.append(
            PolicyCheck(
                name="claimed amount within the established shortfall",
                passed=bool(amount_ok),
                detail=(
                    f"shortfall is worth {money(facts['shortfall_value'], currency)}; "
                    f"claim is {money(claimed, currency)}"
                ),
            )
        )
        if shortage and amount_ok and in_window:
            return (
                "valid",
                min(claimed, facts["shortfall_value"]),
                f"Our own packing list and shipping log establish a {facts['shortfall_units']}-unit "
                f"shortage worth {money(facts['shortfall_value'], currency)}, which covers the "
                f"{money(claimed, currency)} deducted.",
                [],
            )
        if not shortage:
            reason = (
                "our packing list and shipping log show the full invoiced quantity was shipped "
                "and delivered"
            )
        elif not in_window:
            reason = (
                f"the shortage was notified {facts['days_since_delivery']} days after delivery, "
                f"outside the {facts['claim_notice_days']}-day notice window"
            )
        else:
            reason = (
                f"the {money(claimed, currency)} deducted exceeds the "
                f"{money(facts['shortfall_value'], currency)} value of the established shortage"
            )
        return "invalid", Decimal("0"), f"The deduction is not allowable because {reason}.", []

    def _rule_unauthorized_discount(self, extraction, facts, claimed, checks, evidence):
        currency = facts["currency"]
        issue_date = facts["issue_date"]
        early = [
            d
            for d in facts["discounts"]
            if str(d.get("discount_type")) == "early_payment"
            and bool(d.get("is_active"))
            and (to_date(d.get("valid_from")) or dt.date.min)
            <= (issue_date or dt.date.today())
            <= (to_date(d.get("valid_to")) or dt.date.max)
        ]
        agreement = early[0] if early else None
        checks.append(
            PolicyCheck(
                name="an early-payment discount agreement is in force",
                passed=bool(agreement),
                detail=(
                    f"{agreement['code']} at {float(agreement['rate_pct']):.2f}% if paid within "
                    f"{agreement['pay_within_days']} days, valid {agreement['valid_from']} to "
                    f"{agreement['valid_to']}"
                    if agreement
                    else "no active early-payment discount schedule covers this invoice date"
                ),
            )
        )

        days_to_pay = facts["days_to_pay"]
        window = int(agreement["pay_within_days"]) if agreement and agreement.get("pay_within_days") else None
        earned = bool(agreement) and days_to_pay is not None and window is not None and days_to_pay <= window
        checks.append(
            PolicyCheck(
                name="payment received inside the discount window",
                passed=earned,
                detail=(
                    f"payment cleared {days_to_pay} {_day_word(days_to_pay or 0)} after the "
                    f"invoice date; the discount requires {window} days"
                    if days_to_pay is not None and window is not None
                    else "payment timing could not be established"
                ),
            )
        )
        if agreement:
            evidence.append(
                f"Discount schedule {agreement['code']} allows "
                f"{float(agreement['rate_pct']):.2f}% only on cleared funds received within "
                f"{agreement['pay_within_days']} days of the invoice date."
            )
        if days_to_pay is not None:
            evidence.append(
                f"Invoice dated {issue_date}; cleared funds received {facts['payment_date']} — "
                f"{days_to_pay} {_day_word(days_to_pay)} later."
            )

        rate_ok = True
        if agreement and claimed is not None:
            entitlement = (
                facts["total_amount"] * Decimal(str(agreement["rate_pct"])) / Decimal("100")
            ).quantize(CENT)
            rate_ok = claimed <= entitlement + TOLERANCE
            checks.append(
                PolicyCheck(
                    name="deduction matches the agreed discount rate",
                    passed=rate_ok,
                    detail=(
                        f"{float(agreement['rate_pct']):.2f}% of "
                        f"{money(facts['total_amount'], currency)} is "
                        f"{money(entitlement, currency)}; deduction was "
                        f"{money(claimed, currency)}"
                    ),
                    weight=0.5,
                )
            )

        if earned and rate_ok:
            return (
                "valid",
                claimed or Decimal("0"),
                f"The discount schedule was in force and cleared funds arrived within the "
                f"{window}-day window, so the discount was earned.",
                [],
            )
        if not agreement:
            reason = "no early-payment discount schedule is in force for this invoice"
        else:
            reason = (
                f"the discount is earned by payment timing alone and cleared funds arrived "
                f"{days_to_pay} days after the invoice date, outside the {window}-day window"
            )
        return (
            "invalid",
            Decimal("0"),
            f"The discount was not earned: {reason}.",
            [],
        )

    def _rule_duplicate_billing(self, extraction, facts, claimed, checks, evidence):
        currency = facts["currency"]
        duplicates = facts["duplicates"]
        found = len(duplicates) > 0
        checks.append(
            PolicyCheck(
                name="a second invoice exists for the same purchase order",
                passed=found,
                detail=(
                    "matching invoices: "
                    + ", ".join(str(d["invoice_number"]) for d in duplicates)
                    if found
                    else f"PO {facts['invoice'].get('po_number')} was billed exactly once"
                ),
            )
        )
        evidence.append(
            f"PO {facts['invoice'].get('po_number')} is billed on "
            f"{1 + len(duplicates)} invoice(s) in the ledger"
            + (
                ": " + ", ".join(str(d["invoice_number"]) for d in duplicates)
                if found
                else f", namely {facts['invoice']['invoice_number']} alone."
            )
        )
        if found:
            dup_total = min(dec(d.get("total_amount")) for d in duplicates)
            credit = min(claimed or dup_total, dup_total)
            return (
                "valid",
                credit,
                "The same purchase order was billed more than once, so the duplicate charge "
                "is credited.",
                [],
            )
        return (
            "invalid",
            Decimal("0"),
            f"No duplicate exists: invoice {facts['invoice']['invoice_number']} is the only "
            f"billing raised against PO {facts['invoice'].get('po_number')}, and it covers "
            f"goods delivered on {facts['delivery_date']}.",
            [],
        )

    def _rule_pricing(self, extraction, facts, claimed, checks, evidence):
        currency = facts["currency"]
        price_agreements = [
            d
            for d in facts["discounts"]
            if str(d.get("discount_type")) in {"volume", "promotional"}
            and bool(d.get("is_active"))
        ]
        checks.append(
            PolicyCheck(
                name="a price or rebate agreement covers this invoice",
                passed=bool(price_agreements),
                detail=(
                    ", ".join(f"{d['code']} ({d['description']})" for d in price_agreements)
                    if price_agreements
                    else "no active price or rebate agreement on file"
                ),
            )
        )
        if not price_agreements:
            return (
                "invalid",
                Decimal("0"),
                "The invoice was raised at the list price in force on the order date and no "
                "price or rebate agreement applies, so the deduction is unsupported.",
                [],
            )
        evidence.append(
            "Active commercial terms: "
            + "; ".join(f"{d['code']} — {d['description']}" for d in price_agreements)
        )
        return (
            "needs_more_info",
            Decimal("0"),
            "A rebate agreement is in force but the claim does not identify the quote or "
            "price list it relies on, so the pricing cannot be reconciled.",
            [
                "the quote number or price list reference the expected price comes from",
                "the SKUs and unit prices you believe are incorrect",
            ],
        )

    def _rule_unclassified(self, extraction, facts, claimed, checks, evidence):
        checks.append(
            PolicyCheck(
                name="deduction reason identified",
                passed=False,
                detail=f"reason coded as {extraction.reason_code}",
            )
        )
        return (
            "needs_more_info",
            Decimal("0"),
            "The deduction reason could not be classified against a contractual remedy.",
            [
                "the reason code or a short description of why the amount was deducted",
                "any supporting documentation (photographs, carrier reports, return "
                "authorization numbers)",
            ],
        )
