"""Offline narration is the default. A hosted model is opt-in."""

import json

from credit_surveillance.errors import NarratorError
from credit_surveillance.narrator import (
    DeterministicNarrator,
    OpenAINarrator,
    build_narrator,
)


def test_default_provider_is_deterministic(monkeypatch):
    monkeypatch.delenv("CREDIT_SURVEILLANCE_LLM_PROVIDER", raising=False)
    assert isinstance(build_narrator(), DeterministicNarrator)


def test_openai_provider_requires_a_key():
    try:
        build_narrator({"CREDIT_SURVEILLANCE_LLM_PROVIDER": "openai"})
    except NarratorError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("expected NarratorError")


def test_openai_request_sends_cited_figures_and_does_not_recompute():
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "Narrative only."}}]}
            ).encode()

    def transport(request, timeout):
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode())
        return _Response()

    narrator = OpenAINarrator(
        api_key="test-key",
        model="gpt-4o-mini",
        base_url="https://example.invalid/v1",
        transport=transport,
    )
    from datetime import date

    from credit_surveillance.models import NarrativeRequest

    request = NarrativeRequest(
        account_name="Harborline Industrial",
        account_id="HB-2201",
        as_of=date(2026, 9, 22),
        action="reduce",
        rule_codes=("reduce_payment_drift",),
        headline="Apply penalty_rate=0.250000.",
        cited_figures={
            "credit_limit": "150000.00",
            "proposed_limit": "112000.00",
            "penalty_rate": "0.250000",
        },
        conditions=(),
        signals=("payment_drift",),
    )
    assert narrator.narrate(request) == "Narrative only."
    message = captured["body"]["messages"][1]["content"]
    assert "112000.00" in message
    assert "0.250000" in message
    assert captured["body"]["temperature"] == 0
    assert "calculate" in captured["body"]["messages"][0]["content"].lower()
