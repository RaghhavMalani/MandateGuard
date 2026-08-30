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
from mandateguard.intelligence.retrieval.lexical import lexical_scores
from mandateguard.engineering.int2.embeddings import EmbeddingSnapshot
from mandateguard.engineering.int2.models import (
    EmbeddingSource,
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
    """Four-condition retriever isolated from the INT-1 production defaults.

    The retriever never calls an embedding provider. Semantic and hybrid
    configurations read vectors from an `EmbeddingSnapshot` produced once per
    experiment run, so retrieval latency measures ranking work alone.
    """

    embedding_snapshot: EmbeddingSnapshot | None
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
                embedding_source=EmbeddingSource.NOT_USED,
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
        embedding_source = EmbeddingSource.NOT_USED
        if strategy in {
            RetrievalStrategy.SEMANTIC_ONLY,
            RetrievalStrategy.HYBRID,
        }:
            snapshot = self.embedding_snapshot
            if snapshot is None:
                raise ValueError(
                    f"{strategy.value} requires a precomputed embedding snapshot"
                )
            query_vector = snapshot.vector_for(query.query)
            semantic = {
                document.document_id: _cosine(
                    query_vector, snapshot.vector_for(document.text)
                )
                for document in documents
            }
            embedding_source = EmbeddingSource.PRECOMPUTED

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
            embedding_source=embedding_source,
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
