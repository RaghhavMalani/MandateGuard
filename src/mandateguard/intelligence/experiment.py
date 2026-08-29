"""Offline interfaces for a future retrieval ablation experiment.

This module intentionally makes no quality claim and writes no result artifact.
"""

from __future__ import annotations

from dataclasses import dataclass

from mandateguard.intelligence.models import RetrievalDocument, RetrievalResult
from mandateguard.intelligence.retrieval.hybrid import (
    DEFAULT_ALPHA,
    DEFAULT_TOP_K,
    HybridRetriever,
    RetrievalMode,
)


@dataclass(frozen=True, slots=True)
class RetrievalExperimentCase:
    case_id: str
    query: str
    query_sha256: str
    documents: tuple[RetrievalDocument, ...]


@dataclass(frozen=True, slots=True)
class RetrievalExperimentObservation:
    case_id: str
    variant: RetrievalMode
    retrieval: RetrievalResult


@dataclass(frozen=True, slots=True)
class RetrievalExperimentHarness:
    retriever: HybridRetriever

    def run(
        self,
        cases: tuple[RetrievalExperimentCase, ...],
        *,
        alpha: float = DEFAULT_ALPHA,
        top_k: int = DEFAULT_TOP_K,
    ) -> tuple[RetrievalExperimentObservation, ...]:
        observations: list[RetrievalExperimentObservation] = []
        for case in cases:
            for variant in (
                RetrievalMode.NONE,
                RetrievalMode.LEXICAL,
                RetrievalMode.HYBRID,
            ):
                observations.append(
                    RetrievalExperimentObservation(
                        case_id=case.case_id,
                        variant=variant,
                        retrieval=self.retriever.retrieve(
                            query=case.query,
                            query_sha256=case.query_sha256,
                            documents=case.documents,
                            alpha=alpha,
                            top_k=top_k,
                            mode=variant,
                        ),
                    )
                )
        return tuple(observations)
