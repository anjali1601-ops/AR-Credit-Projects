"""The deterministic financial engine.

These are the numbers the memo asserts, so where practical they are checked
against values computed by hand from the seed statements rather than against the
engine's own output.
"""

from __future__ import annotations

import pytest

from credit_underwriter.evidence import EvidenceRegistry
from credit_underwriter.finance import (
    analyse_financials,
    assign_rating,
    compute_ratios,
    compute_trends,
    recommend_limit,
    spread_statements,
)
from credit_underwriter.finance.rating import (
    BAND_LABELS,
    ESTIMATED_PD_PERCENT,
    RATING_BANDS,
    band_for_score,
    interpolate_score,
    round_limit,
)
from credit_underwriter.models import Direction, EvidenceKind, TrendDirection


def latest_ratio(analysis, key: str):
    found = analysis.ratio(key)
    assert found is not None, f"no ratio {key!r} for the latest period"
    return found


# --------------------------------------------------------------------------------------
# Ratios, checked against hand arithmetic on the seed data
# --------------------------------------------------------------------------------------


def test_atlas_liquidity_ratios_match_hand_arithmetic(analyses, atlas):
    analysis = analyses["atlas-precision-works"]
    bs = atlas.latest_statement.balance_sheet

    assert latest_ratio(analysis, "current_ratio").value == pytest.approx(
        bs.total_current_assets / bs.total_current_liabilities, abs=1e-9
    )
    assert latest_ratio(analysis, "quick_ratio").value == pytest.approx(
        (bs.cash_and_equivalents + bs.accounts_receivable) / bs.total_current_liabilities, abs=1e-9
    )
    assert latest_ratio(analysis, "working_capital").value == pytest.approx(
        bs.total_current_assets - bs.total_current_liabilities, abs=1e-9
    )


def test_atlas_leverage_and_coverage_match_hand_arithmetic(analyses, atlas):
    analysis = analyses["atlas-precision-works"]
    latest = atlas.latest_statement
    ebitda = latest.income_statement.ebitda
    net_debt = latest.balance_sheet.total_debt - latest.balance_sheet.cash_and_equivalents

    assert latest_ratio(analysis, "net_debt_to_ebitda").value == pytest.approx(
        net_debt / ebitda, abs=1e-9
    )
    assert latest_ratio(analysis, "ebitda_interest_coverage").value == pytest.approx(
        ebitda / latest.income_statement.interest_expense, abs=1e-9
    )
    assert latest_ratio(analysis, "ebitda_margin").value == pytest.approx(
        ebitda / latest.income_statement.revenue * 100, abs=1e-9
    )


def test_dso_uses_a_365_day_convention(analyses, northwind):
    analysis = analyses["northwind-logistics"]
    latest = northwind.latest_statement
    assert latest_ratio(analysis, "dso").value == pytest.approx(
        latest.balance_sheet.accounts_receivable / latest.income_statement.revenue * 365,
        abs=1e-9,
    )


def test_an_uncomputable_ratio_explains_itself_instead_of_returning_zero(analyses):
    """A ratio that is defined but not interpretable must say why."""
    for applicant_id, analysis in analyses.items():
        for r in analysis.ratios:
            if r.value is None:
                assert r.not_meaningful_reason, (
                    f"{applicant_id} {r.key} {r.period} is None with no reason given"
                )


def test_every_ratio_is_traceable_to_statement_lines(atlas):
    registry = EvidenceRegistry()
    analysis = analyse_financials(atlas, registry)
    statement_ids = {
        item.evidence_id
        for item in registry
        if item.kind is EvidenceKind.STATEMENT_LINE
    }
    assert statement_ids

    for r in analysis.ratios:
        assert r.inputs, f"ratio {r.key} declares no inputs"
        for input_id in r.inputs:
            assert input_id in statement_ids, f"{r.key} cites unknown statement line {input_id}"


def test_every_ratio_is_registered_as_citable_evidence(atlas):
    registry = EvidenceRegistry()
    analysis = analyse_financials(atlas, registry)
    ratio_evidence = {
        item.evidence_id for item in registry if item.kind is EvidenceKind.RATIO
    }
    assert len(ratio_evidence) == len(analysis.ratios)


def test_ratios_are_computed_for_every_submitted_period(analyses, atlas):
    analysis = analyses["atlas-precision-works"]
    assert {r.period for r in analysis.ratios} == {s.period_label for s in atlas.statements}


def test_compute_ratios_is_pure(atlas):
    """Same input, same output: the engine must not depend on hidden state."""
    first = compute_ratios(atlas.ordered_statements)
    second = compute_ratios(atlas.ordered_statements)
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]


def test_the_analysis_does_not_depend_on_the_order_statements_were_submitted(atlas):
    """The engine sorts by fiscal year, so a shuffled submission must not matter."""
    shuffled = atlas.model_copy(deep=True)
    shuffled.statements.reverse()

    baseline = analyse_financials(atlas, EvidenceRegistry())
    reordered = analyse_financials(shuffled, EvidenceRegistry())
    assert reordered.rating.composite_score == baseline.rating.composite_score
    assert reordered.periods == baseline.periods


# --------------------------------------------------------------------------------------
# Spreading checks
# --------------------------------------------------------------------------------------


def test_seeded_balance_sheets_balance(applications):
    """Every seeded statement must foot, or the fixtures themselves are wrong."""
    for application in applications.values():
        for statement in application.statements:
            bs = statement.balance_sheet
            assert bs.total_assets == pytest.approx(
                bs.total_liabilities + bs.total_equity, rel=1e-6
            ), f"{application.applicant_id} {statement.period_label} does not balance"


def test_clean_applicant_spreads_without_exception(atlas):
    assert spread_statements(atlas) == []


def test_spread_detects_an_unbalanced_balance_sheet(atlas):
    broken = atlas.model_copy(deep=True)
    broken.statements[-1].balance_sheet.other_current_assets += 500_000

    exceptions = spread_statements(broken)
    assert "balance_sheet_balances" in {e.check for e in exceptions}
    exception = next(e for e in exceptions if e.check == "balance_sheet_balances")
    assert exception.delta == pytest.approx(500_000)
    assert exception.period == broken.statements[-1].period_label


def test_spread_detects_a_broken_equity_roll_forward(atlas):
    """Equity that moves by more than earnings less dividends is unexplained."""
    broken = atlas.model_copy(deep=True)
    broken.statements[-1].balance_sheet.total_equity += 2_000_000
    broken.statements[-1].balance_sheet.other_current_assets += 2_000_000

    assert "equity_roll_forward" in {e.check for e in spread_statements(broken)}


def test_spread_exceptions_reach_the_findings_list(atlas):
    """A spreading failure must surface as a finding, not stay buried."""
    broken = atlas.model_copy(deep=True)
    broken.statements[-1].balance_sheet.other_current_assets += 500_000

    analysis = analyse_financials(broken, EvidenceRegistry())
    surfaced = [f for f in analysis.findings if f.key.startswith("spread_exception_")]
    assert len(surfaced) == len(analysis.spread_exceptions) > 0
    assert all(f.direction is Direction.ADVERSE for f in surfaced)
    assert all(f.category == "data_quality" for f in surfaced)


# --------------------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------------------


def test_trend_direction_follows_polarity_not_raw_movement(northwind):
    """Rising leverage is deterioration even though the number went up."""
    ratios = compute_ratios(northwind.ordered_statements)
    trends = {t.key: t for t in compute_trends(northwind.ordered_statements, ratios)}

    leverage = trends["net_debt_to_ebitda"]
    assert leverage.last_value > leverage.first_value
    assert not leverage.higher_is_better
    assert leverage.direction is TrendDirection.DETERIORATING

    revenue = trends["revenue"]
    assert revenue.last_value > revenue.first_value
    assert revenue.higher_is_better
    assert revenue.direction is TrendDirection.IMPROVING


def test_a_movement_inside_the_stable_band_is_not_reported_as_a_trend(atlas):
    from credit_underwriter.finance.trends import STABLE_BAND

    ratios = compute_ratios(atlas.ordered_statements)
    for trend in compute_trends(atlas.ordered_statements, ratios):
        if trend.direction is TrendDirection.STABLE and trend.percent_change is not None:
            assert abs(trend.percent_change) <= STABLE_BAND + 1e-9


def test_cagr_is_withheld_across_a_sign_change(veritas):
    """A CAGR computed across zero is meaningless and must not be invented."""
    ratios = compute_ratios(veritas.ordered_statements)
    for trend in compute_trends(veritas.ordered_statements, ratios):
        crosses_zero = (trend.first_value <= 0 < trend.last_value) or (
            trend.last_value <= 0 < trend.first_value
        )
        if crosses_zero:
            assert trend.cagr is None, f"{trend.key} reports a CAGR across a sign change"


def test_trends_need_at_least_two_periods(atlas):
    single = atlas.ordered_statements[:1]
    assert compute_trends(single, compute_ratios(single)) == []


def test_northwind_shows_broad_deterioration(analyses):
    """The marginal applicant's story is a deteriorating trend, so assert it."""
    analysis = analyses["northwind-logistics"]
    deteriorating = [
        t for t in analysis.trends if t.direction is TrendDirection.DETERIORATING
    ]
    assert len(deteriorating) >= 3
    assert analysis.trend("ebitda_margin").direction is TrendDirection.DETERIORATING
    assert analysis.trend("net_debt_to_ebitda").direction is TrendDirection.DETERIORATING


# --------------------------------------------------------------------------------------
# Rating scorecard
# --------------------------------------------------------------------------------------


def test_rating_bands_cover_the_whole_scale_without_gaps():
    thresholds = [t for t, _, _ in RATING_BANDS]
    assert thresholds == sorted(thresholds, reverse=True), "bands must descend"
    assert thresholds[-1] == 0.0, "the bottom band must catch every score"
    assert [g for _, g, _ in RATING_BANDS] == list(range(1, 11))
    assert set(BAND_LABELS) == set(range(1, 11))
    assert set(ESTIMATED_PD_PERCENT) == set(range(1, 11))


@pytest.mark.parametrize("score", [0.0, 0.1, 12.9, 13.0, 25.0, 36.7, 50.0, 93.3, 99.9, 100.0])
def test_every_score_maps_to_exactly_one_grade(score: float):
    grade, label = band_for_score(score)
    assert 1 <= grade <= 10
    assert label == BAND_LABELS[grade]
    threshold = next(t for t, g, _ in RATING_BANDS if g == grade)
    assert score >= threshold


def test_better_scores_never_map_to_worse_grades():
    grades = [band_for_score(s / 10)[0] for s in range(0, 1001)]
    assert grades == sorted(grades, reverse=True)


def test_estimated_pd_rises_monotonically_with_the_grade():
    pds = [ESTIMATED_PD_PERCENT[g] for g in range(1, 11)]
    assert pds == sorted(pds)


def test_interpolation_is_monotonic_and_clamped_at_both_ends():
    """Lower-is-better metric: leverage of 1.0x scores 100, 5.0x scores 20."""
    breakpoints = ((1.0, 100.0), (3.0, 60.0), (5.0, 20.0))
    assert interpolate_score(0.5, breakpoints) == 100.0
    assert interpolate_score(9.0, breakpoints) == 20.0
    assert interpolate_score(3.0, breakpoints) == pytest.approx(60.0)
    assert 60.0 < interpolate_score(2.0, breakpoints) < 100.0

    scores = [interpolate_score(x / 10, breakpoints) for x in range(0, 100)]
    assert scores == sorted(scores, reverse=True)


def test_seeded_applicants_separate_across_the_rating_scale(analyses):
    strong = analyses["atlas-precision-works"].rating
    marginal = analyses["northwind-logistics"].rating
    weak = analyses["veritas-metal-trading"].rating

    assert strong.composite_score > marginal.composite_score > weak.composite_score
    assert strong.grade < marginal.grade < weak.grade
    assert strong.grade <= 3, "the clean applicant should rate in the top bands"
    assert weak.grade >= 9, "the decline-worthy applicant should rate near the floor"


def test_scorecard_weights_sum_to_one(analyses):
    for applicant_id, analysis in analyses.items():
        total = sum(f.weight for f in analysis.rating.factors)
        assert total == pytest.approx(1.0, abs=1e-9), applicant_id


def test_composite_score_is_the_weighted_sum_of_its_factors(analyses):
    for applicant_id, analysis in analyses.items():
        expected = sum(f.weighted_score for f in analysis.rating.factors)
        assert analysis.rating.composite_score == pytest.approx(expected, abs=0.05), applicant_id


def test_rating_band_label_and_pd_agree_with_the_grade(analyses):
    for analysis in analyses.values():
        rating = analysis.rating
        assert rating.band_label == BAND_LABELS[rating.grade]
        assert rating.estimated_pd_percent == ESTIMATED_PD_PERCENT[rating.grade]


def test_rating_is_reproducible(atlas):
    analysis = analyse_financials(atlas, EvidenceRegistry())
    again = assign_rating(atlas, analysis.ratios, analysis.trends)
    assert again.composite_score == analysis.rating.composite_score
    assert again.grade == analysis.rating.grade
    assert [f.model_dump() for f in again.factors] == [
        f.model_dump() for f in analysis.rating.factors
    ]


def test_every_scorecard_factor_cites_its_inputs_or_says_why_not(analyses):
    for applicant_id, analysis in analyses.items():
        for factor in analysis.rating.factors:
            if factor.raw_value is not None:
                assert factor.inputs, f"{applicant_id} factor {factor.key} cites nothing"


# --------------------------------------------------------------------------------------
# Limit capacity
# --------------------------------------------------------------------------------------


def test_limit_guidance_is_bound_by_its_tightest_capacity_test(analyses):
    for applicant_id, analysis in analyses.items():
        guidance = analysis.limit_guidance
        capacities = {
            "tangible net worth": guidance.tangible_net_worth_capacity,
            "cash flow": guidance.cash_flow_capacity,
            "working capital": guidance.working_capital_capacity,
        }
        assert guidance.binding_constraint in capacities, applicant_id
        assert capacities[guidance.binding_constraint] == pytest.approx(
            min(capacities.values())
        ), applicant_id


def test_indicative_limit_never_exceeds_the_request(analyses, applications):
    for applicant_id, analysis in analyses.items():
        requested = applications[applicant_id].requested_limit
        assert analysis.limit_guidance.indicative_limit <= requested, applicant_id


def test_indicative_terms_never_exceed_the_request(analyses, applications):
    for applicant_id, analysis in analyses.items():
        requested = applications[applicant_id].requested_terms_days
        assert analysis.limit_guidance.indicative_terms_days <= requested, applicant_id


def test_a_weaker_grade_never_earns_a_larger_limit(atlas):
    """The grade multiplier must be monotonic in the grade."""
    analysis = analyse_financials(atlas, EvidenceRegistry())
    limits = []
    for grade in range(1, 11):
        rating = analysis.rating.model_copy(
            update={"grade": grade, "band_label": BAND_LABELS[grade]}
        )
        limits.append(recommend_limit(atlas, rating).indicative_limit)
    assert limits == sorted(limits, reverse=True)


def test_round_limit_never_rounds_up_past_the_capacity_it_was_given():
    for value in (0, 1, 999, 12_345, 250_001, 3_400_000):
        assert round_limit(value) <= value


def test_round_limit_floors_a_negative_capacity_at_zero():
    assert round_limit(-500_000) == 0.0


def test_veritas_has_no_lending_capacity(analyses):
    """The decline-worthy applicant must not produce a positive indicative limit."""
    assert analyses["veritas-metal-trading"].limit_guidance.indicative_limit == 0.0


# --------------------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------------------


def test_findings_carry_a_direction_and_a_citation(analyses):
    for applicant_id, analysis in analyses.items():
        assert analysis.findings, f"{applicant_id} produced no findings"
        for finding in analysis.findings:
            assert finding.direction in (Direction.ADVERSE, Direction.SUPPORTIVE)
            assert finding.evidence_ids, f"{finding.key} has no evidence"
            assert finding.statement.strip()


def test_finding_keys_are_unique_within_an_analysis(analyses):
    for applicant_id, analysis in analyses.items():
        keys = [f.key for f in analysis.findings]
        assert len(keys) == len(set(keys)), applicant_id


def test_the_clean_applicant_is_not_buried_in_adverse_findings(analyses):
    analysis = analyses["atlas-precision-works"]
    adverse = [f for f in analysis.findings if f.direction is Direction.ADVERSE]
    supportive = [f for f in analysis.findings if f.direction is Direction.SUPPORTIVE]
    assert len(supportive) > len(adverse)


def test_the_weak_applicant_triggers_the_leverage_and_coverage_rules(analyses):
    analysis = analyses["veritas-metal-trading"]
    adverse_keys = {f.key for f in analysis.findings if f.direction is Direction.ADVERSE}
    assert any("leverage" in k or "net_debt" in k for k in adverse_keys)
    assert any("coverage" in k or "interest" in k for k in adverse_keys)
