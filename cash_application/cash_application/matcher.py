"""Matcher.

Ranks open invoices from amount, invoice number, customer, and reference.
The score and the proposed application are pure functions of those facts.
An explainer may describe the result later. It cannot change it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from cash_application.ingest import Extraction
from cash_application.money import ZERO, money, money_eq

# A cited invoice is selected even below this. An uncited invoice is not,
# unless it is the customer's only open item for this exact amount.
SELECT_THRESHOLD = 40

_LEGAL = {
    "INC",
    "LLC",
    "CO",
    "CORP",
    "CORPORATION",
    "COMPANY",
    "THE",
    "AND",
    "OF",
    "LP",
    "LLP",
    "SYSTEMS",
    "GROUP",
}

REASON_LABELS = {
    "invoice_number": "Invoice number cited",
    "customer": "Customer matches",
    "customer_mismatch": "Payer is a different customer",
    "reference": "PO or reference matches",
    "amount_exact": "Open amount equals the payment",
    "amount_short": "Open amount is higher than the payment",
    "amount_over": "Open amount is lower than the payment",
    "line_amount": "Remittance line amount fits the open balance",
    "nearest_amount": "Nearest open amount, not a match",
    "closed": "Invoice is already paid",
}


@dataclass(frozen=True)
class CustomerView:
    account_number: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvoiceView:
    invoice_number: str
    customer_account: str
    customer_name: str
    po_number: str | None
    open_amount: Decimal
    status: str


@dataclass(frozen=True)
class MatchLine:
    invoice_number: str
    customer_account: str
    customer_name: str
    open_amount: Decimal
    apply_amount: Decimal
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateScore:
    invoice_number: str
    customer_account: str
    customer_name: str
    open_amount: Decimal
    score: int
    reasons: tuple[str, ...]
    selected: bool
    proposed_amount: Decimal | None


@dataclass(frozen=True)
class MatchResult:
    kind: str
    confidence: Decimal
    customer_account: str | None
    customer_name: str | None
    resolved_by: str
    lines: tuple[MatchLine, ...]
    candidates: tuple[CandidateScore, ...]
    payment_amount: Decimal
    applied_amount: Decimal
    unapplied_cash: Decimal
    short_fall: Decimal
    invoices_cited: bool
    customer_mismatch: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def normalize_inv(value: str) -> str:
    return value.strip().upper()


def _squash(value: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()
    return re.sub(r"\s+", " ", cleaned)


def _tokens(value: str) -> set[str]:
    return {token for token in _squash(value).split() if token not in _LEGAL and len(token) > 1}


def _account_keys(extraction: Extraction) -> set[str]:
    keys: set[str] = set()
    if extraction.payer_account:
        keys.add(extraction.payer_account.strip().upper())
    for reference in extraction.references:
        token = reference.strip().upper()
        if re.fullmatch(r"[A-Z]{2}-\d{5}", token):
            keys.add(token)
    return keys


def resolve_customer(
    extraction: Extraction, customers: list[CustomerView] | tuple[CustomerView, ...]
) -> tuple[CustomerView | None, str]:
    """Account number wins, then an exact name, then a token overlap."""
    accounts = _account_keys(extraction)
    for customer in customers:
        if customer.account_number.strip().upper() in accounts:
            return customer, "account_number"
    payer = _squash(extraction.payer_name or "")
    if payer:
        for customer in customers:
            names = (customer.name, *customer.aliases)
            if any(_squash(name) == payer for name in names if name):
                return customer, "name"
        payer_tokens = _tokens(extraction.payer_name or "")
        best: CustomerView | None = None
        best_overlap = 0.0
        for customer in customers:
            pool: set[str] = set()
            for name in (customer.name, *customer.aliases):
                pool |= _tokens(name)
            if not payer_tokens or not pool:
                continue
            overlap = len(payer_tokens & pool) / len(payer_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best = customer
        if best is not None and best_overlap >= 0.6:
            return best, "name_tokens"
    return None, "unresolved"


def score_invoice(
    invoice: InvoiceView,
    extraction: Extraction,
    customer: CustomerView | None,
) -> tuple[int, tuple[str, ...]]:
    if invoice.status == "paid" or invoice.open_amount <= 0:
        return 0, ("closed",)
    reasons: list[str] = []
    points = 0
    cited = {normalize_inv(number) for number in extraction.invoice_numbers}
    number = normalize_inv(invoice.invoice_number)
    if number in cited:
        points += 50
        reasons.append("invoice_number")
    same_customer = customer is not None and customer.account_number == invoice.customer_account
    other_customer = customer is not None and customer.account_number != invoice.customer_account
    if same_customer:
        points += 25
        reasons.append("customer")
    elif other_customer and "invoice_number" in reasons:
        points -= 20
        reasons.append("customer_mismatch")
    refs = {_squash(ref) for ref in extraction.references if ref}
    if invoice.po_number and _squash(invoice.po_number) in refs:
        points += 15
        reasons.append("reference")
    elif number in {normalize_inv(ref) for ref in extraction.references}:
        points += 15
        reasons.append("reference")
    linked = "invoice_number" in reasons or "customer" in reasons or "reference" in reasons
    if money_eq(invoice.open_amount, extraction.amount):
        points += 20
        reasons.append("amount_exact")
    elif linked and invoice.open_amount > extraction.amount:
        points += 6
        reasons.append("amount_short")
    elif linked and invoice.open_amount < extraction.amount:
        points += 6
        reasons.append("amount_over")
    line_amount = extraction.line_amounts.get(number)
    if line_amount is not None and line_amount <= invoice.open_amount:
        points += 10
        reasons.append("line_amount")
    return max(points, 0), tuple(reasons)


def _confidence(kind: str, line_count: int, invoices_cited: bool, has_reference: bool, mismatch: bool) -> Decimal:
    if mismatch:
        return Decimal("0.55")
    if kind == "unapplied" or line_count == 0:
        return Decimal("0.12")
    if kind == "full" and invoices_cited:
        return Decimal("0.96") if line_count > 1 else Decimal("0.98")
    if kind == "full":
        return Decimal("0.86") if has_reference else Decimal("0.78")
    if kind == "short_pay" and invoices_cited:
        return Decimal("0.90") if line_count == 1 else Decimal("0.84")
    if kind == "overpay" and invoices_cited:
        return Decimal("0.88")
    return Decimal("0.60")


def _allocate(
    payment: Decimal,
    selected: list[tuple[InvoiceView, int, tuple[str, ...]]],
    extraction: Extraction,
) -> tuple[str, tuple[MatchLine, ...], Decimal, Decimal, Decimal]:
    if not selected:
        return "unapplied", (), ZERO, payment, ZERO
    opens = money(sum((invoice.open_amount for invoice, _, _ in selected), ZERO))
    use_lines = all(normalize_inv(invoice.invoice_number) in extraction.line_amounts for invoice, _, _ in selected)
    drafted: list[tuple[InvoiceView, int, tuple[str, ...], Decimal]] = []
    if payment == opens:
        kind = "full"
        for invoice, score, reasons in selected:
            drafted.append((invoice, score, reasons, invoice.open_amount))
    elif payment < opens:
        kind = "short_pay"
        remaining = payment
        for invoice, score, reasons in selected:
            if remaining <= 0:
                break
            if use_lines:
                take = min(extraction.line_amounts[normalize_inv(invoice.invoice_number)], invoice.open_amount, remaining)
            else:
                take = min(invoice.open_amount, remaining)
            take = money(take)
            if take <= 0:
                continue
            drafted.append((invoice, score, reasons, take))
            remaining = money(remaining - take)
    else:
        kind = "overpay"
        for invoice, score, reasons in selected:
            drafted.append((invoice, score, reasons, invoice.open_amount))
    lines = tuple(
        MatchLine(
            invoice_number=invoice.invoice_number,
            customer_account=invoice.customer_account,
            customer_name=invoice.customer_name,
            open_amount=invoice.open_amount,
            apply_amount=apply_amount,
            score=score,
            reasons=reasons,
        )
        for invoice, score, reasons, apply_amount in drafted
    )
    applied = money(sum((line.apply_amount for line in lines), ZERO))
    unapplied_cash = money(payment - applied)
    short_fall = money(sum((line.open_amount - line.apply_amount for line in lines), ZERO))
    if not lines:
        return "unapplied", (), ZERO, payment, ZERO
    return kind, lines, applied, unapplied_cash, short_fall


def _as_candidate(
    invoice: InvoiceView,
    score: int,
    reasons: tuple[str, ...],
    selected_numbers: set[str],
    proposed: dict[str, Decimal],
) -> CandidateScore:
    number = normalize_inv(invoice.invoice_number)
    return CandidateScore(
        invoice_number=invoice.invoice_number,
        customer_account=invoice.customer_account,
        customer_name=invoice.customer_name,
        open_amount=invoice.open_amount,
        score=score,
        reasons=reasons,
        selected=number in selected_numbers,
        proposed_amount=proposed.get(number),
    )


def _candidates(
    scored: list[tuple[InvoiceView, int, tuple[str, ...]]],
    selected_numbers: set[str],
    proposed: dict[str, Decimal],
    payment: Decimal,
) -> tuple[CandidateScore, ...]:
    """Ranked clerk list. Selected invoices stay on it even if the cap is tight."""
    positive = [
        row
        for row in scored
        if row[1] > 0 and row[0].open_amount > 0 and row[0].status != "paid"
    ]
    positive.sort(key=lambda row: (-row[1], row[0].invoice_number))
    chosen = list(positive[:8])
    if not chosen:
        open_rows = [row for row in scored if row[0].open_amount > 0 and row[0].status != "paid"]
        nearest = sorted(
            open_rows,
            key=lambda row: (abs(row[0].open_amount - payment), row[0].invoice_number),
        )[:3]
        chosen = [(invoice, 1, ("nearest_amount",)) for invoice, _, _ in nearest]
    chosen_numbers = {normalize_inv(invoice.invoice_number) for invoice, _, _ in chosen}
    for invoice, score, reasons in scored:
        number = normalize_inv(invoice.invoice_number)
        if number in selected_numbers and number not in chosen_numbers:
            chosen.append((invoice, score, reasons))
    chosen.sort(key=lambda row: (-row[1], row[0].invoice_number))
    selected_rows = [row for row in chosen if normalize_inv(row[0].invoice_number) in selected_numbers]
    other_rows = [row for row in chosen if normalize_inv(row[0].invoice_number) not in selected_numbers]
    visible = selected_rows + other_rows[: max(0, 8 - len(selected_rows))]
    visible.sort(key=lambda row: (-row[1], row[0].invoice_number))
    return tuple(
        _as_candidate(invoice, score, reasons, selected_numbers, proposed) for invoice, score, reasons in visible
    )


def propose(
    extraction: Extraction,
    invoices: list[InvoiceView] | tuple[InvoiceView, ...],
    customers: list[CustomerView] | tuple[CustomerView, ...],
) -> MatchResult:
    """Propose one application for one remittance.

    Cited invoice numbers are applied as a set. Without them, the matcher
    takes the customer's single open invoice that equals the payment, and
    otherwise leaves the cash unapplied. It does not invent a multi-invoice
    combination the remittance did not name.
    """
    customer, resolved_by = resolve_customer(extraction, customers)
    scored = [(invoice, *score_invoice(invoice, extraction, customer)) for invoice in invoices]
    by_number = {normalize_inv(invoice.invoice_number): (invoice, score, reasons) for invoice, score, reasons in scored}

    cited: list[str] = []
    for raw in extraction.invoice_numbers:
        number = normalize_inv(raw)
        if number not in cited:
            cited.append(number)

    notes: list[str] = []
    selected: list[tuple[InvoiceView, int, tuple[str, ...]]] = []
    missing: list[str] = []
    paid: list[str] = []
    if cited:
        for number in cited:
            row = by_number.get(number)
            if row is None:
                missing.append(number)
                continue
            invoice, score, reasons = row
            if invoice.status == "paid" or invoice.open_amount <= 0:
                paid.append(number)
                continue
            selected.append((invoice, score, reasons))
        if missing:
            notes.append("Cited invoice not on file: " + ", ".join(missing))
        if paid:
            notes.append("Cited invoice already paid: " + ", ".join(paid))
    else:
        pool = [
            row
            for row in scored
            if customer is not None
            and row[0].customer_account == customer.account_number
            and row[0].open_amount > 0
            and row[0].status != "paid"
        ]
        exact = [row for row in pool if money_eq(row[0].open_amount, extraction.amount)]
        if len(exact) == 1:
            selected = exact
        elif len(exact) > 1:
            notes.append("Several open invoices match the payment amount; none were auto-selected.")
        else:
            linked = [row for row in pool if "reference" in row[2] and row[1] >= SELECT_THRESHOLD]
            amount_linked = [row for row in linked if money_eq(row[0].open_amount, extraction.amount)]
            if len(amount_linked) == 1:
                selected = amount_linked
            elif customer is not None:
                notes.append(
                    f"Payer resolves to {customer.name} but no open invoice ties to the amount."
                )

    mismatch = bool(
        customer is not None
        and selected
        and any(invoice.customer_account != customer.account_number for invoice, _, _ in selected)
    )
    if mismatch and customer is not None:
        notes.append(
            f"Payer resolves to {customer.name} but the cited invoice belongs to another customer."
        )

    kind, lines, applied, unapplied_cash, short_fall = _allocate(extraction.amount, selected, extraction)
    invoices_cited = bool(cited) and bool(lines) and not missing and not paid and all(
        normalize_inv(line.invoice_number) in set(cited) for line in lines
    )
    has_reference = any("reference" in line.reasons for line in lines)
    confidence = _confidence(kind, len(lines), invoices_cited, has_reference, mismatch)
    accounts = {line.customer_account for line in lines}
    if len(accounts) > 1:
        notes.append("Matched invoices span more than one customer.")
        confidence = min(confidence, Decimal("0.50"))

    if lines and len(accounts) == 1 and not mismatch:
        posting_account = lines[0].customer_account
        posting_name = lines[0].customer_name
    elif customer is not None and not lines:
        posting_account = customer.account_number
        posting_name = customer.name
    elif customer is not None and mismatch:
        posting_account = customer.account_number
        posting_name = customer.name
    else:
        posting_account = lines[0].customer_account if len(accounts) == 1 else None
        posting_name = lines[0].customer_name if len(accounts) == 1 else None

    selected_numbers = {normalize_inv(line.invoice_number) for line in lines}
    proposed_amounts = {normalize_inv(line.invoice_number): line.apply_amount for line in lines}
    candidates = _candidates(scored, selected_numbers, proposed_amounts, extraction.amount)
    return MatchResult(
        kind=kind,
        confidence=confidence,
        customer_account=posting_account,
        customer_name=posting_name,
        resolved_by=resolved_by,
        lines=lines,
        candidates=candidates,
        payment_amount=extraction.amount,
        applied_amount=applied,
        unapplied_cash=unapplied_cash,
        short_fall=short_fall,
        invoices_cited=invoices_cited,
        customer_mismatch=mismatch,
        notes=tuple(notes),
    )
