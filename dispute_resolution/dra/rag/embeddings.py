"""Deterministic, dependency-free embeddings.

Contract retrieval has to work with no API key and no model download, and it
has to give the same answer on every run. This is hashed bag-of-words with
bigrams and sublinear term weighting — closer to TF-IDF than to a neural
encoder, but the corpus is a handful of contract clauses whose vocabulary
overlaps the dispute language directly, which is exactly where lexical
retrieval is strong. Point ``embed_documents`` at a real encoder if you swap in
a hosted provider.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

EMBEDDING_DIM = 384

_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a an the and or of to in on for with by at from as is are was were be been being
    this that these those it its they them we you our your i he she his her not no
    if then than so such any all each other into over under more most may shall will
    """.split()
)


def tokenize(text: str) -> list[str]:
    words = [w for w in _TOKEN.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return words + bigrams


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % EMBEDDING_DIM
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


def embed(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    counts = Counter(tokenize(text))
    for token, count in counts.items():
        index, sign = _bucket(token)
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def embed_documents(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
