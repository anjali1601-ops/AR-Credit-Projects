"""Shared fixtures.

The graph context is session-scoped because building it constructs the vector
index; rebuilding it per test would dominate the run time. Tests that need to
write runs get a temporary runs directory so they never touch the project's own.
"""

from __future__ import annotations

import pytest

from credit_underwriter.agents.context import GraphContext
from credit_underwriter.applicants import get_application, list_applications
from credit_underwriter.config import Settings, ensure_dirs
from credit_underwriter.evidence import EvidenceRegistry
from credit_underwriter.finance import analyse_financials
from credit_underwriter.retrieval import load_corpus

APPLICANT_IDS = ("atlas-precision-works", "northwind-logistics", "veritas-metal-trading")


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Default settings: offline provider, in-memory index.

    The in-memory index keeps the suite free of Chroma's on-disk state. A separate
    test asserts the two backends agree, which is what makes this substitution safe.
    """
    return Settings.from_env(retrieval_backend="in_memory")


@pytest.fixture()
def tmp_settings(settings: Settings, tmp_path) -> Settings:
    scoped = settings.with_overrides(runs_dir=tmp_path / "runs", memo_dir=tmp_path / "memos")
    ensure_dirs(scoped)
    return scoped


@pytest.fixture(scope="session")
def corpus(settings: Settings):
    return load_corpus(settings.corpus_path)


@pytest.fixture(scope="session")
def context(settings: Settings) -> GraphContext:
    return GraphContext.build(settings)


@pytest.fixture(scope="session")
def applications(settings: Settings):
    return {a.applicant_id: a for a in list_applications(settings)}


@pytest.fixture(scope="session")
def atlas(settings: Settings):
    return get_application("atlas-precision-works", settings)


@pytest.fixture(scope="session")
def northwind(settings: Settings):
    return get_application("northwind-logistics", settings)


@pytest.fixture(scope="session")
def veritas(settings: Settings):
    return get_application("veritas-metal-trading", settings)


@pytest.fixture(scope="session")
def analyses(applications):
    """The deterministic financial analysis for every applicant."""
    return {
        applicant_id: analyse_financials(application, EvidenceRegistry())
        for applicant_id, application in applications.items()
    }


@pytest.fixture(scope="session")
def runs(applications, settings: Settings, context: GraphContext, tmp_path_factory):
    """One completed underwriting per applicant, shared across the suite."""
    from credit_underwriter.service import underwrite

    scoped = settings.with_overrides(runs_dir=tmp_path_factory.mktemp("runs"))
    return {
        applicant_id: underwrite(application, settings=scoped, context=context)
        for applicant_id, application in applications.items()
    }
