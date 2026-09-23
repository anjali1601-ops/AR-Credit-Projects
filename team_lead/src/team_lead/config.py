"""Runtime settings. The narrative model is offline unless an env var says otherwise."""

from __future__ import annotations

import os
from pathlib import Path

PERIOD = "Q3 2026"
PORT = 47261
MANAGER_NAME = "Alex Okonkwo"

# Full-quarter book for a tenured cash/collections seat.
CASH_TARGET = 1_800_000
WORKLOAD_EXPECTATION = 140

# Tenure under this many months is measured on the ramp bar.
RAMP_TENURE_MONTHS = 6
RAMP_CASH_FACTOR = 0.65

CASH_EXCEEDS_RATIO = 1.10
CASH_MEETS_RATIO = 0.90

PROMISE_EXCEEDS = 0.92
PROMISE_MEETS = 0.80

CYCLE_EXCEEDS_DAYS = 8.0
CYCLE_MEETS_DAYS = 14.0
RAMP_CYCLE_MEETS_DAYS = 18.0

QUALITY_EXCEEDS = 95.0
QUALITY_MEETS = 85.0
RAMP_QUALITY_MEETS = 80.0

# Sustained overtime: this many hours in at least this many weeks of the quarter.
OT_HOURS_THRESHOLD = 10.0
OT_WEEKS_REQUIRED = 4

# Flight risk also requires enough tenure to be a stay conversation, not a ramp check-in.
TENURE_MONTHS_MIN = 12

CORE_METRICS = ("cash_applied", "promises_kept", "dispute_cycle", "quality")


def db_path() -> Path:
    override = os.getenv("TEAM_LEAD_DB")
    if override:
        return Path(override)
    return Path.cwd() / "data" / "cockpit.db"


def llm_provider_name() -> str:
    return os.getenv("TEAM_LEAD_LLM_PROVIDER", "offline").strip().lower() or "offline"
