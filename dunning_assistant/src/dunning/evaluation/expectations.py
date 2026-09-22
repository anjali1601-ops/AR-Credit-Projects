"""What a good collections sequence looks like for each archetype.

These are the graded expectations for the offline eval: they encode collections
policy ("a reliable payer who slipped once gets a soft reminder, never a legal
threat") rather than the exact wording of any draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArchetypeExpectation:
    archetype: str
    allowed_stages: frozenset[str]
    allowed_tones: frozenset[str]
    required_channels: frozenset[str] = field(default_factory=frozenset)
    forbidden_channels: frozenset[str] = field(default_factory=frozenset)
    required_flags: frozenset[str] = field(default_factory=frozenset)
    forbidden_flags: frozenset[str] = field(default_factory=frozenset)
    min_steps: int = 2
    max_steps: int = 5
    max_sequence_days: int = 45
    forbidden_phrases: frozenset[str] = field(default_factory=frozenset)


EXPECTATIONS: dict[str, ArchetypeExpectation] = {
    "reliable_but_late": ArchetypeExpectation(
        archetype="reliable_but_late",
        allowed_stages=frozenset({"courtesy_reminder", "firm_follow_up"}),
        allowed_tones=frozenset({"warm", "neutral_professional"}),
        required_channels=frozenset({"email"}),
        forbidden_channels=frozenset({"certified_letter"}),
        forbidden_flags=frozenset({"legal_referral", "service_hold_warning"}),
        min_steps=2,
        max_steps=3,
        forbidden_phrases=frozenset({"collections counsel", "notice of default", "final demand"}),
    ),
    "deteriorating_avoidant": ArchetypeExpectation(
        archetype="deteriorating_avoidant",
        allowed_stages=frozenset({"firm_follow_up", "escalation_notice"}),
        allowed_tones=frozenset({"neutral_professional", "firm"}),
        required_channels=frozenset({"email", "phone"}),
        required_flags=frozenset({"cite_contract_terms"}),
        forbidden_flags=frozenset({"legal_referral"}),
        min_steps=3,
        max_steps=4,
        forbidden_phrases=frozenset({"collections counsel"}),
    ),
    "high_risk_delinquent": ArchetypeExpectation(
        archetype="high_risk_delinquent",
        allowed_stages=frozenset({"final_demand", "pre_legal_notice"}),
        allowed_tones=frozenset({"firm", "formal_strict"}),
        required_channels=frozenset({"email", "phone", "certified_letter"}),
        required_flags=frozenset(
            {"cite_contract_terms", "late_fee_warning", "legal_referral", "human_approval_required"}
        ),
        min_steps=3,
        max_steps=5,
        max_sequence_days=21,
    ),
}
