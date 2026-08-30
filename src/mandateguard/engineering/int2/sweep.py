"""Stage-A retrieval-only sweep; this module has no semantic model dependency."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from typing import Callable

from mandateguard.engineering.int2.embeddings import (
    EmbeddingSnapshot,
    precompute_embeddings,
)
from mandateguard.engineering.int2.models import (
    ExperimentQuery,
    RelevanceManifest,
    RetrievalConfiguration,
    RetrievalObservation,
    retrieval_matrix,
)
from mandateguard.engineering.int2.retrieval import (
    ExperimentRetriever,
    compute_retrieval_metrics,
)
from mandateguard.intelligence.retrieval.embeddings import EmbeddingProvider


@dataclass(frozen=True, slots=True)
class RetrievalSweepHarness:
    retriever: ExperimentRetriever

    def run(
        self,
        queries: tuple[ExperimentQuery, ...],
        relevance_manifest: RelevanceManifest,
        *,
        configurations: tuple[RetrievalConfiguration, ...] | None = None,
    ) -> tuple[RetrievalObservation, ...]:
        """Run retrieval only; relevance is joined after each retrieval returns."""

        if not isinstance(queries, tuple) or not queries:
            raise ValueError("queries must be a non-empty tuple")
        if not all(isinstance(item, ExperimentQuery) for item in queries):
            raise TypeError("queries contains an invalid ExperimentQuery")
        if not isinstance(relevance_manifest, RelevanceManifest):
            raise TypeError("relevance_manifest must be RelevanceManifest")
        query_ids = [item.query_id for item in queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query IDs must be unique")
        matrix = configurations if configurations is not None else retrieval_matrix()
        if not isinstance(matrix, tuple) or not matrix:
            raise ValueError("configurations must be a non-empty tuple")
        if not all(isinstance(item, RetrievalConfiguration) for item in matrix):
            raise TypeError("configurations contains an invalid value")

        observations: list[RetrievalObservation] = []
        for query in queries:
            annotation = relevance_manifest.for_query(query.query_id)
            for configuration in matrix:
                retrieval = self.retriever.retrieve(query, configuration)
                observations.append(
                    RetrievalObservation(
                        query_id=query.query_id,
                        retrieval=retrieval,
                        metrics=compute_retrieval_metrics(
                            retrieval.retrieved_evidence_ids,
                            annotation,
                            top_k=configuration.top_k,
                        ),
                    )
                )
        return tuple(observations)


@dataclass(frozen=True, slots=True)
class StageASweepResult:
    """Observations plus the single embedding generation that fed them."""

    observations: tuple[RetrievalObservation, ...]
    embedding_snapshot: EmbeddingSnapshot


def run_stage_a_sweep(
    queries: tuple[ExperimentQuery, ...],
    relevance_manifest: RelevanceManifest,
    provider: EmbeddingProvider,
    *,
    configurations: tuple[RetrievalConfiguration, ...] | None = None,
    clock_ns: Callable[[], int] = perf_counter_ns,
) -> StageASweepResult:
    """Embed every unique text once, then evaluate the configuration matrix.

    Both the offline and live modes take this path, so the two runs differ
    only by which provider produced the snapshot.
    """

    snapshot = precompute_embeddings(queries, provider, clock_ns=clock_ns)
    observations = RetrievalSweepHarness(
        ExperimentRetriever(snapshot, clock_ns=clock_ns)
    ).run(queries, relevance_manifest, configurations=configurations)
    return StageASweepResult(observations=observations, embedding_snapshot=snapshot)
