"""Offline dispute extraction: email text in, structured claim out."""

from __future__ import annotations

import re
from typing import Any

REASON_KEYWORDS: dict[str, tuple[str, ...]] = {
    "damaged_goods": (
        "damaged",
        "damage",
        "broken",
        "crushed",
        "dented",
        "leaking",
        "shattered",
        "unsellable",
        "water damage",
    ),
    "short_shipment": (
        "short shipment",
        "short-shipped",
        "short shipped",
        "missing units",
        "missing cases",
        "only received",
        "shortage",
        "quantity discrepancy",
        "never arrived",
    ),
    "unauthorized_discount": (
        "early payment discount",
        "early-payment discount",
        "prompt payment discount",
        "2/10",
        "discount we take",
        "took the discount",
        "applied our discount",
        "standard discount",
    ),
    "pricing_discrepancy": (
        "price discrepancy",
        "priced at",
        "wrong price",
        "overcharged",
        "price list",
        "quoted price",
        "billed at a higher",
    ),
    "duplicate_billing": (
        "duplicate",
        "billed twice",
        "already paid",
        "second invoice for the same",
    ),
    "service_quality": (
        "late delivery",
        "delivered late",
        "service failure",
        "missed delivery window",
        "expired product",
        "quality issue",
    ),
}

DISPUTE_SIGNALS: tuple[str, ...] = (
    "deduct",
    "deduction",
    "short pay",
    "short-pay",
    "short paid",
    "short-paid",
    "withhold",
    "withheld",
    "credit memo",
    "credit note",
    "dispute",
    "disputing",
    "chargeback",
    "claim",
    "adjustment",
    "net of",
    "less the",
    "balance of",
)

NON_DISPUTE_SIGNALS: tuple[str, ...] = (
    "paid in full",
    "remittance advice for the full",
    "statement of account",
    "out of office",
    "unsubscribe",
    "please send a copy of the invoice",
)

INVOICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(INV[-_ ]?\d{4}[-_ ]?\d{3,6})\b", re.I),
    re.compile(r"\binvoice\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z]{2,4}[-_ ]?[\d][\w-]{3,})", re.I),
)

PO_PATTERN = re.compile(r"\b(?:po|purchase order)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][\w-]{3,})", re.I)

AMOUNT_PATTERN = re.compile(
    r"(?:(?P<cur>USD|EUR|GBP)\s*)?\$?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2})"
)

AMOUNT_CONTEXT_WORDS: tuple[str, ...] = (
    "deduct",
    "deduction",
    "short",
    "withhold",
    "withheld",
    "credit",
    "dispute",
    "claim",
    "adjust",
    "less",
    "net of",
    "hold back",
    "holding back",
)


def _normalize_invoice(value: str) -> str:
    compact = re.sub(r"[\s_]+", "-", value.strip().upper())
    compact = re.sub(r"-+", "-", compact)
    if compact.startswith("INV") and not compact.startswith("INV-"):
        compact = "INV-" + compact[3:].lstrip("-")
    return compact


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;\n])\s+", text) if s.strip()]


def extract_amount(text: str) -> tuple[float | None, str | None]:
    """Pick the disputed amount, preferring numbers in deduction sentences."""
    best: tuple[float, str] | None = None
    fallback: tuple[float, str] | None = None
    for sentence in _sentences(text):
        lowered = sentence.lower()
        contextual = any(word in lowered for word in AMOUNT_CONTEXT_WORDS)
        for match in AMOUNT_PATTERN.finditer(sentence):
            raw = match.group("num")
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value <= 0:
                continue
            candidate = (value, sentence)
            if contextual:
                # Within a deduction sentence, the smallest plausible figure is
                # the deduction itself; the larger one is usually the invoice.
                if best is None or value < best[0]:
                    best = candidate
            elif fallback is None or value > fallback[0]:
                fallback = candidate
    chosen = best or fallback
    if chosen is None:
        return None, None
    return chosen[0], chosen[1]


def classify_reason(text: str) -> tuple[str, float, list[str]]:
    lowered = text.lower()
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for reason, keywords in REASON_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lowered:
                scores[reason] = scores.get(reason, 0) + 1
                hits.setdefault(reason, []).append(keyword)
    if not scores:
        return "other", 0.35, []
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_reason, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    confidence = min(0.95, 0.6 + 0.1 * top_score - 0.05 * runner_up)
    return top_reason, round(confidence, 2), hits[top_reason]


def looks_like_dispute(subject: str, body: str) -> tuple[bool, str]:
    text = f"{subject}\n{body}".lower()
    for phrase in NON_DISPUTE_SIGNALS:
        if phrase in text:
            return False, f"matched non-dispute phrase '{phrase}'"
    for phrase in DISPUTE_SIGNALS:
        if phrase in text:
            return True, f"matched dispute phrase '{phrase}'"
    return False, "no short-payment or dispute language found"


def extract_dispute(context: dict[str, Any]) -> dict[str, Any]:
    subject = str(context.get("subject", ""))
    body = str(context.get("body", ""))
    sender = str(context.get("sender", ""))
    text = f"{subject}\n{body}"

    is_dispute, dispute_reason = looks_like_dispute(subject, body)

    invoice_number = None
    for pattern in INVOICE_PATTERNS:
        match = pattern.search(text)
        if match:
            invoice_number = _normalize_invoice(match.group(1))
            break

    po_match = PO_PATTERN.search(text)
    amount, amount_sentence = extract_amount(text)
    reason_code, reason_confidence, keywords = classify_reason(text)

    reason_text = ""
    for sentence in _sentences(body):
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            reason_text = sentence
            break
    if not reason_text:
        reason_text = amount_sentence or (_sentences(body)[0] if _sentences(body) else "")

    signals_present = sum(
        1 for value in (invoice_number, amount, reason_code != "other") if value
    )
    confidence = round(min(0.97, 0.35 + 0.2 * signals_present + 0.1 * reason_confidence), 2)

    return {
        "is_dispute": bool(is_dispute and invoice_number),
        "invoice_number": invoice_number,
        "po_number": po_match.group(1).upper() if po_match else None,
        "reason_code": reason_code if is_dispute else "not_a_dispute",
        "reason_text": reason_text.strip(),
        "disputed_amount": amount,
        "currency": "USD",
        "customer_email": sender,
        "confidence": confidence if is_dispute else 0.2,
        "triage_note": dispute_reason,
        "keywords": keywords,
    }
