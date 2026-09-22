"""Read a deduction backup and assign a reason code.

The classifier looks only at the customer email and the debit-memo text. It
does not see the ERP facts and it does not call a model. Scores are keyword
weights, so the same backup always produces the same reason code.
"""

from __future__ import annotations

import re

from deduction_workbench.models import Classification

LABELS = {
    "shortage": "shortage",
    "pricing": "pricing",
    "returns": "returns",
    "coop_advertising": "co-op advertising",
    "damaged_goods": "damaged goods",
}

# Order is the tie-break when two reason codes score the same.
_RULES: list[tuple[str, list[tuple[str, int]]]] = [
    (
        "shortage",
        [
            (r"reason:\s*shortage\b", 8),
            (r"\bshortage\b", 5),
            (r"\bshort[- ]ship\w*\b", 5),
            (r"\bmissing (?:cases|cartons|units)\b", 4),
            (r"\bfewer (?:cases|cartons|units)\b", 4),
            (r"\bquantity (?:discrepancy|short)\b", 3),
            (r"\bpod\b", 2),
        ],
    ),
    (
        "pricing",
        [
            (r"reason:\s*pricing\b", 8),
            (r"\bbill-?backs?\b", 5),
            (r"\bprice difference\b", 5),
            (r"\bscan allowance\b", 5),
            (r"\boff-invoice\b", 4),
            (r"\bpromotional\b", 4),
            (r"\bpromotion\b", 3),
        ],
    ),
    (
        "returns",
        [
            (r"reason:\s*returns?\b", 8),
            (r"\brma\b", 5),
            (r"\bsent back\b", 4),
            (r"\breturned\b", 4),
            (r"\breturns?\b", 3),
        ],
    ),
    (
        "coop_advertising",
        [
            (r"reason:\s*co-?op(?: advertising)?\b", 8),
            (r"\bco-?op\b", 6),
            (r"\btear sheet\b", 4),
            (r"\bproof of performance\b", 4),
            (r"\badvertising\b", 3),
            (r"\baccrual\b", 2),
        ],
    ),
    (
        "damaged_goods",
        [
            (r"reason:\s*damaged goods\b", 8),
            (r"\bconcealed damage\b", 6),
            (r"\bdamaged goods\b", 6),
            (r"\bcrushed\b", 4),
            (r"\bdamaged\b", 3),
            (r"\bdamage\b", 2),
        ],
    ),
]


def classify(backup_email: str, debit_memo_text: str) -> Classification:
    text = f"{backup_email}\n{debit_memo_text}".lower()
    scored: list[tuple[int, int, str, list[str]]] = []
    for index, (reason_code, patterns) in enumerate(_RULES):
        score = 0
        evidence: list[str] = []
        for pattern, weight in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            score += weight
            snippet = match.group(0).strip()
            if snippet not in evidence:
                evidence.append(snippet)
        scored.append((score, -index, reason_code, evidence))
    scored.sort(reverse=True)
    top_score, _, reason_code, evidence = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if top_score <= 0:
        return Classification(
            reason_code="unclassified",
            reason_label="unclassified",
            confidence=0.0,
            evidence=[],
        )
    confidence = top_score / (top_score + second_score + 1)
    return Classification(
        reason_code=reason_code,
        reason_label=LABELS[reason_code],
        confidence=round(confidence, 4),
        evidence=evidence,
    )
