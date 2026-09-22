"""The evidence registry: the single source of truth for every citation.

Agents never invent an evidence id. They receive fact bundles that already carry
ids registered here, so a citation in the final memo can always be walked back to
a statement line, a computed ratio, a retrieved document, or a policy rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from .models import EvidenceItem, EvidenceKind

#: Matches the numbers the critic looks for in claim prose, including
#: "$1.25m", "4.9x", "57 days", "(2.1%)" and "1,240,000".
_NUMBER_RE = re.compile(
    r"""
    (?P<paren>\()?                 # accounting-style negative
    (?P<sign>[-+])?
    [$€£]?\s?
    (?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s?
    (?P<suffix>[mkbx%]|\ ?days|\ ?bps)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Tokens that look numeric but are never underwriting facts worth citing.
_IGNORED_NUMERIC_CONTEXT = re.compile(r"\b(FY|Q[1-4]|20\d{2}|grade|tier|net)\b", re.IGNORECASE)


class EvidenceRegistry:
    """An insertion-ordered, id-addressable store of citable facts."""

    def __init__(self, items: Iterable[EvidenceItem] = ()) -> None:
        self._items: dict[str, EvidenceItem] = {}
        for item in items:
            self.add(item)

    # -- mutation ------------------------------------------------------------
    def add(self, item: EvidenceItem) -> str:
        existing = self._items.get(item.evidence_id)
        if existing is not None and existing != item:
            # Re-registering the same id with different content would break
            # traceability, so merge numerics rather than silently overwrite.
            merged_numbers = sorted({*existing.numeric_values, *item.numeric_values})
            item = item.model_copy(update={"numeric_values": merged_numbers})
        self._items[item.evidence_id] = item
        return item.evidence_id

    def register(
        self,
        evidence_id: str,
        kind: EvidenceKind,
        label: str,
        source: str,
        display_value: str | None = None,
        detail: str | None = None,
        numeric_values: Iterable[float] = (),
    ) -> str:
        return self.add(
            EvidenceItem(
                evidence_id=evidence_id,
                kind=kind,
                label=label,
                source=source,
                display_value=display_value,
                detail=detail,
                numeric_values=[float(v) for v in numeric_values],
            )
        )

    def extend(self, other: EvidenceRegistry) -> None:
        for item in other:
            self.add(item)

    # -- access --------------------------------------------------------------
    def get(self, evidence_id: str) -> EvidenceItem | None:
        return self._items.get(evidence_id)

    def resolve(self, evidence_id: str) -> EvidenceItem:
        item = self._items.get(evidence_id)
        if item is None:
            raise KeyError(f"unknown evidence id: {evidence_id}")
        return item

    def has(self, evidence_id: str) -> bool:
        return evidence_id in self._items

    def ids(self) -> list[str]:
        return list(self._items)

    def items(self) -> list[EvidenceItem]:
        return list(self._items.values())

    def of_kind(self, kind: EvidenceKind) -> list[EvidenceItem]:
        return [i for i in self._items.values() if i.kind is kind]

    def __iter__(self) -> Iterator[EvidenceItem]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items

    # -- numeric support -----------------------------------------------------
    def numbers_for(self, evidence_ids: Iterable[str]) -> set[float]:
        numbers: set[float] = set()
        for eid in evidence_ids:
            item = self._items.get(eid)
            if item is not None:
                numbers.update(item.numeric_values)
        return numbers

    # -- serialisation -------------------------------------------------------
    def to_list(self) -> list[dict]:
        return [i.model_dump(mode="json") for i in self._items.values()]

    @classmethod
    def from_list(cls, payload: Iterable[dict]) -> EvidenceRegistry:
        return cls(EvidenceItem.model_validate(p) for p in payload)


# --------------------------------------------------------------------------------------
# Numeric extraction / matching used by the citation checker
# --------------------------------------------------------------------------------------

_SUFFIX_MULTIPLIER = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}


def extract_numbers(text: str) -> list[float]:
    """Pull citable numeric assertions out of claim prose.

    Years, fiscal-period labels and grade/tier ordinals are skipped: they are
    labels rather than measured facts, and requiring evidence for "FY2025" would
    make the check noisy without making the memo more trustworthy.
    """
    found: list[float] = []
    for match in _NUMBER_RE.finditer(text):
        raw_digits = match.group("digits")
        suffix = (match.group("suffix") or "").strip().lower()
        prefix = text[max(0, match.start() - 8) : match.start()]
        if _IGNORED_NUMERIC_CONTEXT.search(prefix) and not suffix:
            continue
        value = float(raw_digits.replace(",", ""))
        if suffix in _SUFFIX_MULTIPLIER:
            value *= _SUFFIX_MULTIPLIER[suffix]
        if match.group("paren") or match.group("sign") == "-":
            value = -value
        # A bare 4-digit integer in the 1900-2100 range is a year, not a fact.
        if not suffix and 1900 <= value <= 2100 and "," not in raw_digits and "." not in raw_digits:
            continue
        found.append(value)
    return found


def numbers_match(claimed: float, supported: Iterable[float]) -> bool:
    """Tolerant comparison that accounts for rounding in prose.

    A memo that says "4.9x" is supported by a stored 4.9019..., and "$1.25m" is
    supported by 1_250_000. Percentages stored as 13.0 also match a claim of 13.
    """
    for value in supported:
        if _close(claimed, value):
            return True
        # Prose may state a percentage that is stored as a fraction, or vice versa.
        if _close(claimed, value * 100) or _close(claimed * 100, value):
            return True
    return False


def _close(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b), 1.0)
    # 0.6% relative tolerance covers 2-significant-figure rounding such as
    # 4.90x printed for 4.9019x, without matching genuinely different numbers.
    return abs(a - b) <= max(0.006 * scale, 0.005)
