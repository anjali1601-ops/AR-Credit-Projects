"""JSON payload. Money and DSO days are strings so the wire format stays exact."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from o2c_control_tower.format import format_percent, quantize_ratio
from o2c_control_tower.summary import ControlTower


def _money(amount: Decimal) -> str:
    return f"{amount.quantize(Decimal('0.01')):.2f}"


def _days(value: Fraction) -> str:
    return f"{quantize_ratio(value):.4f}"


def _running(tower: ControlTower) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = [
        {
            "key": "beginning",
            "label": "Beginning DSO",
            "days": _days(tower.dso.beginning_dso),
            "running": _days(tower.dso.beginning_dso),
        }
    ]
    running = quantize_ratio(tower.dso.beginning_dso)
    for key, label, effect in tower.dso.effects():
        running += quantize_ratio(effect)
        rows.append(
            {
                "key": key,
                "label": label,
                "days": _days(effect),
                "running": f"{running:.4f}",
            }
        )
    rows.append(
        {
            "key": "ending",
            "label": "Ending DSO",
            "days": _days(tower.dso.ending_dso),
            "running": _days(tower.dso.ending_dso),
        }
    )
    return rows


def control_tower_payload(tower: ControlTower) -> dict:
    total = tower.aging.total
    return {
        "as_of": tower.as_of.isoformat(),
        "currency": "USD",
        "narrative": tower.narrative,
        "kpis": {
            "open_ar": _money(tower.open_ar),
            "beginning_dso_days": _days(tower.dso.beginning_dso),
            "ending_dso_days": _days(tower.dso.ending_dso),
            "dso_change_days": f"{(quantize_ratio(tower.dso.ending_dso) - quantize_ratio(tower.dso.beginning_dso)):.4f}",
            "predicted_cash_14d": _money(tower.cash.total),
            "actions_waiting": len(tower.actions),
            "unapplied_cash": _money(tower.unapplied_cash),
        },
        "aging": {
            "total": _money(total),
            "buckets": [
                {
                    "key": bucket.key,
                    "label": bucket.label,
                    "amount": _money(bucket.amount),
                    "invoice_count": bucket.invoice_count,
                    "share": format_percent(bucket.amount / total) if total else "0.0%",
                }
                for bucket in tower.aging.buckets
            ],
            "invoices": [
                {
                    "invoice_id": row.invoice_id,
                    "customer": row.customer_name,
                    "collector": row.collector,
                    "issued_on": row.issued_on,
                    "due_on": row.due_on,
                    "original_amount": _money(row.amount),
                    "open_amount": _money(row.open_amount),
                    "days_past_due": row.days_past_due,
                    "bucket": row.bucket,
                }
                for row in tower.aging.invoices
            ],
        },
        "dso_bridge": {
            "days_in_period": tower.dso.days,
            "formula": "DSO = open AR × 30 / gross billings in the 30-day window",
            "current_period": {
                "start": tower.dso.current.start.isoformat(),
                "end": tower.dso.current.end.isoformat(),
            },
            "prior_period": {
                "start": tower.dso.prior.start.isoformat(),
                "end": tower.dso.prior.end.isoformat(),
            },
            "inputs": {
                "beginning_ar": _money(tower.dso.beginning_ar),
                "ending_ar": _money(tower.dso.ending_ar),
                "prior_sales": _money(tower.dso.prior_sales),
                "current_sales": _money(tower.dso.current_sales),
                "billings": _money(tower.dso.billings),
                "collections": _money(tower.dso.collections),
                "credit_memos": _money(tower.dso.credit_memos),
                "other": _money(tower.dso.other),
            },
            "components": _running(tower),
            "ties": tower.dso.ties(),
        },
        "cash_forecast": {
            "window_start": tower.cash.start.isoformat(),
            "window_end": tower.cash.end.isoformat(),
            "total": _money(tower.cash.total),
            "days": [
                {"date": day.day.isoformat(), "amount": _money(day.amount)}
                for day in tower.cash.days
            ],
            "lines": [
                {
                    "date": line.expected_on.isoformat(),
                    "invoice_id": line.invoice_id,
                    "customer": line.customer_name,
                    "source": line.source,
                    "amount": _money(line.amount),
                    "detail": line.detail,
                }
                for line in tower.cash.lines
            ],
        },
        "collectors": [
            {
                "collector": load.collector,
                "customers": list(load.customers),
                "open_invoices": load.open_invoices,
                "open_ar": _money(load.open_ar),
                "past_due_invoices": load.past_due_invoices,
                "past_due_ar": _money(load.past_due_ar),
                "broken_promises": load.broken_promises,
                "waiting_actions": load.waiting_actions,
                "workload_score": load.workload_score,
            }
            for load in tower.collectors
        ],
        "actions": [
            {
                "id": action.id,
                "type": action.action_type,
                "label": action.label,
                "customer": action.customer_name,
                "invoice_id": action.invoice_id,
                "amount": _money(action.amount),
                "owner": action.owner,
                "created_on": action.created_on,
                "age_days": action.age_days,
                "reason": action.reason,
            }
            for action in tower.actions
        ],
    }
