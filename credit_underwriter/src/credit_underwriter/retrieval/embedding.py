"""Local deterministic embeddings.

Chroma's default embedding function downloads an ONNX sentence-transformer on
first use, which would make an offline run depend on the network. This module
supplies a hashed bag-of-words embedder instead: a fixed-dimension vector built
from token hashes with sublinear term weighting and L2 normalisation.

It is a lexical embedder, so it behaves like TF-IDF retrieval rather than
semantic search. That is the right trade for this project -- the risk queries are
keyword-shaped ("winding-up petition", "covenant breach") and the retrieval stage
stays reproducible with no model download. Point ``CREDIT_UNDERWRITER_*`` at a
hosted embedding function if you want semantic recall.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

DEFAULT_DIM = 192

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']*")

_STOPWORDS = frozenset(
    """
    a an and are as at be been by for from has have in is it its of on or that the
    to was were will with this these those which their there they has had not no
    """.split()
)


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


def _bucket(token: str, dim: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """Hashed, sublinearly weighted, L2-normalised bag of words."""
    counts = Counter(tokenize(text))
    vector = [0.0] * dim
    for token, count in counts.items():
        vector[_bucket(token, dim)] += 1.0 + math.log(count)
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class LocalEmbeddingFunction:
    """Chroma-compatible embedding function wrapping :func:`embed`.

    Implements ``embed_documents``/``embed_query`` as well as ``__call__`` so it
    satisfies both the legacy and current Chroma embedding-function protocols.
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002 - Chroma's API
        return [embed(text, self.dim) for text in input]

    def embed_documents(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)

    @staticmethod
    def name() -> str:
        return "credit_underwriter_local_hashed"

    def get_config(self) -> dict[str, int]:
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config: dict[str, int]) -> LocalEmbeddingFunction:
        return LocalEmbeddingFunction(**config)
