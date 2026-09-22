"""Loading the synthetic risk corpus and deriving retrieval scopes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..models import CreditApplication, RiskDocument

#: Placeholder used where a metadata field is absent, because Chroma metadata
#: values must be scalars rather than ``None``.
UNSCOPED = "*"


def load_corpus(path: Path) -> list[RiskDocument]:
    payload = json.loads(path.read_text())
    return [RiskDocument.model_validate(doc) for doc in payload["documents"]]


def corpus_hash(documents: list[RiskDocument]) -> str:
    """Stable digest of corpus content, recorded on each persisted run."""
    blob = json.dumps(
        [d.model_dump(mode="json") for d in sorted(documents, key=lambda d: d.doc_id)],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def document_scope(document: RiskDocument) -> str:
    """Which obligor, jurisdiction, or sector a document belongs to.

    A single scope key per document keeps the retrieval filter to one ``$in``
    clause, which both backends support identically.
    """
    if document.applicant_id:
        return f"applicant:{document.applicant_id}"
    if document.doc_type == "country_report" and document.country_code:
        return f"country:{document.country_code}"
    if document.industry_code:
        return f"industry:{document.industry_code}"
    return UNSCOPED


def application_scopes(application: CreditApplication) -> list[str]:
    return [
        f"applicant:{application.applicant_id}",
        f"country:{application.country_code}",
        f"industry:{application.industry_code}",
    ]


def document_metadata(document: RiskDocument) -> dict[str, str]:
    return {
        "scope": document_scope(document),
        "doc_type": document.doc_type,
        "source": document.source,
        "title": document.title,
        "published_date": document.published_date,
        "applicant_id": document.applicant_id or UNSCOPED,
        "country_code": document.country_code or UNSCOPED,
        "industry_code": document.industry_code or UNSCOPED,
    }


def document_from_metadata(doc_id: str, text: str, metadata: dict[str, str]) -> RiskDocument:
    def unwrap(key: str) -> str | None:
        value = metadata.get(key)
        return None if value in (None, UNSCOPED) else str(value)

    return RiskDocument(
        doc_id=doc_id,
        title=str(metadata["title"]),
        source=str(metadata["source"]),
        doc_type=str(metadata["doc_type"]),  # type: ignore[arg-type]
        published_date=str(metadata["published_date"]),
        text=text,
        applicant_id=unwrap("applicant_id"),
        country_code=unwrap("country_code"),
        industry_code=unwrap("industry_code"),
    )
