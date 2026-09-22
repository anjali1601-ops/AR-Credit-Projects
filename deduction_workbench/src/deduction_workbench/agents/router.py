"""Route a decided deduction. Nothing in this module sends mail or posts a credit.

Valid pricing promotions go to sales. A supported shortage goes to the
warehouse. An invalid claim goes to recovery with the clause the policy agent
cited. Returns, co-op advertising, and damaged goods each have their own desk
so the five reason codes do not collapse onto one queue.
"""

from __future__ import annotations

from deduction_workbench.models import PolicyResult, RouteDecision

# queue, label, organization, desk
_VALID = {
    "shortage": ("warehouse", "Warehouse", "logistics", "warehouse"),
    "pricing": ("sales", "Sales", "sales", "pricing"),
    "returns": ("returns", "Returns", "customer_operations", "returns"),
    "coop_advertising": ("sales_coop", "Sales co-op", "sales", "co-op advertising"),
    "damaged_goods": ("quality", "Quality", "quality", "damaged goods"),
}

_RECOVERY = ("recovery", "Recovery", "recovery", "deduction recovery")

QUEUES = {
    "warehouse": _VALID["shortage"],
    "sales": _VALID["pricing"],
    "returns": _VALID["returns"],
    "sales_coop": _VALID["coop_advertising"],
    "quality": _VALID["damaged_goods"],
    "recovery": _RECOVERY,
}


def decide_route(reason_code: str, policy: PolicyResult) -> RouteDecision:
    if not policy.valid:
        queue, label, organization, desk = _RECOVERY
        rationale = (
            f"Invalid {reason_code.replace('_', ' ')} claim. Route to recovery and cite "
            f"{policy.citation_id}. Do not send a reply or post a credit."
        )
    else:
        if reason_code not in _VALID:
            queue, label, organization, desk = _RECOVERY
            rationale = (
                f"No desk is defined for {reason_code}. Route to recovery and cite "
                f"{policy.citation_id}. Do not send a reply."
            )
        else:
            queue, label, organization, desk = _VALID[reason_code]
            rationale = _valid_rationale(reason_code, policy.citation_id)
    return RouteDecision(
        queue=queue,
        queue_label=label,
        organization=organization,
        desk=desk,
        rationale=rationale,
        auto_send=False,
    )


def _valid_rationale(reason_code: str, citation_id: str) -> str:
    if reason_code == "pricing":
        lead = "Valid promotion. Route to sales."
    elif reason_code == "coop_advertising":
        lead = "Valid co-op advertising claim. Route to the sales co-op desk."
    elif reason_code == "shortage":
        lead = "Supported shortage. Route to the warehouse."
    elif reason_code == "returns":
        lead = "Authorized return. Route to returns."
    elif reason_code == "damaged_goods":
        lead = "Supported damage claim. Route to quality."
    else:
        lead = "Supported claim."
    return f"{lead} Governing clause: {citation_id}. Do not send a reply or post a credit."


def describe_queue(queue: str) -> tuple[str, str, str]:
    """Return label, organization, and desk for a queue a clerk picked."""
    if queue not in QUEUES:
        raise ValueError(queue)
    _queue, label, organization, desk = QUEUES[queue]
    return label, organization, desk
