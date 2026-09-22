"""Sentiment Agent: reads the email thread and scores relationship health, not just polarity."""

from __future__ import annotations

from ..domain import AccountSnapshot, MessageSentiment, SentimentAssessment
from ..providers.llm import LLMProvider, LLMRequest
from ..providers.sentiment import SentimentProvider

RECENCY_HALF_LIFE_DAYS = 45.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _recency_weight(age_days: int) -> float:
    return 0.5 ** (max(0, age_days) / RECENCY_HALF_LIFE_DAYS)


def _responsiveness(days_since_inbound: int, unanswered: int, avg_reply_latency: float | None) -> float:
    score = 100.0 - min(60.0, float(days_since_inbound)) - 8.0 * unanswered
    if avg_reply_latency is not None and avg_reply_latency > 7:
        score -= min(15.0, (avg_reply_latency - 7) * 2)
    return round(_clamp(score, 0.0, 100.0), 1)


def _reply_latency(snapshot: AccountSnapshot) -> float | None:
    """Average days between one of our emails and the client's next reply."""
    emails = snapshot.emails.sort_values("sent_at")
    latencies, pending = [], None
    for _, row in emails.iterrows():
        if row["direction"] == "outbound":
            pending = pending or row["sent_at"]
        elif pending is not None:
            latencies.append((row["sent_at"] - pending).days)
            pending = None
    return round(sum(latencies) / len(latencies), 2) if latencies else None


def _trend(scored: list[MessageSentiment], days_since_inbound: int) -> str:
    if days_since_inbound >= 35:
        return "silent"
    if len(scored) < 3:
        return "stable"
    split = max(1, len(scored) // 2)
    earlier = sum(m.score for m in scored[:split]) / split
    recent = sum(m.score for m in scored[split:]) / (len(scored) - split)
    if recent - earlier > 0.2:
        return "improving"
    if earlier - recent > 0.2:
        return "declining"
    return "stable"


def _label(
    polarity: float,
    responsiveness: float,
    days_since_inbound: int,
    unanswered: int,
    recent_cues: set[str],
    inbound_count: int,
) -> str:
    if inbound_count == 0 or (days_since_inbound >= 45 and unanswered >= 3):
        return "unresponsive"
    if recent_cues & {"hostile", "dispute", "frustration"} or polarity <= -0.35:
        return "frustrated"
    if "hedging" in recent_cues and (days_since_inbound >= 14 or unanswered >= 2):
        return "avoidant"
    if polarity >= 0.15 and responsiveness >= 60:
        return "cooperative"
    return "neutral"


def assess_sentiment(
    snapshot: AccountSnapshot,
    provider: SentimentProvider,
    llm: LLMProvider,
) -> SentimentAssessment:
    as_of = snapshot.as_of
    emails = snapshot.emails.sort_values("sent_at")
    inbound = emails[emails["direction"] == "inbound"]

    scored: list[MessageSentiment] = []
    if not inbound.empty:
        raw = provider.score(inbound["body"].tolist())
        for (_, row), item in zip(inbound.iterrows(), raw):
            scored.append(
                MessageSentiment(
                    message_id=row["message_id"],
                    sent_at=row["sent_at"],
                    polarity=item.polarity,
                    score=item.score,
                    cues=item.cues,
                )
            )

    if scored:
        weights = [_recency_weight((as_of - m.sent_at).days) for m in scored]
        polarity = sum(w * m.score for w, m in zip(weights, scored)) / sum(weights)
        days_since_inbound = (as_of - scored[-1].sent_at).days
        unanswered = int((emails["sent_at"] > scored[-1].sent_at).sum())
    else:
        polarity = -0.5
        days_since_inbound = (as_of - emails["sent_at"].min()).days if not emails.empty else 999
        unanswered = int((emails["direction"] == "outbound").sum())

    responsiveness = _responsiveness(days_since_inbound, unanswered, _reply_latency(snapshot))
    recent_cues: set[str] = set()
    for message in scored[-3:]:
        recent_cues.update(message.cues)

    label = _label(polarity, responsiveness, days_since_inbound, unanswered, recent_cues, len(scored))
    health = round(_clamp(0.5 * responsiveness + 50 * ((polarity + 1) / 2), 0, 100), 1)
    evidence = [
        f"{m.sent_at.isoformat()} ({m.polarity}): "
        + (next(iter(snapshot.emails.loc[snapshot.emails["message_id"] == m.message_id, "body"]), "")[:160]).strip()
        for m in sorted(scored, key=lambda m: (m.score, -m.sent_at.toordinal()))[:2]
    ]

    summary = llm.complete(
        LLMRequest(
            task="sentiment_summary",
            system="You summarise the tone of a customer email thread for a collections specialist.",
            prompt=(
                f"Summarise relationship health for {snapshot.customer.name} from {len(scored)} client replies. "
                f"Weighted polarity {polarity:.2f}, responsiveness {responsiveness}, cues {sorted(recent_cues)}."
            ),
            variables={
                "message_count": len(scored),
                "relationship_label": label,
                "relationship_health": health,
                "engagement_trend": _trend(scored, days_since_inbound),
                "days_since_last_inbound": days_since_inbound,
                "unanswered": unanswered,
            },
        )
    ).text

    backend = getattr(provider, "name", "unknown")
    return SentimentAssessment(
        account_id=snapshot.customer.account_id,
        relationship_label=label,
        relationship_health=health,
        polarity_score=round(polarity, 4),
        responsiveness_score=responsiveness,
        engagement_trend=_trend(scored, days_since_inbound),
        backend=backend,
        per_message=scored,
        evidence=evidence,
        summary=summary,
    )
