"""Behavioral Profiler Agent: turns raw AR history into payment-behaviour metrics and a risk profile."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..domain import AccountSnapshot, Archetype, PaymentMetrics, RiskProfile
from ..providers.llm import LLMProvider, LLMRequest

RISK_BANDS = ((25, "low"), (45, "moderate"), (65, "elevated"), (101, "severe"))


def _days_between(later, earlier) -> int:
    return int((later - earlier).days)


def compute_metrics(snapshot: AccountSnapshot) -> PaymentMetrics:
    """All behavioural metrics, computed with pandas over the account's history."""
    as_of = snapshot.as_of
    invoices = snapshot.invoices
    paid = invoices[invoices["status"] == "paid"].sort_values("due_date")
    open_invoices = invoices[invoices["status"] != "paid"].copy()

    metrics = PaymentMetrics(
        invoices_on_record=int(len(invoices)),
        invoices_paid=int(len(paid)),
        open_invoices=int(len(open_invoices)),
        tenure_months=max(0, _days_between(as_of, snapshot.customer.relationship_start) // 30),
    )

    if not open_invoices.empty:
        open_invoices["outstanding"] = open_invoices["amount"] - open_invoices["amount_paid"]
        open_invoices["days_past_due"] = open_invoices["due_date"].map(lambda d: _days_between(as_of, d))
        past_due = open_invoices[open_invoices["days_past_due"] > 0]
        outstanding_total = float(open_invoices["outstanding"].sum())
        past_due_total = float(past_due["outstanding"].sum())

        metrics.open_balance = round(outstanding_total, 2)
        metrics.past_due_balance = round(past_due_total, 2)
        metrics.largest_open_invoice = round(float(open_invoices["outstanding"].max()), 2)
        metrics.oldest_days_past_due = int(max(0, open_invoices["days_past_due"].max()))
        metrics.disputed_open_invoices = int(open_invoices["disputed"].sum())
        metrics.partial_payment_ratio = round(
            float(open_invoices["amount_paid"].sum()) / max(1.0, float(open_invoices["amount"].sum())), 4
        )
        if past_due_total > 0:
            metrics.weighted_days_past_due = round(
                float((past_due["outstanding"] * past_due["days_past_due"]).sum()) / past_due_total, 2
            )
        buckets = {"aging_0_30": (0, 30), "aging_31_60": (31, 60), "aging_61_90": (61, 90), "aging_90_plus": (91, 10**6)}
        for field_name, (low, high) in buckets.items():
            mask = past_due["days_past_due"].between(low, high)
            setattr(metrics, field_name, round(float(past_due.loc[mask, "outstanding"].sum()), 2))
        if snapshot.customer.credit_limit:
            metrics.exposure_vs_credit_limit = round(outstanding_total / snapshot.customer.credit_limit, 4)

    if not paid.empty:
        days_late = paid["days_late"].astype(float)
        metrics.avg_days_late = round(float(days_late.mean()), 2)
        metrics.median_days_late = round(float(days_late.median()), 2)
        metrics.days_late_stddev = round(float(days_late.std(ddof=0)) if len(days_late) > 1 else 0.0, 2)
        metrics.worst_days_late = int(days_late.max())
        metrics.pct_invoices_paid_late = round(float((days_late > 0).mean()), 4)
        if len(days_late) >= 4:
            # Slope of days-late over invoice sequence: how fast behaviour is degrading.
            metrics.lateness_trend = round(float(np.polyfit(np.arange(len(days_late)), days_late.to_numpy(), 1)[0]), 3)

    payments = snapshot.payments
    if not payments.empty:
        metrics.days_since_last_payment = _days_between(as_of, payments["payment_date"].max())

    promises = snapshot.promises
    if not promises.empty:
        metrics.promises_made = int(len(promises))
        metrics.promises_broken = int((~promises["kept"].astype(bool)).sum())

    emails = snapshot.emails
    if not emails.empty:
        inbound = emails[emails["direction"] == "inbound"]
        if inbound.empty:
            metrics.days_since_last_inbound_email = _days_between(as_of, emails["sent_at"].min())
            metrics.unanswered_outbound_emails = int((emails["direction"] == "outbound").sum())
        else:
            last_inbound = inbound["sent_at"].max()
            metrics.days_since_last_inbound_email = _days_between(as_of, last_inbound)
            metrics.unanswered_outbound_emails = int(
                ((emails["direction"] == "outbound") & (emails["sent_at"] > last_inbound)).sum()
            )
    return metrics


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_risk(metrics: PaymentMetrics) -> tuple[float, list[str]]:
    """Weighted risk score (0-100) plus the human-readable drivers behind it."""
    signals: list[str] = []
    components = {
        "aging": _clamp(metrics.weighted_days_past_due / 120) * 30,
        "oldest": _clamp(metrics.oldest_days_past_due / 150) * 10,
        "broken_promises": _clamp(metrics.promises_broken / 3) * 15,
        "degradation": _clamp(metrics.lateness_trend / 3) * 10,
        "silence": _clamp(metrics.days_since_last_inbound_email / 60) * 12,
        "unanswered": _clamp(metrics.unanswered_outbound_emails / 5) * 8,
        "exposure": _clamp(metrics.exposure_vs_credit_limit / 1.2) * 8,
        "volatility": _clamp(metrics.days_late_stddev / 25) * 7,
    }
    score = round(sum(components.values()), 2)

    if metrics.oldest_days_past_due >= 90:
        signals.append(f"oldest invoice {metrics.oldest_days_past_due} days past due (90+ bucket)")
    elif metrics.oldest_days_past_due > 0:
        signals.append(f"oldest invoice {metrics.oldest_days_past_due} days past due")
    if metrics.promises_broken:
        signals.append(f"{metrics.promises_broken} of {metrics.promises_made} payment promises broken")
    if metrics.lateness_trend >= 1.0:
        signals.append(f"lateness worsening by {metrics.lateness_trend:.1f} days per invoice")
    elif metrics.days_late_stddev <= 6 and metrics.avg_days_late > 5:
        signals.append(f"metronomic {metrics.avg_days_late:.0f}-day lag, stddev {metrics.days_late_stddev:.1f}")
    if metrics.days_since_last_inbound_email >= 21:
        signals.append(
            f"no client reply for {metrics.days_since_last_inbound_email} days "
            f"({metrics.unanswered_outbound_emails} unanswered outreach attempts)"
        )
    if metrics.exposure_vs_credit_limit >= 0.8:
        signals.append(f"exposure at {metrics.exposure_vs_credit_limit:.0%} of credit limit")
    if metrics.disputed_open_invoices:
        signals.append(f"{metrics.disputed_open_invoices} open invoice(s) flagged as disputed")
    if metrics.partial_payment_ratio > 0.05:
        signals.append(f"only {metrics.partial_payment_ratio:.0%} of the open balance has been paid down")
    return score, signals


def classify_archetype(metrics: PaymentMetrics, risk_score: float) -> Archetype:
    """Rule-based archetype assignment; order matters (most severe first)."""
    if metrics.invoices_paid < 3 and metrics.oldest_days_past_due <= 0:
        return "unclassified"

    severe_aging = metrics.aging_90_plus > 0.2 * max(1.0, metrics.past_due_balance)
    if risk_score >= 62 or metrics.oldest_days_past_due >= 90 or metrics.promises_broken >= 2 or severe_aging:
        return "high_risk_delinquent"

    going_quiet = metrics.days_since_last_inbound_email >= 21 and metrics.unanswered_outbound_emails >= 2
    if metrics.lateness_trend >= 1.2 or going_quiet or (metrics.promises_broken >= 1 and risk_score >= 35):
        return "deteriorating_avoidant"

    stable = metrics.days_late_stddev <= 9 and metrics.lateness_trend < 1.2 and metrics.oldest_days_past_due < 60
    if stable and metrics.promises_broken == 0:
        return "reliable_but_late"

    return "deteriorating_avoidant" if risk_score >= 40 else "reliable_but_late"


def _band(score: float) -> str:
    for threshold, label in RISK_BANDS:
        if score < threshold:
            return label
    return "severe"


def _predictability(metrics: PaymentMetrics) -> str:
    if metrics.days_late_stddev <= 6:
        return "metronomic"
    return "variable" if metrics.days_late_stddev <= 18 else "erratic"


def _outlook(archetype: str, metrics: PaymentMetrics, risk_score: float) -> str:
    if risk_score >= 65 or metrics.oldest_days_past_due >= 120 or metrics.promises_broken >= 3:
        return "at_risk_of_write_off"
    if archetype == "reliable_but_late" and risk_score < 30:
        return "self_correcting"
    return "needs_pressure"


def build_profile(snapshot: AccountSnapshot, llm: LLMProvider) -> RiskProfile:
    metrics = compute_metrics(snapshot)
    risk_score, signals = score_risk(metrics)
    archetype = classify_archetype(metrics, risk_score)
    trend_word = (
        "worsening" if metrics.lateness_trend >= 1.0 else "improving" if metrics.lateness_trend <= -1.0 else "steady"
    )

    narrative = llm.complete(
        LLMRequest(
            task="risk_narrative",
            system="You are a credit analyst summarising an accounts-receivable risk profile in three sentences.",
            prompt=(
                f"Summarise the collections risk for {snapshot.customer.name}. "
                f"Metrics: {metrics.model_dump()}. Risk score {risk_score}. Signals: {signals}."
            ),
            variables={
                "customer_name": snapshot.customer.name,
                "invoices_paid": metrics.invoices_paid,
                "avg_days_late": metrics.avg_days_late,
                "open_invoices": metrics.open_invoices,
                "past_due_balance": f"${metrics.past_due_balance:,.0f}",
                "oldest_days_past_due": metrics.oldest_days_past_due,
                "promises_broken": metrics.promises_broken,
                "promises_made": metrics.promises_made,
                "risk_score": risk_score,
                "risk_band": _band(risk_score),
                "recovery_outlook": _outlook(archetype, metrics, risk_score),
                "predictability": _predictability(metrics),
                "trend_word": trend_word,
            },
        )
    ).text

    return RiskProfile(
        account_id=snapshot.customer.account_id,
        archetype=archetype,
        risk_score=min(100.0, risk_score),
        risk_band=_band(risk_score),
        predictability=_predictability(metrics),
        expected_days_late=round(max(0.0, metrics.avg_days_late + 2 * metrics.lateness_trend), 1),
        recovery_outlook=_outlook(archetype, metrics, risk_score),
        metrics=metrics,
        signals=signals,
        narrative=narrative,
    )
