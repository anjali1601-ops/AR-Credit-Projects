"""Exception agent.

Clean full applies wait in the ready queue. Everything else — short-pay,
overpay, unidentified cash, a missing invoice number — goes to the clerk
with the ranked candidates and the actions that are safe to click.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cash_application.matcher import MatchResult

APPLY = "apply"
SPLIT = "split"
LEAVE_UNAPPLIED = "leave_unapplied"

READY = "ready"
EXCEPTION = "exception"

_ALL_ACTIONS = (APPLY, SPLIT, LEAVE_UNAPPLIED)
_UNAPPLIED_ACTIONS = (SPLIT, LEAVE_UNAPPLIED)


@dataclass(frozen=True)
class RouteDecision:
    queue: str
    reason: str
    allowed_actions: tuple[str, ...]
    recommended_action: str


def route(match: MatchResult) -> RouteDecision:
    """Decide where a proposal sits and which one-click actions are valid."""
    if match.kind == "unapplied" or not match.lines:
        return RouteDecision(
            queue=EXCEPTION,
            reason=(
                "No open invoice could be tied to this remittance by invoice "
                "number, customer, amount, and reference. Leave the cash "
                "unapplied, or split it onto invoices you choose."
            ),
            allowed_actions=_UNAPPLIED_ACTIONS,
            recommended_action=LEAVE_UNAPPLIED,
        )
    if match.kind == "short_pay":
        return RouteDecision(
            queue=EXCEPTION,
            reason=(
                "The remittance is short of the open balance on the matched "
                "invoice. Confirm the deduction, split the cash differently, "
                "or leave it unapplied."
            ),
            allowed_actions=_ALL_ACTIONS,
            recommended_action=APPLY,
        )
    if match.kind == "overpay":
        return RouteDecision(
            queue=EXCEPTION,
            reason=(
                "The remittance is more than the open balance. Apply the "
                "open amount and leave the remainder unapplied, or split "
                "the extra onto other invoices."
            ),
            allowed_actions=_ALL_ACTIONS,
            recommended_action=APPLY,
        )
    if match.customer_mismatch or len({line.customer_account for line in match.lines}) > 1:
        return RouteDecision(
            queue=EXCEPTION,
            reason=(
                "The payer and the invoice customer do not agree. Nothing "
                "posts until a clerk picks the application."
            ),
            allowed_actions=_ALL_ACTIONS,
            recommended_action=APPLY,
        )
    if match.invoices_cited and match.confidence >= Decimal("0.90"):
        return RouteDecision(
            queue=READY,
            reason=(
                "Invoice number, customer, and amount all tie. Ready for "
                "a clerk to confirm. Nothing posts until they do."
            ),
            allowed_actions=_ALL_ACTIONS,
            recommended_action=APPLY,
        )
    return RouteDecision(
        queue=EXCEPTION,
        reason=(
            "The amount and the customer tie, but the remittance did not "
            "cite the invoice number. Confirm the candidate before posting."
        ),
        allowed_actions=_ALL_ACTIONS,
        recommended_action=APPLY,
    )


def signature(match: MatchResult, decision: RouteDecision) -> tuple:
    """Outcome fingerprint. The five seeded types must not collide."""
    return (
        match.kind,
        len(match.lines),
        decision.queue,
        match.unapplied_cash > 0,
        match.short_fall > 0,
    )
