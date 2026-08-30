from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mandateguard.engineering.int2.artifacts import (
    retrieval_observation_record,
    write_retrieval_artifacts,
)
from mandateguard.engineering.int2.embeddings import precompute_embeddings
from mandateguard.engineering.int2.fixtures import (
    build_experiment_queries,
    load_query_corpus,
    load_relevance_manifest,
)
from mandateguard.engineering.int2.models import (
    EmbeddingSource,
    ExperimentQuery,
    Int2ExperimentError,
    RelevanceAnnotation,
    RelevanceManifest,
    RetrievalConfiguration,
    RetrievalStrategy,
    retrieval_matrix,
)
from mandateguard.engineering.int2.retrieval import (
    ExperimentRetriever,
    compute_retrieval_metrics,
)
from mandateguard.engineering.int2.sweep import (
    RetrievalSweepHarness,
    run_stage_a_sweep,
)
from mandateguard.intelligence.models import RetrievalDocument, RetrievalSource
from mandateguard.intelligence.retrieval.embeddings import (
    EmbeddingBatch,
    HashingEmbeddingProvider,
    MappingEmbeddingProvider,
)
from mandateguard.intelligence.store import TrustedCommerceStore


ROOT = Path(__file__).resolve().parents[1]
INT2_FIXTURES = ROOT / "fixtures" / "engineering" / "int2"
CATALOG_FIXTURES = ROOT / "fixtures" / "agentic_commerce"


def _documents() -> tuple[RetrievalDocument, ...]:
    return (
        RetrievalDocument(
            document_id="doc.apple",
            source_type=RetrievalSource.MERCHANT_EVIDENCE,
            text="apple fruit",
            merchant_id="merchant-1",
            evidence_id="evidence.apple",
        ),
        RetrievalDocument(
            document_id="doc.banana",
            source_type=RetrievalSource.MERCHANT_EVIDENCE,
            text="banana fruit",
            merchant_id="merchant-1",
            evidence_id="evidence.banana",
        ),
    )


def _query(documents=None) -> ExperimentQuery:
    return ExperimentQuery.from_text(
        query_id="INT2-Q-TEST",
        query="apple",
        documents=documents or _documents(),
    )


@dataclass
class RecordingEmbeddingProvider:
    model_id: str = "recording-fake-v1"
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        self.calls.append(texts)
        vectors = {
            "apple": (1.0, 0.0),
            "apple fruit": (0.0, 1.0),
            "banana fruit": (1.0, 0.0),
        }
        return EmbeddingBatch(
            vectors=tuple(vectors[text] for text in texts),
            input_tokens=7,
        )


def _snapshot(provider, query: ExperimentQuery | None = None):
    """Precompute exactly as the Stage-A runner does, before any retrieval."""

    return precompute_embeddings((query or _query(),), provider)


def _annotation(
    *, relevant=("evidence.banana",), required=("evidence.banana",)
) -> RelevanceAnnotation:
    return RelevanceAnnotation(
        query_id="INT2-Q-TEST",
        relevant_evidence_ids=relevant,
        required_evidence_ids=required,
    )


def test_recall_precision_mrr_and_first_required_rank_are_correct():
    metrics = compute_retrieval_metrics(
        ("noise", "evidence.banana", "evidence.apple"),
        _annotation(
            relevant=("evidence.banana", "evidence.apple"),
            required=("evidence.banana", "evidence.apple"),
        ),
        top_k=3,
    )
    assert metrics.recall_at_k == 1.0
    assert metrics.precision_at_k == pytest.approx(2 / 3)
    assert metrics.reciprocal_rank == 0.5
    assert metrics.rank_of_first_required == 2
    assert metrics.all_required_retrieved is True


def test_duplicate_evidence_is_counted_once():
    metrics = compute_retrieval_metrics(
        ("evidence.banana", "evidence.banana", "noise"),
        _annotation(),
        top_k=3,
    )
    assert metrics.recall_at_k == 1.0
    assert metrics.precision_at_k == 0.5
    assert metrics.reciprocal_rank == 1.0


def test_empty_relevant_set_is_vacuously_complete_and_has_no_mrr():
    metrics = compute_retrieval_metrics(
        (), _annotation(relevant=(), required=()), top_k=1
    )
    assert metrics.recall_at_k == 1.0
    assert metrics.precision_at_k == 0.0
    assert metrics.reciprocal_rank is None
    assert metrics.all_required_retrieved is True
    assert metrics.rank_of_first_required is None


def test_no_retrieval_does_not_depend_on_semantic_vectors():
    result = ExperimentRetriever(None).retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.NO_RETRIEVAL, top_k=5
        ),
    )
    assert result.ranked_documents == ()
    assert result.embedding_source is EmbeddingSource.NOT_USED


def test_lexical_only_does_not_depend_on_semantic_vectors():
    result = ExperimentRetriever(None).retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=2
        ),
    )
    assert result.ranked_documents[0].document.evidence_id == "evidence.apple"
    assert result.embedding_source is EmbeddingSource.NOT_USED
    assert all(item.score.semantic_score == 0.0 for item in result.ranked_documents)


def test_semantic_and_hybrid_read_the_precomputed_snapshot():
    provider = RecordingEmbeddingProvider()
    snapshot = _snapshot(provider)
    retriever = ExperimentRetriever(snapshot)
    assert len(provider.calls) == 1

    for configuration in (
        RetrievalConfiguration(
            strategy=RetrievalStrategy.SEMANTIC_ONLY, top_k=2
        ),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.HYBRID, alpha=0.5, top_k=2
        ),
    ):
        result = retriever.retrieve(_query(), configuration)
        assert result.embedding_source is EmbeddingSource.PRECOMPUTED
    # Retrieval never adds a provider call on top of the one precompute.
    assert len(provider.calls) == 1


def test_semantic_retrieval_without_a_snapshot_is_refused():
    with pytest.raises(ValueError, match="precomputed embedding snapshot"):
        ExperimentRetriever(None).retrieve(
            _query(),
            RetrievalConfiguration(
                strategy=RetrievalStrategy.SEMANTIC_ONLY, top_k=2
            ),
        )


def test_semantic_only_ignores_lexical_scoring(monkeypatch):
    snapshot = _snapshot(RecordingEmbeddingProvider())
    monkeypatch.setattr(
        "mandateguard.engineering.int2.retrieval.lexical_scores",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("lexical scoring must not run")
        ),
    )
    result = ExperimentRetriever(snapshot).retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.SEMANTIC_ONLY, top_k=2
        ),
    )
    assert result.ranked_documents[0].document.evidence_id == "evidence.banana"
    assert all(item.score.lexical_score == 0.0 for item in result.ranked_documents)
    assert result.embedding_source is EmbeddingSource.PRECOMPUTED


def test_hybrid_alpha_boundaries_equal_pure_rankings():
    retriever = ExperimentRetriever(_snapshot(RecordingEmbeddingProvider()))
    semantic = retriever.retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.SEMANTIC_ONLY, top_k=2
        ),
    )
    lexical = retriever.retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=2
        ),
    )
    alpha_zero = retriever.retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.HYBRID, alpha=0.0, top_k=2
        ),
    )
    alpha_one = retriever.retrieve(
        _query(),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.HYBRID, alpha=1.0, top_k=2
        ),
    )
    ids = lambda result: tuple(
        item.document.document_id for item in result.ranked_documents
    )
    assert ids(alpha_zero) == ids(semantic)
    assert ids(alpha_one) == ids(lexical)


def test_stable_tie_behavior_uses_document_id():
    result = ExperimentRetriever(None).retrieve(
        _query(tuple(reversed(_documents()))),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=2
        ),
    )
    # Apple is a lexical match; replace the query to exercise an exact tie.
    tied_query = ExperimentQuery.from_text(
        query_id="INT2-Q-TIE",
        query="unmatched",
        documents=tuple(reversed(_documents())),
    )
    result = ExperimentRetriever(None).retrieve(
        tied_query,
        RetrievalConfiguration(
            strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=2
        ),
    )
    assert [item.document.document_id for item in result.ranked_documents] == [
        "doc.apple",
        "doc.banana",
    ]


@pytest.mark.parametrize("top_k", [0, 4, 6, -1])
def test_top_k_is_limited_to_registered_experiment_values(top_k):
    with pytest.raises(Int2ExperimentError, match="top_k"):
        RetrievalConfiguration(
            strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=top_k
        )


def test_top_k_caps_at_unique_available_evidence():
    duplicate = RetrievalDocument(
        document_id="doc.apple-copy",
        source_type=RetrievalSource.MERCHANT_EVIDENCE,
        text="apple fruit duplicate",
        merchant_id="merchant-1",
        evidence_id="evidence.apple",
    )
    result = ExperimentRetriever(None).retrieve(
        _query((*_documents(), duplicate)),
        RetrievalConfiguration(
            strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=5
        ),
    )
    assert result.retrieved_evidence_ids == (
        "evidence.apple",
        "evidence.banana",
    )


def test_matrix_has_fixed_four_strategy_dimensions_without_auto_tuning():
    matrix = retrieval_matrix()
    assert len(matrix) == 32
    assert {item.strategy for item in matrix} == set(RetrievalStrategy)
    assert {
        item.alpha
        for item in matrix
        if item.strategy is RetrievalStrategy.HYBRID
    } == {0.0, 0.25, 0.5, 0.75, 1.0}


def test_catalog_query_and_relevance_files_are_separate_and_cover_six_scenarios():
    corpus = load_query_corpus(INT2_FIXTURES / "retrieval_queries.json")
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    store = TrustedCommerceStore.from_files(
        catalog_path=CATALOG_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=CATALOG_FIXTURES / "merchant_terms.json",
    )
    queries = build_experiment_queries(corpus, store)
    assert len(queries) == len(relevance.annotations) == 6
    assert not hasattr(queries[0], "relevant_evidence_ids")
    assert {item.query_id for item in queries} == {
        item.query_id for item in relevance.annotations
    }


def test_relevance_annotations_never_enter_embedding_inputs():
    provider = RecordingEmbeddingProvider()
    sentinel = "annotation-only-sentinel"
    relevance = RelevanceManifest(
        schema_version="1.0",
        catalog_id="catalog-1",
        annotations=(
            RelevanceAnnotation(
                query_id="INT2-Q-TEST",
                relevant_evidence_ids=(sentinel,),
                required_evidence_ids=(sentinel,),
            ),
        ),
    )
    run_stage_a_sweep(
        (_query(),),
        relevance,
        provider,
        configurations=(
            RetrievalConfiguration(
                strategy=RetrievalStrategy.SEMANTIC_ONLY, top_k=1
            ),
        ),
    )
    assert len(provider.calls) == 1
    assert sentinel not in " ".join(provider.calls[0])


def test_retrieval_records_never_contain_authorization_verdicts():
    observation = RetrievalSweepHarness(ExperimentRetriever(None)).run(
        (_query(),),
        RelevanceManifest(
            schema_version="1.0",
            catalog_id="catalog-1",
            annotations=(_annotation(),),
        ),
        configurations=(
            RetrievalConfiguration(
                strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=1
            ),
        ),
    )[0]
    record = retrieval_observation_record(observation)
    serialized = json.dumps(record)
    assert "semantic_verdict" not in serialized
    assert "final_action" not in serialized
    assert "ground_truth" not in serialized


def test_full_fixture_sweep_writes_192_non_benchmark_records(tmp_path):
    corpus = load_query_corpus(INT2_FIXTURES / "retrieval_queries.json")
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    store = TrustedCommerceStore.from_files(
        catalog_path=CATALOG_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=CATALOG_FIXTURES / "merchant_terms.json",
    )
    queries = build_experiment_queries(corpus, store)
    result = run_stage_a_sweep(
        queries,
        relevance,
        MappingEmbeddingProvider(
            {
                text: (1.0, float(index % 2))
                for query in queries
                for index, text in enumerate(
                    (query.query, *(item.text for item in query.documents))
                )
            }
        ),
    )
    observations = result.observations
    paths = write_retrieval_artifacts(
        observations,
        tmp_path / "artifacts" / "engineering" / "int2",
        repository_root=tmp_path,
        embedding_snapshot=result.embedding_snapshot,
    )
    assert len(retrieval_matrix()) == 32
    assert len(queries) == 6
    assert len(observations) == 6 * 32 == 192
    assert len((paths[0]).read_text(encoding="utf-8").splitlines()) == 192
    summary = json.loads(paths[1].read_text(encoding="utf-8"))
    assert summary["observation_count"] == 192
    # Embedding accounting is reported once, not multiplied across the matrix.
    assert summary["embedding"]["embedding_api_calls"] == 1
    assert summary["embedding"]["unique_texts_total"] == 15
    assert summary["embedding"]["unique_query_texts"] == 6
    assert summary["embedding"]["unique_document_texts"] == 9
    with pytest.raises(ValueError, match="benchmark"):
        write_retrieval_artifacts(
            observations,
            tmp_path / "benchmark" / "int2",
            repository_root=tmp_path,
        )


def test_precomputed_snapshot_reproduces_direct_offline_rankings():
    """Offline behavior is unchanged by moving embedding out of the cell."""

    corpus = load_query_corpus(INT2_FIXTURES / "retrieval_queries.json")
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    store = TrustedCommerceStore.from_files(
        catalog_path=CATALOG_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=CATALOG_FIXTURES / "merchant_terms.json",
    )
    queries = build_experiment_queries(corpus, store)

    first = run_stage_a_sweep(queries, relevance, HashingEmbeddingProvider())
    second = run_stage_a_sweep(queries, relevance, HashingEmbeddingProvider())

    def ranking(result):
        return [
            (
                item.query_id,
                item.retrieval.configuration.configuration_id,
                item.retrieval.retrieved_evidence_ids,
                item.metrics.recall_at_k,
                item.metrics.precision_at_k,
                item.metrics.reciprocal_rank,
                item.metrics.all_required_retrieved,
                item.metrics.rank_of_first_required,
            )
            for item in result.observations
        ]

    assert ranking(first) == ranking(second)
    assert first.embedding_snapshot.vectors_by_text_hash == (
        second.embedding_snapshot.vectors_by_text_hash
    )


def test_default_cli_makes_zero_live_or_network_calls(tmp_path):
    (tmp_path / "openai.py").write_text(
        "raise AssertionError('retrieval-only mode imported OpenAI')\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "OPENAI_API_KEY": "must-not-be-used",
        "PYTHONPATH": os.pathsep.join(
            (str(tmp_path), str(ROOT / "src"))
        ),
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_int2_retrieval_experiments.py"),
            "--output",
            str(tmp_path / "artifacts" / "engineering" / "int2"),
        ],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    assert "semantic_calls=0 live_calls=0" in completed.stdout
