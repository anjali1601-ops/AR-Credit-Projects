"""Deterministic financial analytics.

Nothing in this subpackage imports an LLM provider. Every number the memo can
cite is produced here, from submitted statement lines, by pure functions.
"""

from .engine import analyse_financials
from .ratios import compute_ratios
from .rating import assign_rating, recommend_limit
from .spreads import register_statement_evidence, spread_statements
from .trends import compute_trends

__all__ = [
    "analyse_financials",
    "assign_rating",
    "compute_ratios",
    "compute_trends",
    "recommend_limit",
    "register_statement_evidence",
    "spread_statements",
]
