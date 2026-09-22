"""The classifier reads the backup. It does not see the ERP facts."""

import pytest

from deduction_workbench.agents.classifier import classify
from deduction_workbench.llm.base import get_narrator
from deduction_workbench.seed import SEED_CASES
from tests.expected import EXPECTED

PROSE = [
    (
        "The carrier POD shows 10 fewer cases than the invoice. This shortage deduction is for the missing cases.",
        "shortage",
    ),
    (
        "Please take the promotional bill-back at the scan allowance. This is a pricing deduction.",
        "pricing",
    ),
    (
        "We returned the goods under an RMA and are taking a return deduction for what we sent back.",
        "returns",
    ),
    (
        "Co-op advertising deduction. Tear sheet attached as proof of performance against the accrual.",
        "coop_advertising",
    ),
    (
        "The cases arrived crushed. This damaged goods claim includes photos of the concealed damage.",
        "damaged_goods",
    ),
]


@pytest.mark.parametrize("case_id", list(EXPECTED))
def test_seeded_backup_classifies_to_its_reason(case_id: str) -> None:
    case = next(item for item in SEED_CASES if item["id"] == case_id)
    result = classify(case["backup_email"], case["debit_memo_text"])
    assert result.reason_code == EXPECTED[case_id]["reason"]
    assert result.reason_label
    assert result.evidence
    assert result.confidence >= 0.5


@pytest.mark.parametrize("text,reason", PROSE)
def test_classifier_reads_prose_without_a_reason_line(text: str, reason: str) -> None:
    result = classify(text, "")
    assert result.reason_code == reason


def test_classifier_ignores_empty_backup() -> None:
    result = classify("Thanks, see you at the show.", "Lunch order attached.")
    assert result.reason_code == "unclassified"
    assert result.confidence == 0


def test_narrative_provider_is_offline_unless_configured(monkeypatch) -> None:
    monkeypatch.setenv("DEDUCTION_LLM_PROVIDER", "offline")
    assert get_narrator().name == "offline"

    monkeypatch.setenv("DEDUCTION_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_narrator().name == "offline"
