"""Shared dependencies the agent nodes need.

Kept out of the graph state so the state stays serialisable: the state holds
facts about one underwriting, while the context holds the provider, the index,
and the settings that the whole process runs under.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import Settings
from ..llm import LLMProvider, build_provider
from ..models import RiskDocument
from ..retrieval import CorpusIndex, build_index, corpus_hash, load_corpus


@dataclass
class GraphContext:
    settings: Settings
    provider: LLMProvider
    index: CorpusIndex
    documents: list[RiskDocument] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        settings: Settings,
        provider: LLMProvider | None = None,
        index: CorpusIndex | None = None,
        documents: list[RiskDocument] | None = None,
    ) -> GraphContext:
        docs = documents if documents is not None else load_corpus(settings.corpus_path)
        return cls(
            settings=settings,
            provider=provider or build_provider(settings),
            index=index
            or build_index(
                docs,
                backend=settings.retrieval_backend,
                persist_dir=None,
                dim=settings.retrieval_embedding_dim,
            ),
            documents=docs,
        )

    @property
    def as_of(self) -> date:
        return date.fromisoformat(self.settings.as_of_date)

    def corpus_fingerprint(self) -> str:
        return corpus_hash(self.documents)
