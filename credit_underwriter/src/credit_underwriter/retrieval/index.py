"""Retrieval indexes over the risk corpus.

Two interchangeable backends behind one protocol:

* :class:`ChromaCorpusIndex` -- ChromaDB, in-memory or persisted to disk, using
  the local embedding function so nothing is downloaded.
* :class:`InMemoryCorpusIndex` -- a dependency-free cosine index over the same
  embeddings, so the test suite and constrained environments do not need Chroma.

Both return identically shaped, identically ordered results for the same corpus,
which is what lets the retrieval tests assert on either.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..models import RiskDocument
from .corpus import document_metadata
from .embedding import DEFAULT_DIM, LocalEmbeddingFunction, cosine_similarity, embed

COLLECTION_NAME = "risk_corpus"


class CorpusIndex(Protocol):
    backend: str

    def add(self, documents: Sequence[RiskDocument]) -> None: ...

    def search(
        self, query: str, k: int, scopes: Sequence[str] | None = None
    ) -> list[tuple[RiskDocument, float]]: ...

    def count(self) -> int: ...


class InMemoryCorpusIndex:
    """Cosine similarity over locally computed embeddings."""

    backend = "in_memory"

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim
        self._documents: dict[str, RiskDocument] = {}
        self._vectors: dict[str, list[float]] = {}

    def add(self, documents: Sequence[RiskDocument]) -> None:
        for document in documents:
            self._documents[document.doc_id] = document
            self._vectors[document.doc_id] = embed(
                _embedding_text(document), self.dim
            )

    def search(
        self, query: str, k: int, scopes: Sequence[str] | None = None
    ) -> list[tuple[RiskDocument, float]]:
        query_vector = embed(query, self.dim)
        allowed = set(scopes) if scopes else None
        scored: list[tuple[RiskDocument, float]] = []
        for doc_id, document in self._documents.items():
            if allowed is not None and document_metadata(document)["scope"] not in allowed:
                continue
            score = cosine_similarity(query_vector, self._vectors[doc_id])
            if score <= 0:
                continue
            scored.append((document, score))
        # Ties broken on doc_id so ordering is stable across runs.
        scored.sort(key=lambda pair: (-pair[1], pair[0].doc_id))
        return scored[:k]

    def count(self) -> int:
        return len(self._documents)


class ChromaCorpusIndex:
    """ChromaDB-backed index using the local embedding function."""

    backend = "chroma"

    def __init__(self, persist_dir: Path | None = None, dim: int = DEFAULT_DIM) -> None:
        import chromadb

        self.dim = dim
        self._embedding_function = LocalEmbeddingFunction(dim)
        if persist_dir is None:
            client = chromadb.EphemeralClient()
        else:
            persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedding_function,  # type: ignore[arg-type]
            metadata={"hnsw:space": "cosine"},
        )
        self._documents: dict[str, RiskDocument] = {}

    def add(self, documents: Sequence[RiskDocument]) -> None:
        if not documents:
            return
        for document in documents:
            self._documents[document.doc_id] = document
        self._collection.upsert(
            ids=[d.doc_id for d in documents],
            documents=[_embedding_text(d) for d in documents],
            metadatas=[document_metadata(d) for d in documents],  # type: ignore[arg-type]
        )

    def search(
        self, query: str, k: int, scopes: Sequence[str] | None = None
    ) -> list[tuple[RiskDocument, float]]:
        where = {"scope": {"$in": list(scopes)}} if scopes else None
        result = self._collection.query(
            query_texts=[query],
            n_results=min(k, max(self._collection.count(), 1)),
            where=where,  # type: ignore[arg-type]
            include=["metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        hits: list[tuple[RiskDocument, float]] = []
        for doc_id, distance in zip(ids, distances, strict=False):
            document = self._documents.get(str(doc_id))
            if document is None:
                continue
            score = 1.0 - float(distance)
            if score <= 0:
                continue
            hits.append((document, score))
        hits.sort(key=lambda pair: (-pair[1], pair[0].doc_id))
        return hits[:k]

    def count(self) -> int:
        return self._collection.count()


def _embedding_text(document: RiskDocument) -> str:
    """Title and source are repeated into the embedded text because short,
    high-signal fields otherwise get swamped by the body."""
    return f"{document.title}. {document.title}. {document.doc_type}. {document.text}"


def build_index(
    documents: Sequence[RiskDocument],
    backend: str = "chroma",
    persist_dir: Path | None = None,
    dim: int = DEFAULT_DIM,
) -> CorpusIndex:
    """Build the requested index, falling back to in-memory if Chroma is absent."""
    index: CorpusIndex
    if backend == "chroma":
        try:
            index = ChromaCorpusIndex(persist_dir=persist_dir, dim=dim)
        except Exception:
            index = InMemoryCorpusIndex(dim=dim)
    elif backend == "in_memory":
        index = InMemoryCorpusIndex(dim=dim)
    else:
        raise ValueError(f"unknown retrieval backend {backend!r}")
    index.add(documents)
    return index
