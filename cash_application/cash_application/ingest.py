"""Ingestion agent.

Reads a lockbox stub, an EDI 820, or a short-pay email and extracts the
payer, the amount, references, and the invoice numbers the customer
claimed. Parsing is deterministic. The model never sees this step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from cash_application.money import money

INVOICE_RE = re.compile(r"\bINV-\d{5}\b", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"\b[A-Z]{2}-\d{5}\b")
PO_RE = re.compile(r"\b(?:[A-Z]{2,3}-PO-\d+|RMU-\d{4}-\d+|VM-\d{4,}|LA-\d{4,}|PG-\d{4,})\b")


class IngestError(ValueError):
    """The document does not carry a usable payment amount."""


@dataclass(frozen=True)
class Extraction:
    channel: str
    payer_name: str | None
    payer_account: str | None
    amount: Decimal
    payment_reference: str | None
    invoice_numbers: tuple[str, ...]
    references: tuple[str, ...]
    line_amounts: dict[str, Decimal] = field(default_factory=dict)
    deduction_note: str | None = None
    warnings: tuple[str, ...] = ()


def parse_remittance(channel: str, text: str) -> Extraction:
    if channel == "lockbox":
        return _parse_lockbox(text)
    if channel == "edi_820":
        return _parse_edi_820(text)
    if channel == "email":
        return _parse_email(text)
    raise IngestError(f"unknown remittance channel: {channel}")


def detect_channel(text: str) -> str:
    sample = text.lstrip()
    if sample.startswith("ISA*") or "ST*820*" in text:
        return "edi_820"
    if "LOCKBOX" in text.upper():
        return "lockbox"
    return "email"


def _label(text: str, name: str) -> str | None:
    """Return the labeled value, or None when the label is absent.

    A present but blank label returns an empty string so callers can tell
    "Invoices:" (explicitly none) from a document that omits the line.
    """
    # [ \t] rather than \s, so a blank "Account:" does not swallow the next line.
    match = re.search(rf"(?im)^{re.escape(name)}:[ \t]*(.*)[ \t]*$", text)
    if not match:
        return None
    return match.group(1).strip()


def _invoices_from_field(value: str | None, fallback_text: str | None = None) -> tuple[str, ...]:
    if value is None:
        source = fallback_text or ""
    else:
        source = value
    seen: list[str] = []
    for raw in INVOICE_RE.findall(source):
        number = raw.upper()
        if number not in seen:
            seen.append(number)
    return tuple(seen)


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in seen:
            seen.append(item)
    return tuple(seen)


def _parse_lockbox(text: str) -> Extraction:
    amount_raw = _label(text, "Check amount")
    if not amount_raw:
        raise IngestError("lockbox stub has no check amount")
    amount = money(amount_raw)
    account = _label(text, "Account") or None
    if account == "":
        account = None
    check_number = _label(text, "Check number") or None
    po = _label(text, "PO") or None
    memo = _label(text, "Memo") or ""
    invoices = _invoices_from_field(_label(text, "Invoices"))
    references = list(_unique([account or "", check_number or "", po or ""]))
    for found in PO_RE.findall(memo):
        if found not in references:
            references.append(found)
    for found in _invoices_from_field(memo):
        if found not in references:
            references.append(found)
    return Extraction(
        channel="lockbox",
        payer_name=_label(text, "Payer") or None,
        payer_account=account,
        amount=amount,
        payment_reference=check_number,
        invoice_numbers=invoices,
        references=tuple(references),
        deduction_note=memo or None,
    )


def _segments(text: str) -> list[list[str]]:
    blob = text.replace("\r", "")
    if "~" in blob:
        blob = blob.replace("\n", "")
        parts = blob.split("~")
    else:
        parts = blob.splitlines()
    segments: list[list[str]] = []
    for part in parts:
        part = part.strip()
        if not part or "*" not in part:
            continue
        segments.append(part.split("*"))
    return segments


def _first_money(elements: list[str], start: int) -> Decimal | None:
    for element in elements[start:]:
        if re.fullmatch(r"\d[\d,]*\.\d{2}", element):
            return money(element)
    return None


def _parse_edi_820(text: str) -> Extraction:
    payer_name = None
    account = None
    trace = None
    header_amount: Decimal | None = None
    invoices: list[str] = []
    line_amounts: dict[str, Decimal] = {}
    references: list[str] = []
    for elements in _segments(text):
        tag = elements[0]
        if tag == "BPR" and len(elements) > 2:
            header_amount = money(elements[2])
        elif tag == "TRN" and len(elements) > 2:
            trace = elements[2] or None
            if trace:
                references.append(trace)
        elif tag == "N1" and len(elements) > 2 and elements[1] == "PR":
            payer_name = elements[2] or None
        elif tag == "REF" and len(elements) > 2:
            qualifier = elements[1]
            value = elements[2].strip()
            if not value:
                continue
            if qualifier == "PO":
                references.append(value)
            elif ACCOUNT_RE.fullmatch(value):
                account = value
                references.append(value)
            else:
                references.append(value)
        elif tag == "RMR" and len(elements) > 2:
            invoice = elements[2].strip().upper()
            if invoice and invoice not in invoices:
                invoices.append(invoice)
            line_amount = _first_money(elements, 3)
            if invoice and line_amount is not None:
                line_amounts[invoice] = line_amount
    if header_amount is None:
        if not line_amounts:
            raise IngestError("EDI 820 has no BPR amount")
        header_amount = money(sum(line_amounts.values(), Decimal("0.00")))
    warnings: list[str] = []
    if line_amounts:
        line_sum = money(sum(line_amounts.values(), Decimal("0.00")))
        if line_sum != header_amount:
            warnings.append(
                f"RMR lines sum to {line_sum} and the BPR header is {header_amount}"
            )
    return Extraction(
        channel="edi_820",
        payer_name=payer_name,
        payer_account=account,
        amount=header_amount,
        payment_reference=trace,
        invoice_numbers=tuple(invoices),
        references=_unique(references),
        line_amounts=line_amounts,
        warnings=tuple(warnings),
    )


def _parse_email(text: str) -> Extraction:
    remitting = _label(text, "Remitting")
    if remitting:
        amount = money(remitting)
    else:
        match = re.search(r"remitting\s+\$?\s*([0-9,]+\.\d{2})", text, re.IGNORECASE)
        if not match:
            raise IngestError("email remittance has no remitting amount")
        amount = money(match.group(1))
    payer = _label(text, "Payer")
    if payer == "":
        payer = None
    if payer is None:
        match = re.search(r"(?m)^From:\s*([^<\n]+)", text)
        payer = match.group(1).strip() if match else None
    account = _label(text, "Account") or None
    if account == "":
        account = None
    if account is None:
        match = ACCOUNT_RE.search(text)
        account = match.group(0) if match else None
    invoices = _invoices_from_field(_label(text, "Invoices"), text if _label(text, "Invoices") is None else None)
    deduction = _label(text, "Deduction") or None
    references: list[str] = []
    if account:
        references.append(account)
    po = _label(text, "PO")
    if po:
        references.append(po)
    for found in PO_RE.findall(text):
        if found not in references:
            references.append(found)
    return Extraction(
        channel="email",
        payer_name=payer,
        payer_account=account,
        amount=amount,
        payment_reference=None,
        invoice_numbers=invoices,
        references=_unique(references),
        deduction_note=deduction,
    )
