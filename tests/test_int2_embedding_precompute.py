"""Stage-A embeddings must be generated once per run, never per configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mandateguard.engineering.int2.embeddings import (
    EmbeddingSnapshot,
    precompute_embeddings,
    text_key,
    unique_embedding_inputs,
)
from mandateguard.engineering.int2.fixtures import (
    build_experiment_queries,
    load_query_corpus,
    load_relevance_manifest,
)
from mandateguard.engineering.int2.models import (
    EmbeddingSource,
    ExperimentQuery,
    Int2ExperimentError,
    RetrievalStrategy,
    retrieval_matrix,
)
from mandateguard.engineering.int2.sweep import run_stage_a_sweep
from mandateguard.intelligence.models import RetrievalDocument, RetrievalSource
from mandateguard.intelligence.retrieval.embeddings import (
    EmbeddingBatch,
    HashingEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from mandateguard.intelligence.store import TrustedCommerceStore


ROOT = Path(__file__).resolve().parents[1]
INT2_FIXTURES = ROOT / "fixtures" / "engineering" / "int2"
CATALOG_FIXTURES = ROOT / "fixtures" / "agentic_commerce"


@dataclass
class CountingEmbeddingProvider:
    """Records every batch a run submits so redundancy is directly observable."""

    model_id: str = "counting-fake-v1"
    dimensions: int = 8
    calls: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def submitted_texts(self) -> list[str]:
        return [text for call in self.calls for text in call]

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        self.calls.append(texts)
        return EmbeddingBatch(
            vectors=tuple(
                tuple(
                    float((index + position) % 3)
                    for position in range(self.dimensions)
                )
                for index, _ in enumerate(texts)
            ),
            input_tokens=11,
        )


@dataclass
class SteppingClock:
    """Monotonic fake clock; a provider may jump it to simulate slow work."""

    now: int = 0
    step: int = 1

    def __call__(self) -> int:
        self.now += self.step
        return self.now


@dataclass
class SlowEmbeddingProvider:
    """Charges a large, unmistakable cost to the one precompute call."""

    clock: SteppingClock
    cost_ns: int
    model_id: str = "slow-fake-v1"

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        self.clock.now += self.cost_ns
        return EmbeddingBatch(
            vectors=tuple((1.0, float(index)) for index, _ in enumerate(texts)),
            input_tokens=None,
        )


def _fixture_queries() -> tuple[ExperimentQuery, ...]:
    corpus = load_query_corpus(INT2_FIXTURES / "retrieval_queries.json")
    store = TrustedCommerceStore.from_files(
        catalog_path=CATALOG_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=CATALOG_FIXTURES / "merchant_terms.json",
    )
    return build_experiment_queries(corpus, store)


def _document(document_id: str, text: str, evidence_id: str) -> RetrievalDocument:
    return RetrievalDocument(
        document_id=document_id,
        source_type=RetrievalSource.MERCHANT_EVIDENCE,
        text=text,
        merchant_id="merchant-1",
        evidence_id=evidence_id,
    )


def test_full_fixture_sweep_submits_only_fifteen_unique_texts():
    queries = _fixture_queries()
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    provider = CountingEmbeddingProvider()

    result = run_stage_a_sweep(queries, relevance, provider)

    submitted = provider.submitted_texts
    assert len(submitted) == len(set(submitted)) == 15
    snapshot = result.embedding_snapshot
    assert snapshot.unique_query_texts == 6
    assert snapshot.unique_document_texts == 9
    assert snapshot.unique_text_count == 15


def test_provider_calls_are_batched_and_not_proportional_to_observations():
    queries = _fixture_queries()
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    provider = CountingEmbeddingProvider()

    result = run_stage_a_sweep(queries, relevance, provider)

    semantic_cells = sum(
        1
        for query in queries
        for configuration in retrieval_matrix()
        if configuration.strategy
        in {RetrievalStrategy.SEMANTIC_ONLY, RetrievalStrategy.HYBRID}
    )
    assert semantic_cells == 144
    assert len(result.observations) == 192
    assert len(provider.calls) == 1
    assert result.embedding_snapshot.provider_call_count == 1


def test_snapshot_rejects_call_counts_that_scale_with_the_matrix():
    provider = CountingEmbeddingProvider()
    snapshot = precompute_embeddings(
        (
            ExperimentQuery.from_text(
                query_id="INT2-Q-TEST",
                query="apple",
                documents=(_document("doc.apple", "apple fruit", "evidence.apple"),),
            ),
        ),
        provider,
    )
    fields = {
        field: getattr(snapshot, field)
        for field in (
            "model_id",
            "vector_dimension",
            "vectors_by_text_hash",
            "texts_by_hash",
            "identifiers_by_text_hash",
            "unique_document_texts",
            "unique_query_texts",
            "input_token_count",
            "precompute_latency_ms",
        )
    }
    with pytest.raises(Int2ExperimentError, match="at most"):
        EmbeddingSnapshot(provider_call_count=144, **fields)


def test_batched_call_maps_each_text_to_its_own_vector():
    documents = (
        _document("doc.apple", "apple fruit", "evidence.apple"),
        _document("doc.banana", "banana fruit", "evidence.banana"),
    )
    query = ExperimentQuery.from_text(
        query_id="INT2-Q-TEST", query="apple", documents=documents
    )
    provider = CountingEmbeddingProvider()

    snapshot = precompute_embeddings((query,), provider)

    assert provider.calls == [("apple", "apple fruit", "banana fruit")]
    expected = provider.embed(("apple", "apple fruit", "banana fruit")).vectors
    assert snapshot.vector_for("apple") == expected[0]
    assert snapshot.vector_for("apple fruit") == expected[1]
    assert snapshot.vector_for("banana fruit") == expected[2]
    assert snapshot.identifiers_for("apple") == ("query:INT2-Q-TEST",)
    assert snapshot.identifiers_for("apple fruit") == ("document:doc.apple",)


def test_identical_text_in_two_documents_is_embedded_once():
    shared = "identical evidence body"
    documents = (
        _document("doc.first", shared, "evidence.first"),
        _document("doc.second", shared, "evidence.second"),
    )
    query = ExperimentQuery.from_text(
        query_id="INT2-Q-TEST", query="lookup", documents=documents
    )
    provider = CountingEmbeddingProvider()

    snapshot = precompute_embeddings((query,), provider)

    assert provider.submitted_texts == ["lookup", shared]
    assert snapshot.unique_text_count == 2
    assert snapshot.unique_document_texts == 1
    assert snapshot.identifiers_for(shared) == (
        "document:doc.first",
        "document:doc.second",
    )


def test_identical_query_text_across_queries_is_embedded_once():
    documents = (_document("doc.apple", "apple fruit", "evidence.apple"),)
    queries = (
        ExperimentQuery.from_text(
            query_id="INT2-Q-ONE", query="same question", documents=documents
        ),
        ExperimentQuery.from_text(
            query_id="INT2-Q-TWO", query="same question", documents=documents
        ),
    )
    provider = CountingEmbeddingProvider()

    snapshot = precompute_embeddings(queries, provider)

    assert provider.submitted_texts == ["same question", "apple fruit"]
    assert snapshot.unique_query_texts == 1
    assert snapshot.identifiers_for("same question") == (
        "query:INT2-Q-ONE",
        "query:INT2-Q-TWO",
    )


def test_distinct_texts_never_share_a_vector():
    documents = (
        _document("doc.apple", "apple fruit", "evidence.apple"),
        _document("doc.banana", "banana fruit", "evidence.banana"),
    )
    query = ExperimentQuery.from_text(
        query_id="INT2-Q-TEST", query="apple", documents=documents
    )

    snapshot = precompute_embeddings((query,), CountingEmbeddingProvider())

    keys = {
        text_key("apple"),
        text_key("apple fruit"),
        text_key("banana fruit"),
    }
    assert len(keys) == 3
    assert frozenset(snapshot.vectors_by_text_hash) == keys
    assert snapshot.vector_for("apple fruit") != snapshot.vector_for("banana fruit")


def test_colliding_hash_for_different_text_is_refused_not_merged():
    documents = (_document("doc.apple", "apple fruit", "evidence.apple"),)
    query = ExperimentQuery.from_text(
        query_id="INT2-Q-TEST", query="apple", documents=documents
    )
    snapshot = precompute_embeddings((query,), CountingEmbeddingProvider())
    apple_vector = snapshot.vector_for("apple fruit")
    key = text_key("apple fruit")

    # Simulate a key shared by two different texts. Construction already
    # rejects this, so corrupt the mapping afterwards to reach the lookup
    # guard and prove it refuses rather than returning the wrong vector.
    object.__setattr__(
        snapshot,
        "texts_by_hash",
        {**snapshot.texts_by_hash, key: "a completely different text"},
    )
    with pytest.raises(Int2ExperimentError, match="collision"):
        snapshot.vector_for("apple fruit")
    # The intruding text hashes to its own key, so it is reported missing
    # rather than being handed the vector that belongs to "apple fruit".
    with pytest.raises(Int2ExperimentError, match="no precomputed embedding"):
        snapshot.vector_for("a completely different text")
    assert apple_vector == snapshot.vectors_by_text_hash[key]


def test_snapshot_rejects_a_text_hash_that_does_not_commit_its_text():
    documents = (_document("doc.apple", "apple fruit", "evidence.apple"),)
    query = ExperimentQuery.from_text(
        query_id="INT2-Q-TEST", query="apple", documents=documents
    )
    snapshot = precompute_embeddings((query,), CountingEmbeddingProvider())
    with pytest.raises(Int2ExperimentError, match="does not commit"):
        EmbeddingSnapshot(
            model_id=snapshot.model_id,
            vector_dimension=snapshot.vector_dimension,
            vectors_by_text_hash={"0" * 64: (0.0,) * snapshot.vector_dimension},
            texts_by_hash={"0" * 64: "apple"},
            identifiers_by_text_hash={"0" * 64: ("query:INT2-Q-TEST",)},
            unique_document_texts=0,
            unique_query_texts=1,
            provider_call_count=1,
            input_token_count=None,
            precompute_latency_ms=0.0,
        )


def test_unknown_text_is_reported_rather_than_silently_embedded():
    documents = (_document("doc.apple", "apple fruit", "evidence.apple"),)
    query = ExperimentQuery.from_text(
        query_id="INT2-Q-TEST", query="apple", documents=documents
    )
    snapshot = precompute_embeddings((query,), CountingEmbeddingProvider())
    with pytest.raises(Int2ExperimentError, match="no precomputed embedding"):
        snapshot.vector_for("never embedded")


def test_unique_inputs_preserve_first_seen_order_without_relevance_data():
    queries = _fixture_queries()
    inputs = unique_embedding_inputs(queries)
    assert inputs.texts[0] == queries[0].query
    assert len(inputs.texts) == 15
    assert len(set(inputs.texts)) == 15


def test_observation_latency_excludes_the_one_time_precompute():
    queries = _fixture_queries()
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    clock = SteppingClock()
    provider = SlowEmbeddingProvider(clock=clock, cost_ns=500_000_000)

    result = run_stage_a_sweep(queries, relevance, provider, clock_ns=clock)

    assert result.embedding_snapshot.precompute_latency_ms >= 500.0
    # Each observation only advances the fake clock by its own ranking ticks.
    assert all(
        observation.retrieval.retrieval_latency_ms < 1.0
        for observation in result.observations
    )
    total_observation_latency = sum(
        observation.retrieval.retrieval_latency_ms
        for observation in result.observations
    )
    assert total_observation_latency < 1.0


def test_embedding_source_is_recorded_per_strategy():
    queries = _fixture_queries()
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")

    result = run_stage_a_sweep(queries, relevance, HashingEmbeddingProvider())

    by_strategy: dict[RetrievalStrategy, set[EmbeddingSource]] = {}
    for observation in result.observations:
        strategy = observation.retrieval.configuration.strategy
        by_strategy.setdefault(strategy, set()).add(
            observation.retrieval.embedding_source
        )
    assert by_strategy[RetrievalStrategy.NO_RETRIEVAL] == {EmbeddingSource.NOT_USED}
    assert by_strategy[RetrievalStrategy.LEXICAL_ONLY] == {EmbeddingSource.NOT_USED}
    assert by_strategy[RetrievalStrategy.SEMANTIC_ONLY] == {
        EmbeddingSource.PRECOMPUTED
    }
    assert by_strategy[RetrievalStrategy.HYBRID] == {EmbeddingSource.PRECOMPUTED}


def test_live_openai_adapter_batches_the_whole_run_into_one_request():
    """The live path sends 15 texts in a single request, with no network here."""

    class FakeEmbeddings:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            inputs = kwargs["input"]
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=index, embedding=[1.0, float(index)])
                    for index, _ in enumerate(inputs)
                ],
                usage=SimpleNamespace(prompt_tokens=137),
            )

    embeddings = FakeEmbeddings()
    provider = OpenAIEmbeddingProvider(
        client=SimpleNamespace(embeddings=embeddings)
    )
    queries = _fixture_queries()
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")

    result = run_stage_a_sweep(queries, relevance, provider)

    assert len(embeddings.calls) == 1
    request = embeddings.calls[0]
    assert request["model"] == "text-embedding-3-small"
    assert len(request["input"]) == 15
    assert len(set(request["input"])) == 15
    snapshot = result.embedding_snapshot
    assert snapshot.model_id == "text-embedding-3-small"
    assert snapshot.provider_call_count == 1
    assert snapshot.input_token_count == 137
    assert len(result.observations) == 192


def _script_environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    (tmp_path / "openai.py").write_text(
        "raise AssertionError('offline INT-2 run imported OpenAI')\n",
        encoding="utf-8",
    )
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(ROOT / "src"))),
        **overrides,
    }


def test_live_embeddings_without_credentials_fails_before_execution(tmp_path):
    output = tmp_path / "artifacts" / "engineering" / "int2"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_int2_retrieval_experiments.py"),
            "--live-embeddings",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        # An empty value keeps python-dotenv from filling it from a local .env.
        env=_script_environment(tmp_path, OPENAI_API_KEY=""),
    )
    assert completed.returncode != 0
    assert "OPENAI_API_KEY" in completed.stderr
    assert "observations=" not in completed.stdout
    assert not output.exists()


def test_live_mode_never_silently_falls_back_to_the_offline_provider(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_int2_retrieval_experiments.py"),
            "--live-embeddings",
            "--output",
            str(tmp_path / "artifacts" / "engineering" / "int2"),
        ],
        capture_output=True,
        text=True,
        env=_script_environment(tmp_path, OPENAI_API_KEY=""),
    )
    assert "HashingEmbeddingProvider" not in completed.stdout
    assert "live_calls=" not in completed.stdout


_OFFLINE_PROBE = '''
import pathlib
import socket

from mandateguard.engineering.int2.fixtures import (
    build_experiment_queries,
    load_query_corpus,
    load_relevance_manifest,
)
from mandateguard.engineering.int2.sweep import run_stage_a_sweep
from mandateguard.intelligence.retrieval.embeddings import HashingEmbeddingProvider
from mandateguard.intelligence.store import TrustedCommerceStore


def _blocked(*_args, **_kwargs):
    raise AssertionError("INT-2 offline sweep attempted a network call")


# Patched after the imports so stdlib modules subclassing socket still load.
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

root = pathlib.Path(r"{root}")
int2 = root / "fixtures" / "engineering" / "int2"
commerce = root / "fixtures" / "agentic_commerce"
corpus = load_query_corpus(int2 / "retrieval_queries.json")
relevance = load_relevance_manifest(int2 / "relevance_manifest.json")
store = TrustedCommerceStore.from_files(
    catalog_path=commerce / "merchant_catalog.json",
    merchant_terms_path=commerce / "merchant_terms.json",
)
queries = build_experiment_queries(corpus, store)
result = run_stage_a_sweep(queries, relevance, HashingEmbeddingProvider())
assert len(result.observations) == 192
assert result.embedding_snapshot.provider_call_count == 1
print("offline-ok")
'''


def test_offline_run_makes_no_network_connection(tmp_path):
    """Prove the default path is offline by breaking outbound connections."""

    probe = tmp_path / "offline_probe.py"
    probe.write_text(_OFFLINE_PROBE.format(root=ROOT), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        env=_script_environment(tmp_path, OPENAI_API_KEY="must-not-be-used"),
    )
    assert completed.returncode == 0, completed.stderr
    assert "offline-ok" in completed.stdout
