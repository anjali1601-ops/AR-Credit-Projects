"""The evidence registry: the single source of truth for every citation.

Agents never invent an evidence id. They receive fact bundles that already carry
ids registered here, so a citation in the final memo can always be walked back to
a statement line, a computed ratio, a retrieved document, or a policy rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

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
    (?P<suffix>[mkbx](?![a-z])|%|\ ?days|\ ?bps)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Period labels and calendar dates are references, not measured facts, so they
#: are removed before extraction rather than tested against evidence.
_PERIOD_TOKENS = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\bFY\s?\d{4}\b|\bQ[1-4]\s?\d{0,4}\b|\bH[12]\s?\d{0,4}\b)",
    re.IGNORECASE,
)

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_WRITTEN_DATES = re.compile(
    rf"""
    \b(?:
        \d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})(?:\s+\d{{4}})?
        | (?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Policy numbers, docket ids, and similar (``TC-88421``) are identifiers.
_ID_CODES = re.compile(r"\b[A-Z]{1,8}-\d+\b", re.IGNORECASE)

#: Labels that precede an ordinal rather than a quantity.
_ORDINAL_CONTEXT = re.compile(r"\b(grade|tier|net|band|naics|sic)\b[\s:]*$", re.IGNORECASE)


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


@dataclass(frozen=True)
class ClaimedNumber:
    """A number asserted in prose, with the tolerance its own precision implies.

    "$2.11m" is written to two decimals of a million, so anything within $5,000
    supports it; "60 days" is written to the whole day, so anything within half a
    day supports it. Deriving the tolerance from how the number was written is
    what lets the check be strict without flagging ordinary rounding.
    """

    value: float
    tolerance: float
    text: str

    def is_supported_by(self, supported: Iterable[float]) -> bool:
        for candidate in supported:
            if abs(self.value - candidate) <= self.tolerance:
                return True
            # Prose may state a percentage where the fact is stored as a fraction.
            if abs(self.value - candidate * 100) <= self.tolerance:
                return True
            if abs(self.value * 100 - candidate) <= self.tolerance * 100:
                return True
        return False


def extract_numbers(text: str) -> list[ClaimedNumber]:
    """Pull citable numeric assertions out of claim prose.

    Calendar dates, fiscal-period labels, and grade/tier ordinals are dropped:
    they are references rather than measured facts, so requiring evidence for
    "FY2025" would make the check noisy without making the memo more trustworthy.
    """
    cleaned = _ID_CODES.sub(
        " ", _WRITTEN_DATES.sub(" ", _PERIOD_TOKENS.sub(" ", text))
    )
    found: list[ClaimedNumber] = []
    for match in _NUMBER_RE.finditer(cleaned):
        raw_digits = match.group("digits")
        suffix = (match.group("suffix") or "").strip().lower()
        prefix = cleaned[max(0, match.start() - 12) : match.start()]
        if not suffix and _ORDINAL_CONTEXT.search(prefix):
            continue
        trailing = cleaned[match.end() : match.end() + 16]
        if not suffix and re.match(r"\s*months\b", trailing, re.IGNORECASE):
            # Lookback windows ("last 24 months") are not measured facts.
            continue

        value = float(raw_digits.replace(",", ""))
        # Half of the last written digit is the rounding the author accepted.
        decimals = len(raw_digits.split(".")[1]) if "." in raw_digits else 0
        tolerance = 0.5 * (10.0**-decimals)

        multiplier = _SUFFIX_MULTIPLIER.get(suffix)
        if multiplier is not None:
            value *= multiplier
            tolerance *= multiplier

        if match.group("paren") or match.group("sign") == "-":
            value = -value

        # A bare 4-digit integer in calendar range is a year, not a quantity.
        if (
            not suffix
            and decimals == 0
            and "," not in raw_digits
            and 1900 <= abs(value) <= 2100
        ):
            continue

        found.append(ClaimedNumber(value=value, tolerance=tolerance, text=match.group(0).strip()))
    return found


def numbers_match(
    claimed: float | ClaimedNumber | Sequence[ClaimedNumber],
    supported: Iterable[float],
    tolerance: float = 0.005,
) -> bool:
    """Whether every claimed figure is supported by the given evidence values.

    Accepts a bare float, a :class:`ClaimedNumber` extracted from prose, or the
    list :func:`extract_numbers` returns, so the citation tests and the critic
    can share one helper.
    """
    if isinstance(claimed, ClaimedNumber):
        return claimed.is_supported_by(supported)
    if isinstance(claimed, (int, float)):
        return ClaimedNumber(float(claimed), tolerance, str(claimed)).is_supported_by(supported)
    if not claimed:
        return False
    return all(number.is_supported_by(supported) for number in claimed)
