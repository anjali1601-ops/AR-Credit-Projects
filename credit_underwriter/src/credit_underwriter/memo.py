"""Markdown rendering of the underwriting memo.

Every claim is followed by numbered references into an evidence appendix, so a
reader can walk any sentence in the memo back to the statement line, ratio,
retrieved document, or policy rule it rests on.
"""

from __future__ import annotations

from .evidence import EvidenceRegistry
from .models import (
    CreditDecision,
    Critique,
    EvidenceKind,
    UnderwritingMemo,
    format_currency,
)

KIND_LABELS: dict[EvidenceKind, str] = {
    EvidenceKind.STATEMENT_LINE: "Statement line",
    EvidenceKind.RATIO: "Computed ratio",
    EvidenceKind.TREND: "Trend",
    EvidenceKind.RATING: "Scorecard",
    EvidenceKind.DOCUMENT: "Retrieved document",
    EvidenceKind.APPLICATION_FIELD: "Application",
    EvidenceKind.COMPUTED_SIGNAL: "Computed signal",
    EvidenceKind.POLICY_RULE: "Policy",
}


def render_memo(
    memo: UnderwritingMemo,
    decision: CreditDecision,
    registry: EvidenceRegistry,
    critique: Critique | None = None,
    run_id: str | None = None,
    provenance: dict[str, str] | None = None,
) -> str:
    """Render the memo, its evidence appendix, and its run provenance."""
    refs: dict[str, int] = {}
    lines: list[str] = []

    lines.append(f"# Credit underwriting memo — {memo.legal_name}")
    lines.append("")
    lines.append(_summary_table(memo, decision))
    lines.append("")

    for section in memo.sections:
        lines.append(f"## {section.heading}")
        lines.append("")
        if not section.claims:
            lines.append("_No material items._")
            lines.append("")
            continue
        for claim in section.claims:
            markers = "".join(f"[{_ref(refs, eid)}]" for eid in claim.evidence_ids)
            lines.append(f"- {claim.text} {markers}".rstrip())
        lines.append("")

    lines.append("## Evidence appendix")
    lines.append("")
    lines.append(
        "Every claim above is numbered to a row here. The kind column shows whether the fact "
        "came from a submitted statement, a computed figure, a retrieved document, or policy."
    )
    lines.append("")
    lines.append("| Ref | Kind | Fact | Value | Source |")
    lines.append("| --- | --- | --- | --- | --- |")
    for evidence_id, ref in sorted(refs.items(), key=lambda kv: kv[1]):
        item = registry.get(evidence_id)
        if item is None:
            lines.append(f"| {ref} | Unresolved | `{evidence_id}` | — | not in registry |")
            continue
        lines.append(
            f"| {ref} | {KIND_LABELS.get(item.kind, item.kind.value)} "
            f"| {_cell(item.label)} <br>`{item.evidence_id}` "
            f"| {_cell(item.display_value or '—')} | {_cell(item.source)} |"
        )
    lines.append("")

    lines.append("## Run provenance")
    lines.append("")
    provenance = dict(provenance or {})
    if run_id:
        provenance.setdefault("run id", run_id)
    provenance.setdefault("memo revision", str(memo.revision))
    provenance.setdefault("as of", memo.as_of_date)
    if critique is not None:
        provenance["completeness check"] = (
            "passed" if critique.passed else f"{len(critique.issues)} outstanding issue(s)"
        )
        provenance["claims checked"] = str(critique.claims_checked)
        provenance["citations checked"] = str(critique.citations_checked)
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    for key, value in provenance.items():
        lines.append(f"| {key} | {_cell(str(value))} |")
    lines.append("")

    if critique is not None and not critique.passed:
        lines.append("### Outstanding completeness issues")
        lines.append("")
        for issue in critique.issues:
            lines.append(f"- `{issue.code}` {issue.detail}")
        lines.append("")

    return "\n".join(lines)


def _summary_table(memo: UnderwritingMemo, decision: CreditDecision) -> str:
    rows = [
        ("Recommendation", f"**{memo.recommendation.label}**"),
        (
            "Approved limit",
            f"{format_currency(memo.approved_limit, memo.currency)} "
            f"(requested {format_currency(decision.requested_limit, memo.currency)})",
        ),
        (
            "Approved terms",
            f"Net {memo.approved_terms_days} days "
            f"(requested net {decision.requested_terms_days})"
            if memo.approved_terms_days
            else "No open account",
        ),
        (
            "Internal rating",
            f"Grade {memo.final_grade} — {memo.final_band_label} "
            f"(standalone grade {decision.standalone_grade}, "
            f"{decision.applied_notches:+.2f} notches)",
        ),
        ("Concentration haircut", f"{decision.limit_haircut_percent:.0f}%"),
        ("Security required", "Yes" if decision.security_required else "No"),
        ("Review frequency", f"Every {decision.review_frequency_months} months"),
    ]
    out = ["| Field | Value |", "| --- | --- |"]
    out += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(out)


def _ref(refs: dict[str, int], evidence_id: str) -> int:
    if evidence_id not in refs:
        refs[evidence_id] = len(refs) + 1
    return refs[evidence_id]


def _cell(text: str) -> str:
    """Escape pipes and collapse newlines so a cell cannot break the table."""
    return text.replace("|", "\\|").replace("\n", " ").strip()
