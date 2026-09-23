"""Metric math is computed from the activity rows, not stored as a rating."""

from __future__ import annotations

from datetime import date

import pytest

from team_lead.metrics import (
    build_snapshot,
    cash_applied,
    cash_target_for,
    dispute_cycle_days,
    overall_rating,
    promise_counts,
    quality_score,
    sustained_overtime,
)
from team_lead.models import CashApplication, Dispute, Promise, QAReview


def test_cash_applied_is_the_sum_of_remittances():
    rows = [
        CashApplication(amount=800_000, applied_on=date(2026, 7, 6), reference="a"),
        CashApplication(amount=720_000, applied_on=date(2026, 7, 20), reference="b"),
        CashApplication(amount=530_000, applied_on=date(2026, 8, 3), reference="c"),
        CashApplication(amount=400_000, applied_on=date(2026, 8, 17), reference="d"),
    ]
    assert cash_applied(rows) == 2_450_000


def test_promise_kept_rate_is_a_count_not_a_stored_grade():
    kept = [Promise(amount=100, kept=True, customer="a", due_on=date(2026, 7, 1)) for _ in range(3)]
    broken = [Promise(amount=900, kept=False, customer="b", due_on=date(2026, 7, 2))]
    made, kept_n, rate = promise_counts(kept + broken)
    assert (made, kept_n, rate) == (4, 3, 0.75)
    assert promise_counts([]) == (0, 0, 0.0)


def test_open_disputes_do_not_shorten_cycle_time():
    closed, days = dispute_cycle_days(
        [
            Dispute(customer="a", opened_on=date(2026, 7, 1), closed_on=date(2026, 7, 11)),
            Dispute(customer="b", opened_on=date(2026, 7, 1), closed_on=None),
        ]
    )
    assert closed == 1
    assert days == 10


def test_quality_score_is_the_mean_of_qa_reviews():
    reviews = [
        QAReview(score=98, comment="a", reviewed_on=date(2026, 7, 9)),
        QAReview(score=97, comment="b", reviewed_on=date(2026, 7, 21)),
        QAReview(score=96, comment="c", reviewed_on=date(2026, 8, 2)),
        QAReview(score=99, comment="d", reviewed_on=date(2026, 8, 14)),
        QAReview(score=97, comment="e", reviewed_on=date(2026, 8, 26)),
    ]
    count, score = quality_score(reviews)
    assert count == 5
    assert score == pytest.approx(97.4)


def test_sustained_overtime_needs_four_weeks_at_ten_hours():
    assert sustained_overtime([10, 10, 10, 9]) is False
    assert sustained_overtime([10, 10, 10, 10]) is True
    assert sustained_overtime([12, 14, 11, 13, 12, 15, 11, 13]) is True
    assert sustained_overtime([2, 1, 3, 2, 2, 1, 3, 2]) is False


def test_ramp_cash_bar_is_65_percent_of_the_full_book():
    target, ramp = cash_target_for(3)
    assert ramp is True
    assert target == 1_170_000
    target, ramp = cash_target_for(6)
    assert ramp is False
    assert target == 1_800_000


def test_rating_rules():
    assert overall_rating(
        {"cash_applied": "exceeds", "promises_kept": "exceeds", "dispute_cycle": "exceeds", "quality": "exceeds"}
    ) == "exceeds"
    assert overall_rating(
        {"cash_applied": "exceeds", "promises_kept": "exceeds", "dispute_cycle": "exceeds", "quality": "below"}
    ) == "meets"
    assert overall_rating(
        {"cash_applied": "meets", "promises_kept": "meets", "dispute_cycle": "meets", "quality": "meets"}
    ) == "meets"
    assert overall_rating(
        {"cash_applied": "below", "promises_kept": "below", "dispute_cycle": "meets", "quality": "exceeds"}
    ) == "below"


def test_seeded_figures_match_the_activity_rows(store):
    expected = {
        "TL-MAYA": dict(cash=2_450_000, kept=46, made=48, cycle=6.2, quality=97.4, cases=148, ot=False, ramp=False),
        "TL-ANDRE": dict(cash=1_920_000, kept=34, made=40, cycle=11.4, quality=91.0, cases=186, ot=True, ramp=False),
        "TL-PRIYA": dict(cash=1_220_000, kept=18, made=22, cycle=13.4, quality=86.0, cases=78, ot=False, ramp=True),
        "TL-JORDAN": dict(cash=1_280_000, kept=22, made=36, cycle=21.0, quality=78.0, cases=95, ot=False, ramp=False),
    }
    for teammate_id, want in expected.items():
        person = store.get_teammate(teammate_id)
        snapshot = build_snapshot(
            tenure_months=person.tenure_months,
            cash=store.cash_applications(teammate_id),
            promises=store.promises(teammate_id),
            disputes=store.disputes(teammate_id),
            reviews=store.qa_reviews(teammate_id),
            overtime_hours=store.overtime_hours(teammate_id),
            cases_closed=store.cases_closed(teammate_id),
        )
        assert snapshot.cash_applied == want["cash"]
        assert snapshot.promises_kept == want["kept"]
        assert snapshot.promises_made == want["made"]
        assert snapshot.dispute_cycle_days == pytest.approx(want["cycle"])
        assert snapshot.quality_score == pytest.approx(want["quality"])
        assert snapshot.cases_closed == want["cases"]
        assert snapshot.sustained_overtime is want["ot"]
        assert snapshot.ramp is want["ramp"]
        assert snapshot.cash_target == (1_170_000 if want["ramp"] else 1_800_000)
