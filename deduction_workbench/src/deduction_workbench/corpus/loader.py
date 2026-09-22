"""Load the agreement markdown and retrieve clauses by lexical overlap.

Retrieval is deterministic: the same query and agreement set always return the
same ranking. Policy checks then apply the structured terms on the clause the
rule selects. The citation the clerk sees is text from this corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

AGREEMENTS = Path(__file__).resolve().parent / "agreements"

_META = re.compile(r"<!--\s*([a-z_]+):\s*(.*?)\s*-->", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TOKEN = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*", re.IGNORECASE)

# Kind prior so a shortage query prefers the shortage clause over a mention
# of "deduction" in an unrelated section. Ties still break on token overlap.
KIND_BOOST = {
    "shortage": {"shortage": 1},
    "pricing": {"pricing_promo": 1, "pricing_default": 1},
    "returns": {"returns": 1},
    "coop_advertising": {"coop": 1, "trade_funds": 1},
    "damaged_goods": {"damaged_goods": 1},
}


@dataclass(frozen=True)
class Clause:
    agreement_id: str
    agreement_title: str
    clause_id: str
    heading: str
    kind: str
    terms: dict[str, object]
    anchors: str
    text: str

    @property
    def search_blob(self) -> str:
        return f"{self.clause_id} {self.heading} {self.anchors} {self.text}"


@dataclass
class Corpus:
    clauses: list[Clause] = field(default_factory=list)

    def for_agreements(self, agreement_ids: list[str]) -> list[Clause]:
        allowed = set(agreement_ids)
        return [clause for clause in self.clauses if clause.agreement_id in allowed]

    def retrieve(
        self,
        query: str,
        agreement_ids: list[str],
        reason_code: str,
        k: int = 6,
    ) -> list[tuple[Clause, int]]:
        query_tokens = tokenize(query)
        boosts = KIND_BOOST.get(reason_code, {})
        ranked: list[tuple[int, str, Clause]] = []
        for clause in self.for_agreements(agreement_ids):
            overlap = len(query_tokens & tokenize(clause.search_blob))
            # The kind prior keeps the governing section in the retrieved set
            # even when the backup shares everyday words with every clause.
            score = overlap + boosts.get(clause.kind, 0) * 25
            if score <= 0:
                continue
            ranked.append((score, clause.clause_id, clause))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [(clause, score) for score, _, clause in ranked[:k]]


def tokenize(text: str) -> set[str]:
    found = _TOKEN.findall(text.lower())
    tokens: set[str] = set()
    for word in found:
        tokens.add(word)
        tokens.update(part for part in re.split(r"[./-]", word) if part)
    return tokens


def parse_terms(raw: str) -> dict[str, object]:
    terms: dict[str, object] = {}
    for part in raw.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        terms[key.strip()] = _coerce(value.strip())
    return terms


def _coerce(value: str) -> object:
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _metas(block: str) -> dict[str, str]:
    return {match.group(1).lower(): match.group(2).strip() for match in _META.finditer(block)}


def _strip(block: str) -> str:
    """Drop machine comments and join hard-wrapped lines into paragraphs."""
    text = _COMMENT.sub("", block)
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def load_corpus() -> Corpus:
    return _load_cached(str(AGREEMENTS))


@lru_cache(maxsize=1)
def _load_cached(directory: str) -> Corpus:
    clauses: list[Clause] = []
    for path in sorted(Path(directory).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        header, _, _rest = raw.partition("\n## ")
        file_meta = _metas(header)
        agreement_id = file_meta.get("agreement_id", path.stem)
        agreement_title = file_meta.get("title", agreement_id)
        sections = re.split(r"\n## ", raw)
        for section in sections[1:]:
            heading, _, body = section.partition("\n")
            meta = _metas(body)
            clauses.append(
                Clause(
                    agreement_id=agreement_id,
                    agreement_title=agreement_title,
                    clause_id=meta.get("id", f"{agreement_id} {heading.strip()}"),
                    heading=heading.strip(),
                    kind=meta.get("kind", ""),
                    terms=parse_terms(meta.get("terms", "")),
                    anchors=meta.get("anchors", ""),
                    text=_strip(body),
                )
            )
    if not clauses:
        raise RuntimeError(f"No agreement clauses found in {directory}")
    return Corpus(clauses)
