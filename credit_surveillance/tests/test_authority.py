"""Authority matrix: increases, and suspensions above the exposure threshold."""

from decimal import Decimal

from credit_surveillance.authority import SUSPEND_APPROVAL_THRESHOLD, evaluate_authority


def _decision(action, current, proposed, exposure):
    return evaluate_authority(
        action=action,
        current_limit=Decimal(current),
        proposed_limit=Decimal(proposed),
        exposure=Decimal(exposure),
    )


def test_threshold_is_seventy_five_thousand():
    assert SUSPEND_APPROVAL_THRESHOLD == Decimal("75000.00")


def test_affirm_reduce_and_conditions_are_within_analyst_authority():
    for action, proposed in (("affirm", "100000.00"), ("reduce", "112000.00"), ("conditions", "80000.00")):
        decision = _decision(action, "150000.00", proposed, "90000.00")
        assert decision.can_post is True
        assert decision.requires_approver is False
        assert decision.code == "within_authority"
        assert decision.required_role is None


def test_any_limit_increase_requires_a_named_approver():
    decision = _decision("increase", "100000.00", "120000.00", "50000.00")
    assert decision.requires_approver is True
    assert decision.can_post is False
    assert decision.code == "limit_increase"
    assert decision.required_role == "credit_manager"
    assert "credit_limit=100000.00" in decision.reason
    assert "proposed_limit=120000.00" in decision.reason

    disguised = _decision("affirm", "100000.00", "100000.01", "50000.00")
    assert disguised.code == "limit_increase"


def test_suspension_at_the_threshold_can_post():
    decision = _decision("suspend", "80000.00", "80000.00", "75000.00")
    assert decision.requires_approver is False
    assert decision.can_post is True
    assert "75000.00" in decision.reason


def test_suspension_above_the_threshold_cannot_post():
    decision = _decision("suspend", "200000.00", "200000.00", "75000.01")
    assert decision.requires_approver is True
    assert decision.can_post is False
    assert decision.code == "suspend_above_threshold"
    assert "exposure=75000.01" in decision.reason
    assert "suspend_approval_threshold=75000.00" in decision.reason


def test_seeded_redline_suspension_is_above_the_threshold(conn):
    from credit_surveillance.narrator import DeterministicNarrator
    from credit_surveillance.seed import seed_database
    from credit_surveillance.service import run_surveillance

    seed_database(conn)
    reviews = run_surveillance(conn, narrator=DeterministicNarrator())
    by_account = {review.account_id: review for review in reviews}
    assert by_account["RL-4408"].authority.code == "suspend_above_threshold"
    assert by_account["RL-4408"].exposure == Decimal("185000.00")
    for account_id in ("NW-1044", "HB-2201", "VP-3310"):
        assert by_account[account_id].authority.requires_approver is False
