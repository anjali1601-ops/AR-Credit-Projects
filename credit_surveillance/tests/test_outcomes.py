"""The four seeded accounts land on four different actions, each citing its figures."""

from datetime import date
from decimal import Decimal

from credit_surveillance.cli import main
from credit_surveillance.exposure import AS_OF
from credit_surveillance.models import ExposureFacts, NarrativeRequest
from credit_surveillance.narrator import DeterministicNarrator, compose_memo
from credit_surveillance.policy import compute_reduced_limit, decide
from credit_surveillance.seed import seed_database
from credit_surveillance.service import account_facts, run_surveillance


def _lines(narrative: str) -> set[str]:
    return {line.strip() for line in narrative.splitlines()}


def _healthy(**overrides) -> ExposureFacts:
    values = dict(
        as_of=AS_OF,
        credit_limit=Decimal("100000.00"),
        accounts_receivable=Decimal("40000.00"),
        current_ar=Decimal("40000.00"),
        past_due_ar=Decimal("0.00"),
        open_orders=Decimal("10000.00"),
        exposure=Decimal("50000.00"),
        over_limit_amount=Decimal("0.00"),
        utilization=Decimal("0.500000"),
        past_due_ratio=Decimal("0.000000"),
        avg_days_to_pay_recent=Decimal("30.00"),
        avg_days_to_pay_baseline=Decimal("30.00"),
        payment_drift_days=Decimal("0.00"),
        terms_days=30,
        late_payment_rate=Decimal("0.000000"),
        invoices_paid_recent=4,
        invoices_late_recent=0,
        broken_promise_count=0,
        broken_promise_amount=Decimal("0.00"),
        max_days_past_due=0,
        signals=(),
    )
    values.update(overrides)
    return ExposureFacts(**values)


def test_reduced_limit_steps_down_to_thousands():
    proposed, penalty = compute_reduced_limit(
        Decimal("150000.00"),
        Decimal("15.00"),
        Decimal("0.500000"),
    )
    assert penalty == Decimal("0.250000")
    assert proposed == Decimal("112000.00")


def test_penalty_is_capped_at_forty_percent():
    proposed, penalty = compute_reduced_limit(
        Decimal("100000.00"),
        Decimal("50.00"),
        Decimal("1.000000"),
    )
    assert penalty == Decimal("0.400000")
    assert proposed == Decimal("60000.00")


def test_four_seeded_outcomes_cite_the_figures_they_use(conn):
    seed_database(conn)
    reviews = run_surveillance(conn, narrator=DeterministicNarrator())
    by_account = {review.account_id: review for review in reviews}

    assert by_account["NW-1044"].action == "affirm"
    assert by_account["NW-1044"].proposed_limit == Decimal("100000.00")
    assert by_account["NW-1044"].status == "ready"
    assert by_account["NW-1044"].rule_codes == ("affirm_within_policy",)

    harbor = by_account["HB-2201"]
    assert harbor.action == "reduce"
    assert harbor.proposed_limit == Decimal("112000.00")
    assert harbor.cited_figures["penalty_rate"] == "0.250000"
    assert harbor.cited_figures["payment_drift_days"] == "15.00"
    assert harbor.cited_figures["late_payment_rate"] == "0.500000"
    assert harbor.status == "ready"

    vesper = by_account["VP-3310"]
    assert vesper.action == "conditions"
    assert vesper.proposed_limit == Decimal("80000.00")
    assert "conditions_over_limit" in vesper.rule_codes
    assert "conditions_broken_promise" in vesper.rule_codes
    assert any("over_limit_amount=14000.00" in line for line in vesper.conditions)
    assert vesper.status == "ready"

    redline = by_account["RL-4408"]
    assert redline.action == "suspend"
    assert redline.proposed_limit == Decimal("200000.00")
    assert redline.status == "pending_approval"
    assert "suspend_broken_promises" in redline.rule_codes
    assert "suspend_severe_delinquency" in redline.rule_codes
    assert redline.cited_figures["exposure"] == "185000.00"
    assert redline.cited_figures["broken_promise_amount"] == "45000.00"

    assert [by_account[key].action for key in ("NW-1044", "HB-2201", "VP-3310", "RL-4408")] == [
        "affirm",
        "reduce",
        "conditions",
        "suspend",
    ]

    for review in reviews:
        lines = _lines(review.narrative)
        for key, value in review.cited_figures.items():
            assert f"{key}={value}" in lines
        assert review.cited_figures["proposed_limit"] in review.narrative
        assert "surveillance engine" in review.narrative


def test_drafting_does_not_change_the_limit_or_suspend(conn):
    seed_database(conn)
    reviews = run_surveillance(conn, narrator=DeterministicNarrator())
    harbor = next(review for review in reviews if review.account_id == "HB-2201")
    redline = next(review for review in reviews if review.account_id == "RL-4408")
    assert harbor.status == "ready"
    assert account_facts(conn, _account(conn, "HB-2201")).credit_limit == Decimal("150000.00")
    assert _account(conn, "RL-4408").status == "open"
    assert redline.action == "suspend"


def test_gray_zone_is_conditions_not_affirm():
    facts = _healthy(
        utilization=Decimal("0.900000"),
        exposure=Decimal("90000.00"),
        payment_drift_days=Decimal("6.00"),
    )
    recommendation = decide(facts)
    assert recommendation.action == "conditions"
    assert recommendation.rule_codes == ("conditions_outside_affirm_band",)


def test_healthy_limit_request_becomes_an_increase():
    recommendation = decide(_healthy(), requested_limit=Decimal("120000.00"))
    assert recommendation.action == "increase"
    assert recommendation.proposed_limit == Decimal("120000.00")
    assert recommendation.cited_figures["requested_limit"] == "120000.00"


def test_limit_request_does_not_override_a_suspension():
    facts = _healthy(
        credit_limit=Decimal("200000.00"),
        accounts_receivable=Decimal("160000.00"),
        past_due_ar=Decimal("78000.00"),
        current_ar=Decimal("82000.00"),
        open_orders=Decimal("25000.00"),
        exposure=Decimal("185000.00"),
        utilization=Decimal("0.925000"),
        past_due_ratio=Decimal("0.487500"),
        payment_drift_days=Decimal("37.00"),
        avg_days_to_pay_recent=Decimal("72.00"),
        avg_days_to_pay_baseline=Decimal("35.00"),
        late_payment_rate=Decimal("1.000000"),
        broken_promise_count=3,
        broken_promise_amount=Decimal("45000.00"),
        max_days_past_due=67,
    )
    recommendation = decide(facts, requested_limit=Decimal("250000.00"))
    assert recommendation.action == "suspend"


def test_narrator_does_not_recompute_a_contradictory_limit():
    request = NarrativeRequest(
        account_name="Example",
        account_id="EX-1",
        as_of=date(2026, 9, 22),
        action="reduce",
        rule_codes=("reduce_payment_drift",),
        headline="Reduce to proposed_limit=112000.00.",
        cited_figures={"credit_limit": "150000.00", "proposed_limit": "112000.00"},
        conditions=(),
        signals=("payment_drift",),
    )
    memo = compose_memo(
        "Reduce the limit to proposed_limit=1.00.",
        request,
        "Within analyst authority.",
    )
    lines = _lines(memo)
    assert "proposed_limit=112000.00" in lines
    assert "proposed_limit=1.00" not in lines
    prose = DeterministicNarrator().narrate(request)
    assert "112000.00" in prose
    assert "1.00" not in prose


def test_cli_demo_prints_all_four_outcomes(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "demo.db"), "demo"])
    captured = capsys.readouterr()
    assert code == 0
    output = captured.out
    assert "NW-1044" in output and "AFFIRM" in output
    assert "HB-2201" in output and "REDUCE" in output
    assert "VP-3310" in output and "CONDITIONS" in output
    assert "RL-4408" in output and "SUSPEND" in output
    assert "penalty_rate=0.250000" in output
    assert "proposed_limit=112000.00" in output


def _account(conn, account_id):
    from credit_surveillance.db import get_account

    return get_account(conn, account_id)
