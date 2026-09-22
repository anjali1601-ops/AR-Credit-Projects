"""Memo Critic: the completeness and citation gate.

The critic is deliberately not an LLM. It is a set of checks the memo either
passes or fails, because a subjective reviewer would make the revise loop
non-deterministic and unauditable:

* every required section is present and populated;
* every claim carries at least one citation;
* every cited evidence id resolves in the registry;
* every number asserted in a claim appears in the evidence that claim cites;
* the headline recommendation, limit, and terms match the reconciled decision; and
* the memo cites both a retrieved document and a computed ratio, so neither
  specialist's work is missing from the write-up.

A failure routes the memo back to the writer with the issues attached, up to the
configured revision ceiling.
"""

from __future__ import annotations

from ..evidence import EvidenceRegistry, extract_numbers
from ..models import (
    Critique,
    CritiqueIssue,
    EvidenceKind,
    UnderwritingMemo,
    format_currency,
)
from ..state import AgentMessage, UnderwritingState
from .context import GraphContext
from .memo_writer import REQUIRED_SECTIONS

AGENT = "memo_critic"


def review(state: UnderwritingState, context: GraphContext) -> dict:
    memo = state["memo"]
    decision = state["decision"]
    registry = EvidenceRegistry(state.get("evidence", []))

    critique = critique_memo(memo, decision, registry)
    revision = int(state.get("revision", 0))
    critique = critique.model_copy(update={"revision": revision})

    exhausted = revision >= context.settings.max_memo_revisions
    if critique.passed:
        detail = (
            f"Memo passed: {critique.claims_checked} claims, {critique.citations_checked} "
            f"citations, all resolvable, with {critique.document_citations} document and "
            f"{critique.ratio_citations} ratio citations."
        )
    elif exhausted:
        detail = (
            f"Memo still has {len(critique.issues)} issue(s) after {revision} revision(s); the "
            "revision ceiling is reached, so the memo is released with the issues recorded on "
            "the run."
        )
    else:
        detail = (
            f"Memo failed with {len(critique.issues)} issue(s): "
            + "; ".join(f"{i.code} ({i.detail})" for i in critique.issues[:3])
            + ("; …" if len(critique.issues) > 3 else "")
            + ". Returning it to the memo writer."
        )

    return {
        "critique": critique,
        "critique_history": [critique],
        "trace": [
            AgentMessage(
                agent=AGENT,
                action="check_memo",
                detail=detail,
                metrics={
                    "passed": critique.passed,
                    "revision": revision,
                    "issues": [i.code for i in critique.issues],
                    "claims_checked": critique.claims_checked,
                    "citations_checked": critique.citations_checked,
                    "revision_ceiling_reached": exhausted and not critique.passed,
                },
            )
        ],
    }


def critique_memo(
    memo: UnderwritingMemo,
    decision,
    registry: EvidenceRegistry,
) -> Critique:
    issues: list[CritiqueIssue] = []
    present = {section.key for section in memo.sections}

    for key in REQUIRED_SECTIONS:
        if key not in present:
            issues.append(
                CritiqueIssue(
                    code="missing_section",
                    detail=f"required section '{key}' is absent from the memo",
                    section_key=key,
                )
            )
    for section in memo.sections:
        if section.key in REQUIRED_SECTIONS and not section.claims:
            issues.append(
                CritiqueIssue(
                    code="empty_section",
                    detail=f"required section '{section.key}' contains no claims",
                    section_key=section.key,
                )
            )

    claims_checked = 0
    citations_checked = 0
    document_citations = 0
    ratio_citations = 0

    for section in memo.sections:
        for claim in section.claims:
            claims_checked += 1
            if not claim.evidence_ids:
                issues.append(
                    CritiqueIssue(
                        code="uncited_claim",
                        detail=f"claim '{claim.claim_id}' carries no citation",
                        section_key=section.key,
                        claim_id=claim.claim_id,
                    )
                )
                continue

            resolvable: list[str] = []
            for evidence_id in claim.evidence_ids:
                citations_checked += 1
                item = registry.get(evidence_id)
                if item is None:
                    issues.append(
                        CritiqueIssue(
                            code="unresolvable_evidence",
                            detail=(
                                f"claim '{claim.claim_id}' cites '{evidence_id}', which is not in "
                                "the evidence registry"
                            ),
                            section_key=section.key,
                            claim_id=claim.claim_id,
                        )
                    )
                    continue
                resolvable.append(evidence_id)
                if item.kind is EvidenceKind.DOCUMENT:
                    document_citations += 1
                elif item.kind is EvidenceKind.RATIO:
                    ratio_citations += 1

            supported = registry.numbers_for(resolvable)
            for number in extract_numbers(claim.text):
                if not number.is_supported_by(supported):
                    issues.append(
                        CritiqueIssue(
                            code="unsupported_number",
                            detail=(
                                f"claim '{claim.claim_id}' asserts '{number.text}' which does not "
                                "appear in any evidence it cites"
                            ),
                            section_key=section.key,
                            claim_id=claim.claim_id,
                        )
                    )

    issues.extend(_decision_consistency_issues(memo, decision))

    if document_citations == 0:
        issues.append(
            CritiqueIssue(
                code="insufficient_coverage",
                detail="the memo cites no retrieved document, so the risk searcher's work is unevidenced",
            )
        )
    if ratio_citations == 0:
        issues.append(
            CritiqueIssue(
                code="insufficient_coverage",
                detail="the memo cites no computed ratio, so the financial analysis is unevidenced",
            )
        )

    return Critique(
        passed=not issues,
        revision=memo.revision,
        issues=issues,
        claims_checked=claims_checked,
        citations_checked=citations_checked,
        document_citations=document_citations,
        ratio_citations=ratio_citations,
    )


def _decision_consistency_issues(memo: UnderwritingMemo, decision) -> list[CritiqueIssue]:
    """The memo header must not drift from the decision it is reporting."""
    issues: list[CritiqueIssue] = []
    if memo.recommendation is not decision.recommendation:
        issues.append(
            CritiqueIssue(
                code="decision_mismatch",
                detail=(
                    f"memo states recommendation '{memo.recommendation.value}' but the reconciled "
                    f"decision is '{decision.recommendation.value}'"
                ),
                section_key="recommendation",
            )
        )
    if abs(memo.approved_limit - decision.approved_limit) > 0.5:
        issues.append(
            CritiqueIssue(
                code="decision_mismatch",
                detail=(
                    f"memo states a limit of {format_currency(memo.approved_limit)} but the "
                    f"decision approved {format_currency(decision.approved_limit)}"
                ),
                section_key="recommendation",
            )
        )
    if memo.approved_terms_days != decision.approved_terms_days:
        issues.append(
            CritiqueIssue(
                code="decision_mismatch",
                detail=(
                    f"memo states net {memo.approved_terms_days} day terms but the decision "
                    f"approved net {decision.approved_terms_days}"
                ),
                section_key="recommendation",
            )
        )
    if memo.final_grade != decision.final_grade:
        issues.append(
            CritiqueIssue(
                code="decision_mismatch",
                detail=(
                    f"memo states grade {memo.final_grade} but the decision reached grade "
                    f"{decision.final_grade}"
                ),
                section_key="recommendation",
            )
        )
    return issues
