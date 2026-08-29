from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from mandateguard.intelligence.buyer import parse_offline_intent
from mandateguard.intelligence.models import (
    PurchaseProposal,
    RetrievalDocument,
    RetrievalSource,
)
from mandateguard.intelligence.orchestration import build_mandate_from_intent
from mandateguard.intelligence.retrieval import (
    DEFAULT_EMBEDDING_MODEL,
    HybridRetriever,
    MappingEmbeddingProvider,
    OpenAIEmbeddingProvider,
    RetrievalMode,
    build_retrieval_query,
    lexical_scores,
)
from mandateguard.intelligence.experiment import (
    RetrievalExperimentCase,
    RetrievalExperimentHarness,
)
from tests.intelligence_factories import ALLOW_INTENT, NOW, make_store


def _documents() -> tuple[RetrievalDocument, ...]:
    return (
        RetrievalDocument(
            document_id="doc.apple",
            source_type=RetrievalSource.DECISION_MEMORY,
            text="apple fruit",
        ),
        RetrievalDocument(
            document_id="doc.banana",
            source_type=RetrievalSource.DECISION_MEMORY,
            text="banana fruit",
        ),
    )


def _retriever() -> HybridRetriever:
    return HybridRetriever(
        MappingEmbeddingProvider(
            {
                "apple": (1.0, 0.0),
                "apple fruit": (0.0, 1.0),
                "banana fruit": (1.0, 0.0),
            }
        )
    )


def test_lexical_ranking_prefers_matching_document():
    scores = lexical_scores("apple", _documents())
    assert scores["doc.apple"] == 1.0
    assert scores["doc.banana"] == 0.0


def test_semantic_fake_vectors_control_ranking():
    result = _retriever().retrieve(
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
        alpha=0.0,
        top_k=2,
    )
    assert [item.document.document_id for item in result.ranked_documents] == [
        "doc.banana",
        "doc.apple",
    ]
    assert result.ranked_documents[0].score.semantic_score == pytest.approx(1.0)


def test_hybrid_equation_is_transparent():
    result = _retriever().retrieve(
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
        alpha=0.4,
        top_k=2,
    )
    by_id = {item.document.document_id: item.score for item in result.ranked_documents}
    assert by_id["doc.apple"].hybrid_score == pytest.approx(0.4)
    assert by_id["doc.banana"].hybrid_score == pytest.approx(0.6)


def test_alpha_boundaries_select_lexical_or_semantic_signal():
    lexical = _retriever().retrieve(
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
        alpha=1.0,
        top_k=2,
    )
    semantic = _retriever().retrieve(
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
        alpha=0.0,
        top_k=2,
    )
    assert lexical.ranked_documents[0].document.document_id == "doc.apple"
    assert semantic.ranked_documents[0].document.document_id == "doc.banana"
    with pytest.raises(ValueError, match="alpha"):
        _retriever().retrieve(
            query="apple",
            query_sha256="0" * 64,
            documents=_documents(),
            alpha=1.1,
        )


def test_top_k_is_enforced():
    result = _retriever().retrieve(
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
        alpha=0.4,
        top_k=1,
    )
    assert len(result.ranked_documents) == 1


def test_stable_tie_break_uses_document_id():
    documents = tuple(reversed(_documents()))
    result = HybridRetriever(None).retrieve(
        query="unmatched",
        query_sha256="1" * 64,
        documents=documents,
        top_k=2,
        mode=RetrievalMode.LEXICAL,
    )
    assert [item.document.document_id for item in result.ranked_documents] == [
        "doc.apple",
        "doc.banana",
    ]


def test_lexical_mode_makes_no_embedding_call():
    result = HybridRetriever(None).retrieve(
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
        mode=RetrievalMode.LEXICAL,
    )
    assert result.embedding_latency_ms == 0.0
    assert all(item.score.semantic_score == 0.0 for item in result.ranked_documents)


def test_query_construction_is_stable_and_binds_candidate_context():
    store = make_store()
    interpreted = parse_offline_intent(ALLOW_INTENT)
    mandate = build_mandate_from_intent(
        user_intent=ALLOW_INTENT,
        interpreted=interpreted,
        evaluated_at=NOW,
    )
    product = store.get_product(
        merchant_id="merchant-scholarly", sku="studyglow-desk-lamp"
    )
    proposal = PurchaseProposal(
        merchant_id=product.merchant_id,
        sku=product.sku,
        quantity=1,
        declared_total_minor=product.effective_unit_price_minor,
        currency="INR",
        reason="reason is not trusted evidence",
        selected_evidence_ids=product.evidence_ids,
    )
    first = build_retrieval_query(
        user_intent=ALLOW_INTENT,
        mandate=mandate,
        proposal=proposal,
        product=product,
    )
    second = build_retrieval_query(
        user_intent=ALLOW_INTENT,
        mandate=mandate,
        proposal=proposal,
        product=product,
    )
    changed = build_retrieval_query(
        user_intent=ALLOW_INTENT,
        mandate=mandate,
        proposal=replace(proposal, quantity=2),
        product=product,
    )
    assert first == second
    assert first[1] == sha256(first[0].encode("utf-8")).hexdigest()
    assert changed[1] != first[1]
    assert "trusted_product_context" in first[0]


def test_openai_embedding_adapter_uses_configured_model_and_usage():
    class FakeEmbeddings:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=0, embedding=[1.0, 0.0]),
                    SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                ],
                usage=SimpleNamespace(prompt_tokens=9),
            )

    embeddings = FakeEmbeddings()
    provider = OpenAIEmbeddingProvider(
        client=SimpleNamespace(embeddings=embeddings)
    )
    batch = provider.embed(("first", "second"))
    assert provider.model_id == DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"
    assert embeddings.calls == [
        {"model": "text-embedding-3-small", "input": ["first", "second"]}
    ]
    assert batch.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert batch.input_tokens == 9


def test_experiment_harness_exposes_three_unrun_comparison_variants():
    case = RetrievalExperimentCase(
        case_id="case-1",
        query="apple",
        query_sha256="0" * 64,
        documents=_documents(),
    )
    observations = RetrievalExperimentHarness(_retriever()).run((case,), top_k=1)
    assert [item.variant.value for item in observations] == [
        "no_retrieval",
        "lexical_only",
        "hybrid",
    ]
    assert all(item.case_id == "case-1" for item in observations)
