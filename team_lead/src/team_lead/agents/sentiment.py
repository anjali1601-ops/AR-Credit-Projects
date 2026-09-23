"""Label the relationship with the work from notes the manager already has.

The agent does not infer a mood from missing messages, and it does not write
dialogue that was not in the 1:1 or the QA comment.
"""

from __future__ import annotations

from ..models import Evidence, Relationship, SentimentFinding, SourceNote

WITHDRAWING_PHRASES = (
    "withdrawing from",
    "interviewing for another role",
    "looking elsewhere",
    "checked out",
    "don't see a future",
    "do not see a future",
)

STRAINED_PHRASES = (
    "not sure he can keep this pace",
    "hours are catching",
    "was quiet in the team huddle",
    "was short",
    "pushed back",
    "follow-up dates are missed",
)

ENGAGED_PHRASES = (
    "proud of",
    "wants to mentor",
    "best queue",
    "likes seeing",
    "brings questions",
)


def _hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    return [phrase for phrase in phrases if phrase.casefold() in lowered]


def label_relationship(notes: list[SourceNote]) -> tuple[Relationship, list[Evidence]]:
    withdrawing = strained = engaged = 0
    evidence: list[Evidence] = []
    for note in notes:
        found_withdrawing = _hits(note.text, WITHDRAWING_PHRASES)
        found_strained = _hits(note.text, STRAINED_PHRASES)
        found_engaged = _hits(note.text, ENGAGED_PHRASES)
        withdrawing += len(found_withdrawing)
        strained += len(found_strained)
        engaged += len(found_engaged)
        evidence.append(
            Evidence(
                source=note.source,
                observed_on=note.observed_on.isoformat(),
                text=note.text,
                matched_phrases=found_withdrawing + found_strained + found_engaged,
            )
        )
    if withdrawing:
        label: Relationship = "withdrawing"
    elif strained and strained >= engaged:
        label = "strained"
    else:
        # No adverse phrase in the file. Do not invent strain from silence.
        label = "engaged"
    return label, evidence


def assess_sentiment(name: str, notes: list[SourceNote]) -> SentimentFinding:
    label, evidence = label_relationship(notes)
    if not notes:
        summary = (
            f"No 1:1 notes or QA comments are on file for {name}, "
            "so no adverse relationship was inferred."
        )
    else:
        summary = (
            f"{name}'s relationship with the work is {label}. "
            "Labeled only from the 1:1 notes and QA comments on file. "
            "No other messages were used."
        )
    return SentimentFinding(label=label, summary=summary, evidence=evidence)
