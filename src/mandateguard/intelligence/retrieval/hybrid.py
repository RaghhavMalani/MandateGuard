"""Auditable lexical/semantic hybrid ranking without a hidden reranker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from time import perf_counter

from mandateguard.intelligence.models import (
    RankedDocument,
    RetrievalDocument,
    RetrievalResult,
    RetrievalScore,
)
from mandateguard.intelligence.retrieval.embeddings import EmbeddingProvider
from mandateguard.intelligence.retrieval.lexical import lexical_scores


DEFAULT_ALPHA = 0.4
DEFAULT_TOP_K = 5


class RetrievalMode(str, Enum):
    NONE = "no_retrieval"
    LEXICAL = "lexical_only"
    HYBRID = "hybrid"


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(
        0.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right, strict=True))
            / (left_norm * right_norm),
        ),
    )


@dataclass(frozen=True, slots=True)
class HybridRetriever:
    embedding_provider: EmbeddingProvider | None

    def retrieve(
        self,
        *,
        query: str,
        query_sha256: str,
        documents: tuple[RetrievalDocument, ...],
        alpha: float = DEFAULT_ALPHA,
        top_k: int = DEFAULT_TOP_K,
        mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> RetrievalResult:
        if not isinstance(mode, RetrievalMode):
            raise TypeError("mode must be RetrievalMode")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0.0 <= float(alpha) <= 1.0:
            raise ValueError("alpha must be within [0, 1]")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if not isinstance(documents, tuple) or not all(
            isinstance(item, RetrievalDocument) for item in documents
        ):
            raise TypeError("documents must be a tuple of RetrievalDocument")
        if mode is RetrievalMode.NONE:
            return RetrievalResult(
                query=query,
                query_sha256=query_sha256,
                ranked_documents=(),
                alpha=float(alpha),
                top_k=top_k,
                embedding_latency_ms=0.0,
            )

        lexical = lexical_scores(query, documents)
        semantic = {document.document_id: 0.0 for document in documents}
        embedding_latency_ms = 0.0
        input_tokens: int | None = None
        if mode is RetrievalMode.HYBRID:
            if self.embedding_provider is None:
                raise ValueError("hybrid retrieval requires an embedding provider")
            started = perf_counter()
            batch = self.embedding_provider.embed(
                (query, *(document.text for document in documents))
            )
            embedding_latency_ms = (perf_counter() - started) * 1000.0
            if len(batch.vectors) != len(documents) + 1:
                raise ValueError("embedding provider returned invalid cardinality")
            query_vector = batch.vectors[0]
            semantic = {
                document.document_id: _cosine(query_vector, vector)
                for document, vector in zip(
                    documents, batch.vectors[1:], strict=True
                )
            }
            input_tokens = batch.input_tokens

        ranked: list[RankedDocument] = []
        for document in documents:
            lexical_score = lexical[document.document_id]
            semantic_score = semantic[document.document_id]
            hybrid_score = (
                lexical_score
                if mode is RetrievalMode.LEXICAL
                else float(alpha) * lexical_score
                + (1.0 - float(alpha)) * semantic_score
            )
            ranked.append(
                RankedDocument(
                    document=document,
                    score=RetrievalScore(
                        document_id=document.document_id,
                        source_type=document.source_type,
                        lexical_score=lexical_score,
                        semantic_score=semantic_score,
                        hybrid_score=hybrid_score,
                    ),
                )
            )
        ranked.sort(
            key=lambda item: (
                -item.score.hybrid_score,
                -item.score.lexical_score,
                -item.score.semantic_score,
                item.document.document_id,
            )
        )
        return RetrievalResult(
            query=query,
            query_sha256=query_sha256,
            ranked_documents=tuple(ranked[:top_k]),
            alpha=float(alpha),
            top_k=top_k,
            embedding_latency_ms=embedding_latency_ms,
            input_tokens=input_tokens,
        )
