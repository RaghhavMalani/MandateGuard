"""One-time embedding precomputation shared by offline and live Stage-A runs.

Stage A evaluates 32 configurations against 6 queries. Embedding inside a
configuration cell would re-embed identical text once per cell, so this module
embeds every unique text exactly once, before the matrix is evaluated, and
hands the resulting vectors to the retriever as an immutable snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter_ns
from types import MappingProxyType
from typing import Callable, Mapping

from mandateguard.engineering.int2.models import (
    ExperimentQuery,
    Int2ExperimentError,
)
from mandateguard.intelligence.retrieval.embeddings import EmbeddingProvider


MAX_PROVIDER_CALLS = 2


def text_key(text: str) -> str:
    """Return the deterministic canonical identity of an exact experiment text."""

    if not isinstance(text, str) or not text:
        raise Int2ExperimentError("embedding text must be a non-empty string")
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddingSnapshot:
    """Immutable record of the embedding generation for one experiment run.

    `unique_query_texts` and `unique_document_texts` count distinct texts in
    each role. A text used as both a query and a document is counted in both,
    so they need not sum to `unique_text_count`.
    """

    model_id: str
    vector_dimension: int
    vectors_by_text_hash: Mapping[str, tuple[float, ...]]
    texts_by_hash: Mapping[str, str]
    identifiers_by_text_hash: Mapping[str, tuple[str, ...]]
    unique_document_texts: int
    unique_query_texts: int
    provider_call_count: int
    input_token_count: int | None
    precompute_latency_ms: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model_id, str)
            or not self.model_id
            or len(self.model_id) > 256
        ):
            raise Int2ExperimentError("model_id must be a bounded non-empty string")
        if (
            isinstance(self.vector_dimension, bool)
            or not isinstance(self.vector_dimension, int)
            or self.vector_dimension < 1
        ):
            raise Int2ExperimentError("vector_dimension must be a positive integer")
        for mapping, name in (
            (self.vectors_by_text_hash, "vectors_by_text_hash"),
            (self.texts_by_hash, "texts_by_hash"),
            (self.identifiers_by_text_hash, "identifiers_by_text_hash"),
        ):
            if not isinstance(mapping, Mapping) or not mapping:
                raise Int2ExperimentError(f"{name} must be a non-empty mapping")
        keys = frozenset(self.vectors_by_text_hash)
        if keys != frozenset(self.texts_by_hash) or keys != frozenset(
            self.identifiers_by_text_hash
        ):
            raise Int2ExperimentError("snapshot mappings must share the same keys")
        for key in keys:
            if text_key(self.texts_by_hash[key]) != key:
                raise Int2ExperimentError("text hash does not commit its text")
            vector = self.vectors_by_text_hash[key]
            if not isinstance(vector, tuple) or len(vector) != self.vector_dimension:
                raise Int2ExperimentError("vector dimensions are inconsistent")
            identifiers = self.identifiers_by_text_hash[key]
            if not isinstance(identifiers, tuple) or not identifiers:
                raise Int2ExperimentError("each text must map back to an identifier")
        for value, name in (
            (self.unique_document_texts, "unique_document_texts"),
            (self.unique_query_texts, "unique_query_texts"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise Int2ExperimentError(f"{name} must be a non-negative integer")
        if (
            isinstance(self.provider_call_count, bool)
            or not isinstance(self.provider_call_count, int)
            or not 1 <= self.provider_call_count <= MAX_PROVIDER_CALLS
        ):
            raise Int2ExperimentError(
                "provider_call_count must be batched into at most "
                f"{MAX_PROVIDER_CALLS} calls per experiment run"
            )
        if self.input_token_count is not None and (
            isinstance(self.input_token_count, bool)
            or not isinstance(self.input_token_count, int)
            or self.input_token_count < 0
        ):
            raise Int2ExperimentError(
                "input_token_count must be a non-negative integer or null"
            )
        if (
            isinstance(self.precompute_latency_ms, bool)
            or not isinstance(self.precompute_latency_ms, (int, float))
            or float(self.precompute_latency_ms) < 0.0
        ):
            raise Int2ExperimentError(
                "precompute_latency_ms must be a non-negative number"
            )

    @property
    def unique_text_count(self) -> int:
        return len(self.vectors_by_text_hash)

    def vector_for(self, text: str) -> tuple[float, ...]:
        """Return the precomputed vector for an exact text.

        The stored text is compared verbatim so two different texts can never
        be silently merged onto one vector by a shared hash key.
        """

        key = text_key(text)
        stored = self.texts_by_hash.get(key)
        if stored is None:
            raise Int2ExperimentError(
                "no precomputed embedding for this text; the snapshot must be "
                "built from the same queries the retriever is given"
            )
        if stored != text:
            raise Int2ExperimentError(
                "embedding text hash collision: refusing to merge distinct texts"
            )
        return self.vectors_by_text_hash[key]

    def identifiers_for(self, text: str) -> tuple[str, ...]:
        """Return every query/document identifier that contributed this text."""

        key = text_key(text)
        if key not in self.identifiers_by_text_hash:
            raise Int2ExperimentError("no precomputed embedding for this text")
        return self.identifiers_by_text_hash[key]


@dataclass(frozen=True, slots=True)
class UniqueEmbeddingInputs:
    """Exact-identity deduplication of every text a Stage-A run must embed."""

    texts: tuple[str, ...]
    identifiers_by_text_hash: Mapping[str, tuple[str, ...]]
    unique_document_texts: int
    unique_query_texts: int


def unique_embedding_inputs(
    queries: tuple[ExperimentQuery, ...],
) -> UniqueEmbeddingInputs:
    """Deduplicate query and document texts by exact canonical identity."""

    if not isinstance(queries, tuple) or not queries:
        raise Int2ExperimentError("queries must be a non-empty tuple")
    if not all(isinstance(item, ExperimentQuery) for item in queries):
        raise Int2ExperimentError("queries contains an invalid ExperimentQuery")

    ordered: list[str] = []
    texts_by_hash: dict[str, str] = {}
    identifiers: dict[str, list[str]] = {}
    query_hashes: set[str] = set()
    document_hashes: set[str] = set()
    for query in queries:
        pairs: list[tuple[str, str, set[str]]] = [
            (query.query, f"query:{query.query_id}", query_hashes)
        ]
        pairs.extend(
            (document.text, f"document:{document.document_id}", document_hashes)
            for document in query.documents
        )
        for text, identifier, bucket in pairs:
            key = text_key(text)
            stored = texts_by_hash.setdefault(key, text)
            if stored != text:
                raise Int2ExperimentError(
                    "embedding text hash collision: refusing to merge distinct texts"
                )
            if key not in identifiers:
                identifiers[key] = []
                ordered.append(text)
            if identifier not in identifiers[key]:
                identifiers[key].append(identifier)
            bucket.add(key)
    return UniqueEmbeddingInputs(
        texts=tuple(ordered),
        identifiers_by_text_hash=MappingProxyType(
            {key: tuple(value) for key, value in identifiers.items()}
        ),
        unique_document_texts=len(document_hashes),
        unique_query_texts=len(query_hashes),
    )


def precompute_embeddings(
    queries: tuple[ExperimentQuery, ...],
    provider: EmbeddingProvider,
    *,
    clock_ns: Callable[[], int] = perf_counter_ns,
) -> EmbeddingSnapshot:
    """Embed every unique query and document text once, in one batched call."""

    if provider is None:
        raise Int2ExperimentError("an embedding provider is required")
    model_id = getattr(provider, "model_id", None)
    if not isinstance(model_id, str) or not model_id:
        raise Int2ExperimentError("embedding provider must expose a model_id")
    inputs = unique_embedding_inputs(queries)

    started = clock_ns()
    batch = provider.embed(inputs.texts)
    latency_ms = max(0.0, (clock_ns() - started) / 1_000_000.0)

    if len(batch.vectors) != len(inputs.texts):
        raise Int2ExperimentError("embedding provider returned invalid cardinality")
    vectors: dict[str, tuple[float, ...]] = {}
    texts_by_hash: dict[str, str] = {}
    for text, vector in zip(inputs.texts, batch.vectors, strict=True):
        key = text_key(text)
        vectors[key] = vector
        texts_by_hash[key] = text
    return EmbeddingSnapshot(
        model_id=model_id,
        vector_dimension=len(batch.vectors[0]),
        vectors_by_text_hash=MappingProxyType(vectors),
        texts_by_hash=MappingProxyType(texts_by_hash),
        identifiers_by_text_hash=inputs.identifiers_by_text_hash,
        unique_document_texts=inputs.unique_document_texts,
        unique_query_texts=inputs.unique_query_texts,
        provider_call_count=1,
        input_token_count=batch.input_tokens,
        precompute_latency_ms=latency_ms,
    )
