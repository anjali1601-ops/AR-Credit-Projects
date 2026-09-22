"""Auditability: the evidence registry, the citation check, and the revise loop.

The claim this project makes is that every statement in the memo is traceable to
a statement line, a computed figure, a retrieved document, or a policy rule.
These tests are what make that claim checkable, including the negative cases --
a fabricated number and a dropped section must both be caught.
"""

from __future__ import annotations

import pytest

from credit_underwriter.agents.critic import critique_memo
from credit_underwriter.agents.memo_writer import REQUIRED_SECTIONS
from credit_underwriter.evidence import EvidenceRegistry, extract_numbers, numbers_match
from credit_underwriter.memo import render_memo
from credit_underwriter.models import Claim, EvidenceKind, MemoSection, Recommendation


# --------------------------------------------------------------------------------------
# Numeric extraction
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Leverage of 4.90x", [4.90]),
        ("EBITDA margin of 5.2%", [5.2]),
        ("a limit of $350k", [350_000.0]),
        ("revenue of $62.40m", [62_400_000.0]),
        ("approved terms of 30 days", [30.0]),
        ("no figures here", []),
    ],
)
def test_numbers_are_extracted_with_their_scale(text: str, expected: list[float]):
    values = [n.value for n in extract_numbers(text)]
    for wanted in expected:
        assert any(v == pytest.approx(wanted, rel=1e-6) for v in values), (
            f"{wanted} not found in {values} for {text!r}"
        )


def test_dates_and_ordinals_are_not_treated_as_claimed_figures():
    """A memo that mentions a date has not asserted a number needing evidence."""
    assert extract_numbers("for the quarter ended 31 March 2026") == []
    assert extract_numbers("contracted to 2029-01-31") == []


def test_matching_tolerates_the_rounding_the_memo_displays():
    """4.897x printed as 4.90x must still match the underlying value."""
    claimed = extract_numbers("leverage of 4.90x")
    assert numbers_match(claimed, [4.897])
    assert not numbers_match(claimed, [4.5])


def test_matching_is_tighter_when_the_memo_is_more_precise():
    assert numbers_match(extract_numbers("5.2%"), [5.16])
    assert not numbers_match(extract_numbers("5.16%"), [5.4])


def test_a_fabricated_number_does_not_match():
    assert not numbers_match(extract_numbers("EBITDA of $9.99m"), [3_250_000.0])


# --------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------


def test_registry_deduplicates_by_evidence_id(runs):
    record = runs["atlas-precision-works"]
    ids = [item.evidence_id for item in record.evidence]
    assert len(ids) == len(set(ids))


def test_registry_covers_every_kind_of_evidence(runs):
    """A memo resting on only one kind of source would not be a credit review."""
    for applicant_id, record in runs.items():
        kinds = {item.kind for item in record.evidence}
        for required in (
            EvidenceKind.STATEMENT_LINE,
            EvidenceKind.RATIO,
            EvidenceKind.TREND,
            EvidenceKind.RATING,
            EvidenceKind.DOCUMENT,
            EvidenceKind.APPLICATION_FIELD,
            EvidenceKind.POLICY_RULE,
        ):
            assert required in kinds, f"{applicant_id} registered no {required.value}"


def test_every_evidence_item_names_a_source(runs):
    for applicant_id, record in runs.items():
        for item in record.evidence:
            assert item.source.strip(), f"{applicant_id} {item.evidence_id} has no source"
            assert item.label.strip(), f"{applicant_id} {item.evidence_id} has no label"


def test_registry_lookup_and_numbers_round_trip(runs):
    record = runs["northwind-logistics"]
    registry = record.registry
    item = next(i for i in record.evidence if i.numeric_values)
    assert registry.get(item.evidence_id) is item
    assert set(registry.numbers_for([item.evidence_id])) >= set(item.numeric_values)


def test_registry_lookup_of_an_unknown_id_returns_none(runs):
    assert runs["atlas-precision-works"].registry.get("not:a:real:id") is None


# --------------------------------------------------------------------------------------
# Every memo passes its own check
# --------------------------------------------------------------------------------------


def test_every_memo_passes_the_completeness_and_citation_check(runs):
    for applicant_id, record in runs.items():
        assert record.critique.passed, (
            f"{applicant_id} memo failed: "
            + "; ".join(f"{i.code} {i.detail}" for i in record.critique.issues)
        )


def test_every_memo_claim_carries_a_resolvable_citation(runs):
    for applicant_id, record in runs.items():
        registry = record.registry
        for claim in record.memo.claims:
            assert claim.evidence_ids, f"{applicant_id} claim {claim.claim_id} is uncited"
            for evidence_id in claim.evidence_ids:
                assert registry.get(evidence_id) is not None, (
                    f"{applicant_id} claim {claim.claim_id} cites unknown {evidence_id}"
                )


def test_every_number_in_every_memo_is_supported_by_a_cited_source(runs):
    """This is the central auditability claim, asserted directly."""
    for applicant_id, record in runs.items():
        registry = record.registry
        for claim in record.memo.claims:
            supported = registry.numbers_for(claim.evidence_ids)
            for number in extract_numbers(claim.text):
                assert number.is_supported_by(supported), (
                    f"{applicant_id} claim {claim.claim_id} asserts {number.text!r} with no "
                    f"support in {claim.evidence_ids}"
                )


def test_every_memo_contains_all_required_sections(runs):
    for applicant_id, record in runs.items():
        present = {s.key for s in record.memo.sections}
        assert set(REQUIRED_SECTIONS) <= present, applicant_id
        for section in record.memo.sections:
            if section.key in REQUIRED_SECTIONS:
                assert section.claims, f"{applicant_id} section {section.key} is empty"


def test_a_decline_memo_still_reports_on_strengths(runs):
    """A required section with nothing to say must say so, not vanish."""
    record = runs["veritas-metal-trading"]
    strengths = next(s for s in record.memo.sections if s.key == "strengths")
    assert strengths.claims


def test_every_memo_cites_both_specialists_work(runs):
    for applicant_id, record in runs.items():
        assert record.critique.document_citations > 0, f"{applicant_id} cites no document"
        assert record.critique.ratio_citations > 0, f"{applicant_id} cites no ratio"


def test_claim_ids_are_unique_within_a_memo(runs):
    for applicant_id, record in runs.items():
        ids = [c.claim_id for c in record.memo.claims]
        assert len(ids) == len(set(ids)), applicant_id


# --------------------------------------------------------------------------------------
# The check catches what it is meant to catch
# --------------------------------------------------------------------------------------


def test_the_check_catches_a_fabricated_number(runs):
    record = runs["atlas-precision-works"]
    tampered = _replace_first_claim(
        record.memo,
        Claim(
            claim_id="recommendation:fabricated",
            text="The applicant reported EBITDA of $99.90m in the period.",
            evidence_ids=["rating:standalone"],
        ),
    )
    critique = critique_memo(tampered, record.decision, record.registry)
    assert not critique.passed
    assert any(i.code == "unsupported_number" for i in critique.issues)


def test_the_check_catches_an_uncited_claim(runs):
    record = runs["atlas-precision-works"]
    tampered = _replace_first_claim(
        record.memo,
        Claim(claim_id="recommendation:uncited", text="The credit is sound.", evidence_ids=[]),
    )
    critique = critique_memo(tampered, record.decision, record.registry)
    assert not critique.passed
    assert any(i.code == "uncited_claim" for i in critique.issues)


def test_the_check_catches_a_citation_that_does_not_resolve(runs):
    record = runs["atlas-precision-works"]
    tampered = _replace_first_claim(
        record.memo,
        Claim(
            claim_id="recommendation:dangling",
            text="The credit is sound.",
            evidence_ids=["ratio:FY2099:invented"],
        ),
    )
    critique = critique_memo(tampered, record.decision, record.registry)
    assert not critique.passed
    assert any(i.code == "unresolvable_evidence" for i in critique.issues)


def test_the_check_catches_a_missing_required_section(runs):
    record = runs["northwind-logistics"]
    stripped = record.memo.model_copy(
        update={"sections": [s for s in record.memo.sections if s.key != "conditions"]}
    )
    critique = critique_memo(stripped, record.decision, record.registry)
    assert not critique.passed
    issue = next(i for i in critique.issues if i.code == "missing_section")
    assert issue.section_key == "conditions"


def test_the_check_catches_an_emptied_required_section(runs):
    record = runs["northwind-logistics"]
    emptied = record.memo.model_copy(
        update={
            "sections": [
                MemoSection(key=s.key, heading=s.heading, claims=[])
                if s.key == "risk_factors"
                else s
                for s in record.memo.sections
            ]
        }
    )
    critique = critique_memo(emptied, record.decision, record.registry)
    assert not critique.passed
    assert any(i.code == "empty_section" for i in critique.issues)


def test_the_check_catches_a_memo_that_contradicts_its_own_decision(runs):
    """A memo header that drifts from the decision is the worst failure mode."""
    record = runs["veritas-metal-trading"]
    assert record.decision.recommendation is Recommendation.DECLINE

    drifted = record.memo.model_copy(
        update={"recommendation": Recommendation.APPROVE, "approved_limit": 1_200_000.0}
    )
    critique = critique_memo(drifted, record.decision, record.registry)
    assert not critique.passed
    mismatches = [i for i in critique.issues if i.code == "decision_mismatch"]
    assert len(mismatches) >= 2


def test_the_check_notices_when_no_document_is_cited(runs):
    record = runs["atlas-precision-works"]
    stripped = record.memo.model_copy(
        update={
            "sections": [
                MemoSection(
                    key=s.key,
                    heading=s.heading,
                    claims=[
                        c.model_copy(
                            update={
                                "evidence_ids": [
                                    e
                                    for e in c.evidence_ids
                                    if (item := record.registry.get(e)) is not None
                                    and item.kind is not EvidenceKind.DOCUMENT
                                ]
                                or ["rating:standalone"]
                            }
                        )
                        for c in s.claims
                    ],
                )
                for s in record.memo.sections
            ]
        }
    )
    critique = critique_memo(stripped, record.decision, record.registry)
    assert any(
        i.code == "insufficient_coverage" and "document" in i.detail for i in critique.issues
    )


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def test_rendered_memo_carries_a_reference_for_every_claim(runs):
    for applicant_id, record in runs.items():
        markdown = record.memo_markdown
        assert "## Evidence appendix" in markdown, applicant_id
        assert "## Run provenance" in markdown, applicant_id
        for claim in record.memo.claims:
            assert claim.text in markdown, f"{applicant_id} dropped a claim in rendering"


def test_rendered_appendix_resolves_every_reference_it_prints(runs):
    for applicant_id, record in runs.items():
        assert "| Unresolved |" not in record.memo_markdown, applicant_id


def test_rendering_escapes_pipes_so_the_tables_survive(runs):
    record = runs["atlas-precision-works"]
    registry = EvidenceRegistry(record.evidence)
    registry.register(
        "test:pipe",
        EvidenceKind.POLICY_RULE,
        "A label with | a pipe",
        "A source with | a pipe",
        display_value="a | b",
    )
    memo = record.memo.model_copy(
        update={
            "sections": [
                MemoSection(
                    key="recommendation",
                    heading="Recommendation and terms",
                    claims=[
                        Claim(
                            claim_id="recommendation:pipe",
                            text="A claim citing a piped label.",
                            evidence_ids=["test:pipe"],
                        )
                    ],
                )
            ]
        }
    )
    markdown = render_memo(memo, record.decision, registry)
    appendix = markdown.split("## Evidence appendix", 1)[1].split("## Run provenance", 1)[0]
    assert "\\|" in appendix
    data_rows = [
        line
        for line in appendix.splitlines()
        if line.startswith("|") and not line.startswith("| ---") and not line.startswith("| Ref")
    ]
    assert data_rows
    for line in data_rows:
        unescaped = line.replace("\\|", "")
        assert unescaped.count("|") == 6, "an unescaped pipe would add a column"


def test_rendered_memo_states_the_recommendation_in_its_header(runs):
    for record in runs.values():
        header = record.memo_markdown.split("##", 1)[0]
        assert record.decision.recommendation.label in header


def _replace_first_claim(memo, claim: Claim):
    sections = list(memo.sections)
    first = sections[0]
    sections[0] = MemoSection(
        key=first.key, heading=first.heading, claims=[claim, *first.claims[1:]]
    )
    return memo.model_copy(update={"sections": sections})
