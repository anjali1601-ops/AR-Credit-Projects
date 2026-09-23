from __future__ import annotations

import pytest

from team_lead.case import run_all
from team_lead.store import Store


@pytest.fixture()
def store(tmp_path) -> Store:
    database = Store(tmp_path / "cockpit.db")
    database.reset()
    return database


@pytest.fixture()
def cases(store):
    return run_all(store)
