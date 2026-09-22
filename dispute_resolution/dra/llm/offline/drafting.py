"""Offline drafting of the negotiation artefacts.

Same JSON contract a hosted model is asked for: ``{"subject", "body"}``.
"""

from __future__ import annotations

from typing import Any

REASON_LABELS: dict[str, str] = {
    "damaged_goods": "damaged goods",
    "short_shipment": "short shipment",
    "unauthorized_discount": "unauthorized discount deduction",
    "pricing_discrepancy": "pricing discrepancy",
    "duplicate_billing": "duplicate billing",
    "service_quality": "service quality",
    "other": "unspecified deduction",
}


def money(value: Any, currency: str = "USD") -> str:
    if value is None:
        return "n/a"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    return f"{symbol}{float(value):,.2f}"


def _bullets(items: list[str], marker: str = "  \u2022 ") -> str:
    return "\n".join(f"{marker}{item}" for item in items) if items else f"{marker}(none)"


def _signature(company: dict[str, Any]) -> str:
    return (
        f"{company.get('signer_name', 'Accounts Receivable')}\n"
        f"{company.get('signer_title', 'Deductions & Disputes')}\n"
        f"{company.get('name', 'Acme Supply Co.')}\n"
        f"{company.get('ar_email', 'ar@acme-supply.example')}"
    )


REOPEN_ASKS: dict[str, str] = {
    "damaged_goods": (
        "photographs, a carrier exception report, or a delivery receipt annotated with "
        "the damage"
    ),
    "short_shipment": (
        "your signed receiving report or a carrier weight ticket showing the quantity "
        "actually delivered"
    ),
    "unauthorized_discount": (
        "a bank confirmation showing when cleared funds left your account, or a discount "
        "schedule we have not seen"
    ),
    "duplicate_billing": (
        "the two invoice numbers and purchase orders you believe overlap, with the "
        "remittance that settled the first one"
    ),
    "pricing_discrepancy": (
        "the quote number or price list the expected price comes from"
    ),
}
DEFAULT_REOPEN_ASK = "any documentation supporting the deduction"


def _clause_block(decision: dict[str, Any]) -> str:
    ref = decision.get("clause_ref") or "the governing agreement"
    title = decision.get("clause_title") or ""
    text = (decision.get("clause_text") or "").strip()
    header = f"{ref}" + (f" — {title}" if title else "")
    if not text:
        return header
    quoted = "\n".join(f"    {line}" for line in text.splitlines() if line.strip())
    return f"{header}\n\n{quoted}"


def draft_rebuttal(context: dict[str, Any]) -> dict[str, str]:
    case = context["case"]
    customer = context["customer"]
    decision = context["decision"]
    company = context["company"]
    currency = case.get("currency", "USD")
    reason = REASON_LABELS.get(case.get("reason_code", "other"), "deduction")
    amount = money(case.get("disputed_amount"), currency)

    subject = (
        f"Re: Invoice {case['invoice_number']} — deduction of {amount} is not supported "
        f"(case {case['case_number']})"
    )

    body = f"""Dear {customer.get('contact_name', 'Accounts Payable team')},

Thank you for the detail on the {amount} deduction you applied against invoice \
{case['invoice_number']}. We have completed a full review against our shipping records, \
your payment history and {decision.get('contract_number', 'your master agreement')}.

We are not able to accept this deduction, and we would ask that the balance be remitted. \
Here is exactly what our records show:

{_bullets(decision.get('evidence', []))}

The governing term is {_clause_block(decision)}

In short: {decision.get('rationale', 'the claim is not supported by our records.')}

We value the relationship and we are not trying to be difficult about this — if you hold \
documentation we have not seen ({REOPEN_ASKS.get(case.get('reason_code', ''), DEFAULT_REOPEN_ASK)}), \
send it over and we will reopen the case immediately under reference {case['case_number']}.

Otherwise, please release {amount} against invoice {case['invoice_number']} with your next \
payment run. Your remittance can quote deduction reference {case['case_number']} so we can \
clear it cleanly.

Happy to jump on a call if that is easier.

Best regards,
{_signature(company)}

Reference: case {case['case_number']} · invoice {case['invoice_number']} · \
reason coded as {reason}
"""
    return {"subject": subject, "body": body.strip()}


def draft_credit_memo(context: dict[str, Any]) -> dict[str, str]:
    case = context["case"]
    customer = context["customer"]
    decision = context["decision"]
    company = context["company"]
    memo = context.get("credit_memo", {})
    currency = case.get("currency", "USD")
    amount = money(memo.get("amount", decision.get("recommended_credit")), currency)
    reason = REASON_LABELS.get(case.get("reason_code", "other"), "deduction")

    subject = (
        f"Credit memo {memo.get('memo_number', 'DRAFT')} — "
        f"{amount} against invoice {case['invoice_number']}"
    )

    lines = memo.get("lines") or []
    line_rows = "\n".join(
        f"  {i + 1:>2}. {line.get('description', '')}".ljust(72)
        + money(line.get("amount"), currency).rjust(14)
        for i, line in enumerate(lines)
    )

    body = f"""CREDIT MEMO (DRAFT — PENDING APPROVAL)
{'=' * 86}
Memo number      : {memo.get('memo_number', 'DRAFT')}
Issued by        : {company.get('name', 'Acme Supply Co.')}
Customer         : {customer.get('name', '')} ({customer.get('code', '')})
Applies to       : invoice {case['invoice_number']}  (PO {case.get('po_number') or 'n/a'})
Reason code      : {case.get('reason_code', 'other')} ({reason})
Dispute case     : {case['case_number']}
Contract basis   : {decision.get('clause_ref') or 'n/a'} of \
{decision.get('contract_number') or 'the governing agreement'}

LINES
{'-' * 86}
{line_rows if line_rows else '   1. Approved dispute credit'.ljust(72) + amount.rjust(14)}
{'-' * 86}
{'TOTAL CREDIT'.ljust(72)}{amount.rjust(14)}

SUPPORTING FINDINGS
{_bullets(decision.get('evidence', []), marker='  - ')}

DETERMINATION
  {decision.get('rationale', '')}

This credit memo is a draft. It is posted to the ledger only after a supervisor
approves dispute case {case['case_number']}.
"""
    return {"subject": subject, "body": body.strip()}


def draft_supervisor_email(context: dict[str, Any]) -> dict[str, str]:
    case = context["case"]
    customer = context["customer"]
    decision = context["decision"]
    links = context.get("links", {})
    memo = context.get("credit_memo", {})
    currency = case.get("currency", "USD")
    credit = money(memo.get("amount", decision.get("recommended_credit")), currency)
    claimed = money(case.get("disputed_amount"), currency)
    reason = REASON_LABELS.get(case.get("reason_code", "other"), "deduction")

    subject = (
        f"[Approve {credit}] {customer.get('name', 'Customer')} — invoice "
        f"{case['invoice_number']} {reason} claim ({case['case_number']})"
    )

    checks = decision.get("checks", [])
    check_lines = [
        f"{'PASS' if c.get('passed') else 'FAIL'}  {c.get('name')}: {c.get('detail')}"
        for c in checks
    ]

    body = f"""One approval needed. The claim checks out.

  Customer        {customer.get('name', '')} ({customer.get('code', '')})
  Invoice         {case['invoice_number']} · PO {case.get('po_number') or 'n/a'}
  Claimed         {claimed} ({reason})
  Recommended     {credit} credit memo {memo.get('memo_number', 'DRAFT')}
  Confidence      {float(decision.get('confidence', 0)):.0%}

WHY IT IS VALID
{_bullets(decision.get('evidence', []))}

POLICY CHECKS
{_bullets(check_lines, marker='  ')}

CONTRACT BASIS
{_clause_block(decision)}

APPROVE  ->  {links.get('approve_url', '(link unavailable)')}
REJECT   ->  {links.get('reject_url', '(link unavailable)')}
Full case with the SQL the auditor ran: {links.get('case_url', '')}

Approving posts credit memo {memo.get('memo_number', 'DRAFT')} and releases the customer
confirmation already drafted on the case. Rejecting voids the draft memo and closes the
case unsent — nothing reaches the customer until you click.
"""
    return {"subject": subject, "body": body.strip()}


def draft_info_request(context: dict[str, Any]) -> dict[str, str]:
    case = context["case"]
    customer = context["customer"]
    decision = context["decision"]
    company = context["company"]
    currency = case.get("currency", "USD")
    amount = money(case.get("disputed_amount"), currency)

    subject = (
        f"Re: Invoice {case['invoice_number']} — a few details needed to review your "
        f"{amount} deduction ({case['case_number']})"
    )

    missing = decision.get("missing_information") or [
        "the reason code or description behind the deduction",
        "any supporting documentation you hold",
    ]

    body = f"""Dear {customer.get('contact_name', 'Accounts Payable team')},

Thanks for flagging the {amount} deduction on invoice {case['invoice_number']}. We have \
opened case {case['case_number']} and started the review, but our records do not yet give \
us enough to make a decision.

Could you send us:

{_bullets(missing)}

What we were able to confirm so far:

{_bullets(decision.get('evidence', []))}

As soon as that lands we will turn the review around quickly — if the claim is supported \
you will get a credit memo, and if it is not we will explain precisely why against the \
contract terms.

Best regards,
{_signature(company)}

Reference: case {case['case_number']} · invoice {case['invoice_number']}
"""
    return {"subject": subject, "body": body.strip()}
