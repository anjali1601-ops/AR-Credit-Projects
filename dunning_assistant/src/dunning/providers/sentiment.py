"""Sentiment provider interface: HuggingFace when available, lexicon rules otherwise.

`auto` (the default) tries to load a small local HuggingFace classifier and
silently degrades to the deterministic lexicon classifier when transformers,
torch or the model weights are unavailable - so the demo always runs offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from ..config import Settings, get_settings

NEGATIVE_TERMS = {
    "frustrat": 2.0,
    "unacceptable": 2.0,
    "mess": 1.5,
    "dispute": 1.8,
    "disputed": 1.8,
    "not paying": 2.5,
    "not releasing": 2.5,
    "refuse": 2.2,
    "stop sending": 2.0,
    "unhelpful": 1.8,
    "losing patience": 2.5,
    "complaint": 1.5,
    "never delivered": 2.0,
    "cash is extremely tight": 1.8,
    "escalate": 1.0,
    "poor": 1.2,
    "wrong": 1.0,
    "no response": 1.2,
    "pressuring": 1.8,
}

POSITIVE_TERMS = {
    "thanks": 1.2,
    "thank you": 1.2,
    "appreciate": 1.4,
    "pleasure": 1.6,
    "happy to": 1.2,
    "approved": 1.5,
    "will be included": 1.5,
    "got it": 0.8,
    "no problem": 1.2,
    "apolog": 0.6,
    "sorry": 0.5,
}

CUE_PATTERNS: dict[str, list[str]] = {
    "hostile": [
        r"not (paying|releasing)",
        r"stop sending",
        r"losing patience",
        r"unacceptable",
        r"\bmess\b",
        r"unhelpful",
    ],
    "dispute": [r"\bdisputes?\b", r"never delivered", r"scope was never"],
    "frustration": [
        r"frustrat",
        r"second time",
        r"wrong amount",
        r"billing error",
        r"harder than it needs",
        r"nobody responds",
    ],
    "hedging": [
        r"check with",
        r"revert",
        r"circl\w* back",
        r"waiting on",
        r"chase it",
        r"once i know more",
        r"taking longer",
        r"hectic",
        r"still waiting",
    ],
    "commitment": [
        r"ap run",
        r"approved",
        r"will be (included|sent|paid)",
        r"payment is",
        r"next week",
        r"processed this week",
    ],
    "apology": [r"sorry", r"apolog"],
    "appreciation": [r"thanks", r"thank you", r"appreciate", r"pleasure"],
    "cash_stress": [r"cash (is|flow)", r"extremely tight", r"funding", r"tight"],
}


@dataclass
class SentimentScore:
    polarity: str  # positive | neutral | negative
    score: float  # signed confidence, -1..1
    cues: list[str] = field(default_factory=list)
    backend: str = "rules"


class SentimentProvider(Protocol):
    name: str

    def score(self, texts: list[str]) -> list[SentimentScore]: ...


def extract_cues(text: str) -> list[str]:
    lowered = text.lower()
    return [cue for cue, patterns in CUE_PATTERNS.items() if any(re.search(p, lowered) for p in patterns)]


class RuleBasedSentiment:
    """Lexicon classifier: no downloads, fully deterministic, decent on AR email copy."""

    name = "rules"

    def score(self, texts: list[str]) -> list[SentimentScore]:
        results = []
        for text in texts:
            lowered = text.lower()
            negative = sum(weight for term, weight in NEGATIVE_TERMS.items() if term in lowered)
            positive = sum(weight for term, weight in POSITIVE_TERMS.items() if term in lowered)
            raw = (positive - negative) / (positive + negative + 1.5)
            polarity = "positive" if raw > 0.18 else "negative" if raw < -0.18 else "neutral"
            results.append(SentimentScore(polarity=polarity, score=round(raw, 4), cues=extract_cues(text), backend=self.name))
        return results


class HuggingFaceSentiment:
    """Small local transformer classifier (default: distilbert SST-2)."""

    name = "huggingface"

    def __init__(self, model: str, neutral_band: float = 0.75):
        from transformers import pipeline  # imported lazily: optional extra

        self.model = model
        self.neutral_band = neutral_band
        self._pipeline = pipeline("sentiment-analysis", model=model, truncation=True)

    def score(self, texts: list[str]) -> list[SentimentScore]:
        if not texts:
            return []
        raw = self._pipeline(texts)
        results = []
        for text, item in zip(texts, raw):
            label = str(item["label"]).upper()
            confidence = float(item["score"])
            signed = confidence if label.startswith("POS") else -confidence
            if confidence < self.neutral_band:
                polarity = "neutral"
            else:
                polarity = "positive" if signed > 0 else "negative"
            results.append(
                SentimentScore(polarity=polarity, score=round(signed, 4), cues=extract_cues(text), backend=self.name)
            )
        return results


def get_sentiment_provider(settings: Settings | None = None) -> SentimentProvider:
    settings = settings or get_settings()
    choice = settings.sentiment_backend.lower()
    if choice in ("rules", "rule", "lexicon", "offline"):
        return RuleBasedSentiment()
    if choice in ("huggingface", "hf", "transformers"):
        return HuggingFaceSentiment(settings.hf_sentiment_model)
    if choice != "auto":
        raise ValueError(f"Unknown sentiment backend {settings.sentiment_backend!r}")
    try:
        return HuggingFaceSentiment(settings.hf_sentiment_model)
    except Exception:  # noqa: BLE001 - missing extra, no weights cached, or no network
        return RuleBasedSentiment()
