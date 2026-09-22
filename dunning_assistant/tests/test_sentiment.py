from __future__ import annotations

import pytest

from dunning.agents.sentiment_agent import assess_sentiment
from dunning.config import Settings
from dunning.providers.sentiment import (
    HuggingFaceSentiment,
    RuleBasedSentiment,
    extract_cues,
    get_sentiment_provider,
)
from tests.conftest import make_snapshot


@pytest.fixture(scope="module")
def provider() -> RuleBasedSentiment:
    return RuleBasedSentiment()


def test_rule_based_polarity(provider):
    positive, negative, neutral = provider.score(
        [
            "Thanks so much, always a pleasure working with your team - payment is approved.",
            "Your invoicing has been a mess and we are not paying until this is sorted. Stop sending reminders.",
            "The invoice arrived on Tuesday.",
        ]
    )
    assert positive.polarity == "positive"
    assert negative.polarity == "negative"
    assert neutral.polarity == "neutral"
    assert negative.score < positive.score


def test_cue_extraction_separates_hedging_from_hostility():
    assert "hedging" in extract_cues("Let me check with finance and revert next week.")
    assert "hostile" in extract_cues("We are not paying until this is sorted out.")
    assert "commitment" in extract_cues("It will be included in the next AP run.")
    assert "frustration" in extract_cues("This is the second time we've been billed for the wrong amount.")


def test_provider_factory_falls_back_without_transformers(monkeypatch):
    monkeypatch.setattr(
        "dunning.providers.sentiment.HuggingFaceSentiment.__init__",
        lambda self, model, neutral_band=0.75: (_ for _ in ()).throw(ImportError("no transformers")),
    )
    assert get_sentiment_provider(Settings(sentiment_backend="auto")).name == "rules"


def test_explicit_rules_backend_never_downloads():
    assert get_sentiment_provider(Settings(sentiment_backend="rules")).name == "rules"


def test_cooperative_thread_reads_as_cooperative(provider, deps):
    assessment = assess_sentiment(deps.repository.snapshot("ACC-1001"), provider, deps.llm)

    assert assessment.relationship_label == "cooperative"
    assert assessment.relationship_health > 70
    assert assessment.responsiveness_score > 70
    assert assessment.engagement_trend != "silent"


def test_hedging_then_silence_reads_as_avoidant(provider, deps):
    assessment = assess_sentiment(deps.repository.snapshot("ACC-2001"), provider, deps.llm)

    assert assessment.relationship_label == "avoidant"
    assert assessment.engagement_trend == "silent"
    assert 30 < assessment.relationship_health < 70


def test_long_silence_after_hostility_reads_as_unresponsive(provider, deps):
    assessment = assess_sentiment(deps.repository.snapshot("ACC-3001"), provider, deps.llm)

    assert assessment.relationship_label == "unresponsive"
    assert assessment.relationship_health < 30
    assert assessment.evidence


def test_recent_complaint_outweighs_older_goodwill(provider, deps):
    snapshot = make_snapshot(
        emails=[
            (120, "inbound", "Thanks so much, a pleasure as always - payment is approved."),
            (110, "inbound", "Appreciate the quick turnaround, thank you."),
            (2, "inbound", "This is the second time we've been billed for the wrong amount and it is frustrating."),
        ]
    )
    assessment = assess_sentiment(snapshot, provider, deps.llm)

    assert assessment.relationship_label == "frustrated"
    assert assessment.per_message[-1].polarity == "negative"


def test_empty_thread_is_treated_as_unresponsive(provider, deps):
    assessment = assess_sentiment(make_snapshot(emails=[]), provider, deps.llm)
    assert assessment.relationship_label == "unresponsive"


@pytest.mark.parametrize("account_id", ["ACC-1001", "ACC-2001", "ACC-3001"])
def test_huggingface_backend_agrees_on_relationship_label(deps, account_id):
    """Skips automatically when transformers/torch or the weights are unavailable."""
    try:
        hf = HuggingFaceSentiment(Settings().hf_sentiment_model)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"HuggingFace backend unavailable: {exc}")

    snapshot = deps.repository.snapshot(account_id)
    assert assess_sentiment(snapshot, hf, deps.llm).relationship_label == assess_sentiment(
        snapshot, RuleBasedSentiment(), deps.llm
    ).relationship_label
