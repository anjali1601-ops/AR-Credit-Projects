"""Corpus, embedding, and retrieval.

The risk searcher's findings are only as good as what retrieval returns, so the
tests here cover scoping (an applicant must not see another applicant's adverse
news), ranking, and the equivalence of the two index backends.
"""

from __future__ import annotations

import os

import pytest

from credit_underwriter.retrieval import (
    build_index,
    corpus_hash,
    document_scope,
    live_search_enabled,
)
from credit_underwriter.retrieval.corpus import (
    UNSCOPED,
    application_scopes,
    document_from_metadata,
    document_metadata,
)
from credit_underwriter.retrieval.embedding import (
    DEFAULT_DIM,
    LocalEmbeddingFunction,
    cosine_similarity,
    embed,
    tokenize,
)
from credit_underwriter.retrieval.live_search import LiveSearchUnavailable, search_adverse_media

BACKENDS = ("in_memory", "chroma")


# --------------------------------------------------------------------------------------
# Corpus integrity
# --------------------------------------------------------------------------------------


def test_corpus_loads_and_covers_every_document_type(corpus):
    assert len(corpus) >= 18
    types = {d.doc_type for d in corpus}
    assert {"news", "filing", "litigation", "industry_report", "country_report"} <= types


def test_document_ids_are_unique(corpus):
    ids = [d.doc_id for d in corpus]
    assert len(ids) == len(set(ids))


def test_every_document_declares_a_source_and_a_dated_publication(corpus):
    for document in corpus:
        assert document.source.strip(), f"{document.doc_id} has no source"
        assert len(document.published_date) == 10, f"{document.doc_id} has no ISO date"


def test_synthetic_corpus_is_labelled_as_synthetic(corpus):
    """Nothing here is real. A memo must never imply otherwise."""
    for document in corpus:
        assert "synthetic" in document.source.lower(), f"{document.doc_id} is not labelled"


def test_corpus_hash_is_stable_and_order_independent(corpus):
    assert corpus_hash(corpus) == corpus_hash(corpus)
    assert corpus_hash(corpus) == corpus_hash(list(reversed(corpus)))


def test_corpus_hash_changes_when_a_document_changes(corpus):
    mutated = [d.model_copy(deep=True) for d in corpus]
    mutated[0] = mutated[0].model_copy(update={"text": mutated[0].text + " Amended."})
    assert corpus_hash(mutated) != corpus_hash(corpus)


def test_every_applicant_has_corpus_coverage(corpus, applications):
    """An applicant with no in-scope documents would silently get no risk review."""
    for applicant_id in applications:
        assert [d for d in corpus if d.applicant_id == applicant_id], (
            f"no documents scoped to {applicant_id}"
        )


def test_metadata_round_trips_through_the_index_representation(corpus):
    for document in corpus:
        restored = document_from_metadata(
            document.doc_id, document.text, document_metadata(document)
        )
        assert restored.model_dump() == document.model_dump()


# --------------------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------------------


def test_document_scope_prefers_the_obligor_then_jurisdiction_then_sector(corpus):
    for document in corpus:
        scope = document_scope(document)
        if document.applicant_id:
            assert scope == f"applicant:{document.applicant_id}"
        elif document.doc_type == "country_report" and document.country_code:
            assert scope == f"country:{document.country_code}"
        elif document.industry_code:
            assert scope == f"industry:{document.industry_code}"
        else:
            assert scope == UNSCOPED


def test_application_scopes_cover_the_obligor_its_country_and_its_sector(atlas):
    scopes = application_scopes(atlas)
    assert f"applicant:{atlas.applicant_id}" in scopes
    assert f"country:{atlas.country_code}" in scopes
    assert f"industry:{atlas.industry_code}" in scopes


def test_every_document_scope_is_reachable_by_some_applicant(corpus, applications):
    """A document nobody can retrieve is dead weight in the corpus."""
    reachable = {s for a in applications.values() for s in application_scopes(a)}
    for document in corpus:
        assert document_scope(document) in reachable, f"{document.doc_id} is unreachable"


# --------------------------------------------------------------------------------------
# Embedding
# --------------------------------------------------------------------------------------


def test_embedding_is_deterministic():
    text = "covenant waiver granted by the lender"
    assert embed(text) == embed(text)


def test_embedding_has_the_requested_dimension():
    assert len(embed("anything", 64)) == 64
    assert len(embed("anything")) == DEFAULT_DIM


def test_embedding_is_unit_length_and_empty_text_is_the_zero_vector():
    vector = embed("winding-up petition filed against the obligor")
    assert sum(x * x for x in vector) ** 0.5 == pytest.approx(1.0, abs=1e-9)
    assert all(x == 0.0 for x in embed(""))


def test_related_text_scores_above_unrelated_text():
    query = embed("covenant breach and waiver from the lender")
    related = embed("the borrower breached its fixed-charge covenant and obtained a waiver")
    unrelated = embed("the applicant renewed its suburban office lease")
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


def test_cosine_similarity_of_a_vector_with_itself_is_one():
    vector = embed("insolvency proceedings")
    assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-9)


def test_tokenizer_drops_stopwords_and_casing():
    tokens = tokenize("The Company and the Lender")
    assert "the" not in tokens and "and" not in tokens
    assert "company" in tokens and "lender" in tokens


def test_embedding_function_satisfies_both_chroma_api_generations():
    """Chroma has asked for __call__, embed_documents, and embed_query over time."""
    fn = LocalEmbeddingFunction(dim=32)
    texts = ["covenant waiver", "winding-up petition"]
    assert fn(texts) == fn.embed_documents(texts)
    assert fn.embed_query(texts) == fn(texts)
    assert all(len(v) == 32 for v in fn(texts))
    assert isinstance(fn.name(), str)


# --------------------------------------------------------------------------------------
# Index behaviour, across both backends
# --------------------------------------------------------------------------------------


@pytest.fixture(params=BACKENDS)
def index(request, corpus, settings):
    return build_index(
        corpus, backend=request.param, dim=settings.retrieval_embedding_dim
    )


def test_index_holds_the_whole_corpus(index, corpus):
    assert index.count() == len(corpus)


def test_search_returns_results_ranked_by_score(index, northwind):
    results = index.search("covenant breach waiver", 5, application_scopes(northwind))
    assert results
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)


def test_search_respects_k(index, northwind):
    results = index.search("litigation against the company", 3, application_scopes(northwind))
    assert len(results) <= 3


def test_search_never_leaks_another_applicants_documents(index, applications):
    """Scoping is the guard against citing the wrong company's adverse news."""
    owners = set(applications)
    for applicant_id, application in applications.items():
        results = index.search(
            "litigation insolvency default covenant fraud",
            20,
            application_scopes(application),
        )
        for document, _ in results:
            owner = document.applicant_id
            if owner and owner in owners:
                assert owner == applicant_id, (
                    f"{applicant_id} retrieved a document scoped to {owner}"
                )


def test_the_relevant_adverse_document_ranks_for_each_applicant(index, northwind, veritas):
    covenant = index.search("covenant breach waiver", 5, application_scopes(northwind))
    assert any("covenant" in d.text.lower() for d, _ in covenant)

    insolvency = index.search(
        "winding-up petition insolvency proceedings", 10, application_scopes(veritas)
    )
    assert any(d.doc_type == "litigation" for d, _ in insolvency)


def test_an_out_of_scope_query_returns_nothing_rather_than_a_wrong_answer(index):
    assert index.search("covenant breach", 5, ["applicant:no-such-company"]) == []


def test_an_unrelated_query_returns_nothing_rather_than_the_least_bad_match(index, atlas):
    """Zero-similarity hits must be dropped, not returned as weak evidence."""
    results = index.search("xyzzy plugh frobnicate", 5, application_scopes(atlas))
    assert all(score > 0 for _, score in results)


def test_both_backends_agree_on_the_documents_they_return(corpus, settings, applications):
    """The suite runs on the in-memory index, so it must agree with Chroma."""
    for application in applications.values():
        scopes = application_scopes(application)
        returned = {}
        for backend in BACKENDS:
            built = build_index(corpus, backend=backend, dim=settings.retrieval_embedding_dim)
            returned[backend] = {
                d.doc_id for d, _ in built.search("litigation covenant default", 5, scopes)
            }
        assert returned["in_memory"] == returned["chroma"], application.applicant_id


def test_build_index_falls_back_to_memory_when_chroma_cannot_start(
    corpus, monkeypatch, settings
):
    """A broken optional dependency must degrade, not fail the underwriting."""
    import credit_underwriter.retrieval.index as index_module

    def explode(*args, **kwargs):
        raise RuntimeError("chroma is unavailable in this environment")

    monkeypatch.setattr(index_module, "ChromaCorpusIndex", explode)
    built = index_module.build_index(
        corpus, backend="chroma", dim=settings.retrieval_embedding_dim
    )
    assert built.backend == "in_memory"
    assert built.count() == len(corpus)


def test_an_unknown_backend_is_rejected_rather_than_guessed(corpus):
    with pytest.raises(ValueError, match="unknown retrieval backend"):
        build_index(corpus, backend="pinecone")


# --------------------------------------------------------------------------------------
# Live search stays off
# --------------------------------------------------------------------------------------


def test_live_search_is_disabled_by_default(settings):
    assert settings.enable_live_search is False


def test_live_search_stays_off_when_the_flag_is_unset(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "not-a-real-key")
    assert live_search_enabled(False) is False


def test_live_search_stays_off_without_a_key_even_when_flagged_on(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert live_search_enabled(True) is False


def test_live_search_refuses_to_run_without_a_key(monkeypatch, atlas):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(LiveSearchUnavailable, match="TAVILY_API_KEY"):
        search_adverse_media(atlas.legal_name, ["adverse media"])


def test_no_test_in_this_suite_depends_on_a_live_search_key():
    """The suite must pass on a machine with no credentials at all."""
    assert os.environ.get("CREDIT_UNDERWRITER_ENABLE_LIVE_SEARCH") in (None, "", "0", "false")
