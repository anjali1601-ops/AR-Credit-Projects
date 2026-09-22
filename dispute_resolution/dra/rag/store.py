"""Vector store for contract clauses.

ChromaDB (persistent, on disk under ``var/chroma``) is the default. An
in-memory implementation with the same interface is used when
``DRA_VECTOR_STORE=memory``, in tests, and as an automatic fallback if Chroma
cannot start in the current environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from dra.rag.embeddings import EMBEDDING_DIM, cosine, embed, embed_documents
from dra.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict[str, Any]


@dataclass
class RetrievedChunk:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float

    @property
    def clause_ref(self) -> str:
        return str(self.metadata.get("clause_ref", ""))

    @property
    def clause_title(self) -> str:
        return str(self.metadata.get("clause_title", ""))


class VectorStore(Protocol):
    backend: str

    def reset(self) -> None: ...
    def add(self, chunks: list[Chunk]) -> None: ...
    def count(self) -> int: ...
    def query(
        self, text: str, k: int = 4, where: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]: ...


def _matches(metadata: dict[str, Any], where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    return all(metadata.get(key) == value for key, value in where.items())


class MemoryVectorStore:
    backend = "memory"

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, list[float]] = {}

    def reset(self) -> None:
        self._chunks.clear()
        self._vectors.clear()

    def add(self, chunks: list[Chunk]) -> None:
        for chunk, vector in zip(chunks, embed_documents([c.text for c in chunks])):
            self._chunks[chunk.id] = chunk
            self._vectors[chunk.id] = vector

    def count(self) -> int:
        return len(self._chunks)

    def query(
        self, text: str, k: int = 4, where: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]:
        probe = embed(text)
        scored = [
            RetrievedChunk(
                id=cid,
                text=self._chunks[cid].text,
                metadata=self._chunks[cid].metadata,
                score=cosine(probe, vector),
            )
            for cid, vector in self._vectors.items()
            if _matches(self._chunks[cid].metadata, where)
        ]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]


def _build_embedding_function():  # noqa: ANN202 - chroma types are imported lazily
    from chromadb.api.types import Documents, EmbeddingFunction

    class DeterministicEmbeddingFunction(EmbeddingFunction[Documents]):
        """Chroma embedding function wrapper around the hashed encoder."""

        def __init__(self) -> None:
            pass

        def __call__(self, input: Documents):  # noqa: A002 - chroma API name
            return embed_documents(list(input))

        @staticmethod
        def name() -> str:
            return "dra_hashed_bow"

        def default_space(self) -> str:
            return "cosine"

        def get_config(self) -> dict[str, Any]:
            return {"dimension": EMBEDDING_DIM}

        @staticmethod
        def build_from_config(config: dict[str, Any]):  # noqa: ANN205
            return DeterministicEmbeddingFunction()

        def is_legacy(self) -> bool:
            return False

    return DeterministicEmbeddingFunction()


class ChromaVectorStore:
    backend = "chroma"

    def __init__(self, persist_dir: str, collection_name: str) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection_name = collection_name
        self._embedder = _build_embedding_function()
        self._collection = self._get_or_create()

    def _get_or_create(self):  # noqa: ANN202
        return self._client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=self._embedder,
            metadata={"hnsw:space": "cosine"},
        )

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._collection_name)
        except Exception:  # noqa: BLE001 - collection may not exist yet
            pass
        self._collection = self._get_or_create()

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        self._collection.upsert(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )

    def count(self) -> int:
        return int(self._collection.count())

    def query(
        self, text: str, k: int = 4, where: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]:
        if self.count() == 0:
            return []
        result = self._collection.query(
            query_texts=[text],
            n_results=min(k, self.count()),
            where=where or None,
        )
        out: list[RetrievedChunk] = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        for cid, doc, meta, dist in zip(ids, docs, metas, dists):
            out.append(
                RetrievedChunk(
                    id=cid,
                    text=doc,
                    metadata=dict(meta or {}),
                    score=1.0 - float(dist),
                )
            )
        return out


_STORE: VectorStore | None = None


def get_vector_store(force_backend: str | None = None) -> VectorStore:
    global _STORE
    if _STORE is not None and force_backend is None:
        return _STORE
    settings = get_settings()
    backend = (force_backend or settings.vector_store).lower()
    store: VectorStore
    if backend == "memory":
        store = MemoryVectorStore()
    else:
        try:
            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            store = ChromaVectorStore(
                persist_dir=str(settings.chroma_dir),
                collection_name=settings.vector_collection,
            )
        except Exception as exc:  # noqa: BLE001 - degrade instead of failing the demo
            logger.warning("ChromaDB unavailable (%s); falling back to in-memory store", exc)
            store = MemoryVectorStore()
    _STORE = store
    return store


def reset_store_cache() -> None:
    global _STORE
    _STORE = None
