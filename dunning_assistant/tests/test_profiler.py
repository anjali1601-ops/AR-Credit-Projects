from __future__ import annotations

import pytest

from dunning.agents.profiler import classify_archetype, compute_metrics, score_risk
from tests.conftest import make_snapshot


def test_metrics_capture_payment_lag_and_aging():
    snapshot = make_snapshot(paid_days_late=[10, 14, 18], open_dpd=[75, 20], amount=10_000)
    metrics = compute_metrics(snapshot)

    assert metrics.invoices_paid == 3
    assert metrics.open_invoices == 2
    assert metrics.avg_days_late == pytest.approx(14.0)
    assert metrics.median_days_late == pytest.approx(14.0)
    assert metrics.worst_days_late == 18
    assert metrics.oldest_days_past_due == 75
    assert metrics.past_due_balance == pytest.approx(20_000)
    assert metrics.aging_61_90 == pytest.approx(10_000)
    assert metrics.aging_0_30 == pytest.approx(10_000)
    assert metrics.aging_90_plus == pytest.approx(0)
    # Balance-weighted days past due, not a simple mean of the buckets.
    assert metrics.weighted_days_past_due == pytest.approx(47.5)
    assert metrics.exposure_vs_credit_limit == pytest.approx(0.2)


def test_lateness_trend_detects_degradation_and_stability():
    worsening = compute_metrics(make_snapshot(paid_days_late=[2, 8, 16, 25, 34, 44]))
    stable = compute_metrics(make_snapshot(paid_days_late=[15, 14, 16, 15, 15, 14]))

    assert worsening.lateness_trend > 1.2
    assert abs(stable.lateness_trend) < 0.5
    assert stable.days_late_stddev < worsening.days_late_stddev


def test_email_responsiveness_metrics():
    snapshot = make_snapshot(
        emails=[
            (60, "outbound", "Following up on the past due balance."),
            (58, "inbound", "Thanks, we will process this week."),
            (30, "outbound", "Checking in again."),
            (14, "outbound", "Third attempt, please advise."),
            (3, "outbound", "Still nothing - can you confirm a date?"),
        ]
    )
    metrics = compute_metrics(snapshot)

    assert metrics.days_since_last_inbound_email == 58
    assert metrics.unanswered_outbound_emails == 3


def test_broken_promises_and_partial_payments_raise_risk():
    clean = make_snapshot(open_dpd=[40], promises=[True])
    broken = make_snapshot(open_dpd=[40], promises=[False, False, False])

    clean_score, _ = score_risk(compute_metrics(clean))
    broken_score, signals = score_risk(compute_metrics(broken))

    assert broken_score > clean_score + 10
    assert any("promises broken" in signal for signal in signals)


def test_risk_score_is_bounded_and_monotonic_in_aging():
    scores = [score_risk(compute_metrics(make_snapshot(open_dpd=[dpd])))[0] for dpd in (10, 45, 95, 160)]
    assert scores == sorted(scores)
    assert all(0 <= score <= 100 for score in scores)


@pytest.mark.parametrize(
    "account_id,expected",
    [
        ("ACC-1001", "reliable_but_late"),
        ("ACC-1002", "reliable_but_late"),
        ("ACC-1003", "reliable_but_late"),
        ("ACC-2001", "deteriorating_avoidant"),
        ("ACC-2002", "deteriorating_avoidant"),
        ("ACC-2003", "deteriorating_avoidant"),
        ("ACC-3001", "high_risk_delinquent"),
        ("ACC-3002", "high_risk_delinquent"),
        ("ACC-3003", "high_risk_delinquent"),
    ],
)
def test_seeded_accounts_are_classified_into_their_archetype(repository, account_id, expected):
    metrics = compute_metrics(repository.snapshot(account_id))
    risk_score, _ = score_risk(metrics)
    assert classify_archetype(metrics, risk_score) == expected


def test_profile_narrative_and_labels(deps):
    from dunning.agents.profiler import build_profile

    profile = build_profile(deps.repository.snapshot("ACC-1001"), deps.llm)

    assert profile.risk_band == "low"
    assert profile.predictability == "metronomic"
    assert profile.recovery_outlook == "self_correcting"
    assert "Harbor Point Logistics" in profile.narrative
    assert profile.signals
