"""Optional live web search, off by default.

Enable with ``CREDIT_UNDERWRITER_ENABLE_LIVE_SEARCH=1`` and a ``TAVILY_API_KEY``.
Results are wrapped as :class:`RiskDocument` instances and appended to the local
corpus for the duration of a single run, so they flow through exactly the same
classification, citation, and completeness checks as seeded documents.

When the flag is off -- the default -- nothing here touches the network, which is
what keeps the demo reproducible.
"""

from __future__ import annotations

import os
from datetime import date

from ..models import RiskDocument

TAVILY_ENDPOINT = "https://api.tavily.com/search"
LIVE_DOC_PREFIX = "live"


class LiveSearchUnavailable(RuntimeError):
    """Raised when live search is requested but cannot run."""


def live_search_enabled(enabled: bool) -> bool:
    return bool(enabled) and bool(os.environ.get("TAVILY_API_KEY"))


def search_adverse_media(
    legal_name: str,
    queries: list[str],
    max_results: int = 3,
    timeout_seconds: float = 12.0,
) -> list[RiskDocument]:
    """Query the configured live search provider and wrap the hits as documents."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise LiveSearchUnavailable(
            "TAVILY_API_KEY is not set; unset CREDIT_UNDERWRITER_ENABLE_LIVE_SEARCH to run "
            "against the local corpus only"
        )

    import httpx

    documents: list[RiskDocument] = []
    today = date.today().isoformat()
    with httpx.Client(timeout=timeout_seconds) as client:
        for index, query in enumerate(queries):
            response = client.post(
                TAVILY_ENDPOINT,
                json={
                    "api_key": api_key,
                    "query": f"{legal_name} {query}",
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            for rank, hit in enumerate(response.json().get("results", [])):
                content = str(hit.get("content", "")).strip()
                if not content:
                    continue
                documents.append(
                    RiskDocument(
                        doc_id=f"{LIVE_DOC_PREFIX}-{index}-{rank}",
                        title=str(hit.get("title", query))[:180],
                        source=f"Live search — {hit.get('url', 'unknown source')}",
                        doc_type="news",
                        published_date=str(hit.get("published_date") or today),
                        text=content,
                    )
                )
    return documents
