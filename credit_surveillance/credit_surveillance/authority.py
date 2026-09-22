"""Credit-manager authority matrix.

A limit increase never posts on analyst authority. A suspension posts on
analyst authority only when exposure is at or below the threshold.
"""

from decimal import Decimal

from credit_surveillance.formatting import money
from credit_surveillance.models import AuthorityDecision

SUSPEND_APPROVAL_THRESHOLD = Decimal("75000.00")
APPROVER_ROLE = "credit_manager"
ANALYST_ROLES = frozenset({"analyst", "credit_manager"})


def evaluate_authority(
    *,
    action: str,
    current_limit: Decimal,
    proposed_limit: Decimal,
    exposure: Decimal,
) -> AuthorityDecision:
    """Decide whether an analyst may post the recommendation."""
    if action == "increase" or proposed_limit > current_limit:
        return AuthorityDecision(
            can_post=False,
            requires_approver=True,
            code="limit_increase",
            reason=(
                "Limit increase from "
                f"credit_limit={money(current_limit)} to proposed_limit={money(proposed_limit)} "
                "cannot post without a named credit manager."
            ),
            required_role=APPROVER_ROLE,
        )
    if action == "suspend" and exposure > SUSPEND_APPROVAL_THRESHOLD:
        return AuthorityDecision(
            can_post=False,
            requires_approver=True,
            code="suspend_above_threshold",
            reason=(
                "Suspension cannot post without a named credit manager because "
                f"exposure={money(exposure)} is above "
                f"suspend_approval_threshold={money(SUSPEND_APPROVAL_THRESHOLD)}."
            ),
            required_role=APPROVER_ROLE,
        )
    if action == "suspend":
        return AuthorityDecision(
            can_post=True,
            requires_approver=False,
            code="within_authority",
            reason=(
                "Suspension is within analyst authority because "
                f"exposure={money(exposure)} is at or below "
                f"suspend_approval_threshold={money(SUSPEND_APPROVAL_THRESHOLD)}."
            ),
            required_role=None,
        )
    return AuthorityDecision(
        can_post=True,
        requires_approver=False,
        code="within_authority",
        reason=(
            f"Action {action} is within analyst authority. "
            f"proposed_limit={money(proposed_limit)} does not increase "
            f"credit_limit={money(current_limit)}."
        ),
        required_role=None,
    )
