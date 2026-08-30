"""Network-free-capable retrieval and scoring for INT-2 Stage A."""

from __future__ import annotations

from dataclasses import dataclass
import math
from time import perf_counter_ns
from typing import Callable

from mandateguard.intelligence.models import (
    RankedDocument,
    RetrievalDocument,
    RetrievalScore,
)
from mandateguard.intelligence.retrieval.embeddings import EmbeddingProvider
from mandateguard.intelligence.retrieval.lexical import lexical_scores
from mandateguard.engineering.int2.models import (
    ExperimentQuery,
    ExperimentRetrievalResult,
    RelevanceAnnotation,
    RetrievalConfiguration,
    RetrievalMetrics,
    RetrievalStrategy,
)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    value = sum(
        a * b for a, b in zip(left, right, strict=True)
    ) / (left_norm * right_norm)
    return max(0.0, min(1.0, value))


def _milliseconds(started: int, finished: int) -> float:
    return max(0.0, (finished - started) / 1_000_000.0)


def _deduplicate_ranked_evidence(
    ranked: list[RankedDocument],
) -> list[RankedDocument]:
    """Keep the first rank for duplicate evidence without inflating metrics."""

    seen: set[str] = set()
    unique: list[RankedDocument] = []
    for item in ranked:
        key = item.document.evidence_id or f"document:{item.document.document_id}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


@dataclass(frozen=True, slots=True)
class ExperimentRetriever:
    """Four-condition retriever isolated from the INT-1 production defaults."""

    embedding_provider: EmbeddingProvider | None
    clock_ns: Callable[[], int] = perf_counter_ns

    def retrieve(
        self,
        query: ExperimentQuery,
        configuration: RetrievalConfiguration,
    ) -> ExperimentRetrievalResult:
        if not isinstance(query, ExperimentQuery):
            raise TypeError("query must be ExperimentQuery")
        if not isinstance(configuration, RetrievalConfiguration):
            raise TypeError("configuration must be RetrievalConfiguration")

        retrieval_started = self.clock_ns()
        if configuration.strategy is RetrievalStrategy.NO_RETRIEVAL:
            return ExperimentRetrievalResult(
                configuration=configuration,
                ranked_documents=(),
                retrieval_latency_ms=_milliseconds(
                    retrieval_started, self.clock_ns()
                ),
                embedding_latency_ms=0.0,
                embedding_calls=0,
                embedding_input_tokens=None,
            )

        documents = query.documents
        strategy = configuration.strategy
        lexical = {item.document_id: 0.0 for item in documents}
        if strategy in {
            RetrievalStrategy.LEXICAL_ONLY,
            RetrievalStrategy.HYBRID,
        }:
            lexical = lexical_scores(query.query, documents)

        semantic = {item.document_id: 0.0 for item in documents}
        embedding_latency_ms = 0.0
        embedding_calls = 0
        embedding_input_tokens: int | None = None
        if strategy in {
            RetrievalStrategy.SEMANTIC_ONLY,
            RetrievalStrategy.HYBRID,
        }:
            if self.embedding_provider is None:
                raise ValueError(
                    f"{strategy.value} requires an embedding provider"
                )
            embedding_started = self.clock_ns()
            batch = self.embedding_provider.embed(
                (query.query, *(item.text for item in documents))
            )
            embedding_latency_ms = _milliseconds(
                embedding_started, self.clock_ns()
            )
            embedding_calls = 1
            if len(batch.vectors) != len(documents) + 1:
                raise ValueError("embedding provider returned invalid cardinality")
            semantic = {
                document.document_id: _cosine(batch.vectors[0], vector)
                for document, vector in zip(
                    documents, batch.vectors[1:], strict=True
                )
            }
            embedding_input_tokens = batch.input_tokens

        ranked: list[RankedDocument] = []
        for document in documents:
            lexical_score = lexical[document.document_id]
            semantic_score = semantic[document.document_id]
            if strategy is RetrievalStrategy.LEXICAL_ONLY:
                score = lexical_score
            elif strategy is RetrievalStrategy.SEMANTIC_ONLY:
                score = semantic_score
            else:
                alpha = configuration.alpha
                assert alpha is not None
                score = alpha * lexical_score + (1.0 - alpha) * semantic_score
            ranked.append(
                RankedDocument(
                    document=document,
                    score=RetrievalScore(
                        document_id=document.document_id,
                        source_type=document.source_type,
                        lexical_score=lexical_score,
                        semantic_score=semantic_score,
                        hybrid_score=score,
                    ),
                )
            )

        if strategy is RetrievalStrategy.SEMANTIC_ONLY or (
            strategy is RetrievalStrategy.HYBRID and configuration.alpha == 0.0
        ):
            ranked.sort(
                key=lambda item: (
                    -item.score.semantic_score,
                    item.document.document_id,
                )
            )
        elif strategy is RetrievalStrategy.LEXICAL_ONLY or (
            strategy is RetrievalStrategy.HYBRID and configuration.alpha == 1.0
        ):
            ranked.sort(
                key=lambda item: (
                    -item.score.lexical_score,
                    item.document.document_id,
                )
            )
        else:
            ranked.sort(
                key=lambda item: (
                    -item.score.hybrid_score,
                    -item.score.lexical_score,
                    -item.score.semantic_score,
                    item.document.document_id,
                )
            )
        ranked = _deduplicate_ranked_evidence(ranked)
        finished = self.clock_ns()
        return ExperimentRetrievalResult(
            configuration=configuration,
            ranked_documents=tuple(ranked[: configuration.top_k]),
            retrieval_latency_ms=_milliseconds(retrieval_started, finished),
            embedding_latency_ms=embedding_latency_ms,
            embedding_calls=embedding_calls,
            embedding_input_tokens=embedding_input_tokens,
        )


def compute_retrieval_metrics(
    retrieved_evidence_ids: tuple[str, ...],
    annotation: RelevanceAnnotation,
    *,
    top_k: int,
) -> RetrievalMetrics:
    """Score unique retrieved evidence; annotations are never retrieval inputs."""

    if not isinstance(retrieved_evidence_ids, tuple) or not all(
        isinstance(item, str) for item in retrieved_evidence_ids
    ):
        raise TypeError("retrieved_evidence_ids must be a tuple of strings")
    if not isinstance(annotation, RelevanceAnnotation):
        raise TypeError("annotation must be RelevanceAnnotation")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be positive")

    unique: list[str] = []
    seen: set[str] = set()
    for evidence_id in retrieved_evidence_ids:
        if evidence_id not in seen:
            seen.add(evidence_id)
            unique.append(evidence_id)
    ranked = tuple(unique[:top_k])
    relevant = frozenset(annotation.relevant_evidence_ids)
    required = frozenset(annotation.required_evidence_ids)
    relevant_retrieved = sum(item in relevant for item in ranked)
    recall = relevant_retrieved / len(relevant) if relevant else 1.0
    precision = relevant_retrieved / len(ranked) if ranked else 0.0
    first_relevant = next(
        (rank for rank, item in enumerate(ranked, start=1) if item in relevant),
        None,
    )
    first_required = next(
        (rank for rank, item in enumerate(ranked, start=1) if item in required),
        None,
    )
    return RetrievalMetrics(
        recall_at_k=recall,
        precision_at_k=precision,
        reciprocal_rank=(
            1.0 / first_relevant
            if first_relevant is not None
            else None if not relevant else 0.0
        ),
        all_required_retrieved=required.issubset(ranked),
        rank_of_first_required=first_required,
    )
