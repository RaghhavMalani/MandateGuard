"""Typed values shared by the non-benchmark INT-2 experiment harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import math
import re

from mandateguard.intelligence.models import RankedDocument, RetrievalDocument


SUPPORTED_ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
SUPPORTED_TOP_K = (1, 2, 3, 5)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Int2ExperimentError(ValueError):
    """An INT-2 engineering experiment input is invalid."""


class RetrievalStrategy(str, Enum):
    NO_RETRIEVAL = "no_retrieval"
    LEXICAL_ONLY = "lexical_only"
    SEMANTIC_ONLY = "semantic_only"
    HYBRID = "hybrid"


class EmbeddingSource(str, Enum):
    """Where an observation's semantic vectors came from.

    Stage A embeds every unique text once before the configuration matrix is
    evaluated, so no observation ever generates an embedding of its own.
    """

    NOT_USED = "not_used"
    PRECOMPUTED = "precomputed"


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise Int2ExperimentError(f"{name} must be a bounded identifier")
    return value


def _nonnegative_int_or_none(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Int2ExperimentError(f"{name} must be a non-negative integer or null")
    return value


def _nonnegative_number(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise Int2ExperimentError(f"{name} must be a finite non-negative number")
    return float(value)


@dataclass(frozen=True, slots=True)
class RetrievalConfiguration:
    strategy: RetrievalStrategy
    top_k: int
    alpha: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, RetrievalStrategy):
            raise Int2ExperimentError("strategy must be a RetrievalStrategy")
        if self.top_k not in SUPPORTED_TOP_K:
            raise Int2ExperimentError(
                f"top_k must be one of {SUPPORTED_TOP_K}"
            )
        if self.strategy is RetrievalStrategy.HYBRID:
            if (
                isinstance(self.alpha, bool)
                or not isinstance(self.alpha, (int, float))
                or float(self.alpha) not in SUPPORTED_ALPHAS
            ):
                raise Int2ExperimentError(
                    f"hybrid alpha must be one of {SUPPORTED_ALPHAS}"
                )
            object.__setattr__(self, "alpha", float(self.alpha))
        elif self.alpha is not None:
            raise Int2ExperimentError("alpha is defined only for hybrid retrieval")

    @property
    def configuration_id(self) -> str:
        alpha = "na" if self.alpha is None else format(self.alpha, ".2f")
        return f"{self.strategy.value}.alpha-{alpha}.k-{self.top_k}"


def retrieval_matrix() -> tuple[RetrievalConfiguration, ...]:
    """Return the fixed, untuned 32-configuration Stage-A matrix."""

    configurations: list[RetrievalConfiguration] = []
    for strategy in (
        RetrievalStrategy.NO_RETRIEVAL,
        RetrievalStrategy.LEXICAL_ONLY,
        RetrievalStrategy.SEMANTIC_ONLY,
    ):
        configurations.extend(
            RetrievalConfiguration(strategy=strategy, top_k=top_k)
            for top_k in SUPPORTED_TOP_K
        )
    configurations.extend(
        RetrievalConfiguration(
            strategy=RetrievalStrategy.HYBRID,
            alpha=alpha,
            top_k=top_k,
        )
        for alpha in SUPPORTED_ALPHAS
        for top_k in SUPPORTED_TOP_K
    )
    return tuple(configurations)


@dataclass(frozen=True, slots=True)
class ExperimentQuery:
    """Retrieval input deliberately containing no relevance annotation."""

    query_id: str
    query: str
    query_sha256: str
    documents: tuple[RetrievalDocument, ...]

    def __post_init__(self) -> None:
        _identifier(self.query_id, "query_id")
        if not isinstance(self.query, str) or not self.query.strip():
            raise Int2ExperimentError("query must be non-empty")
        if (
            not isinstance(self.query_sha256, str)
            or not _SHA256_RE.fullmatch(self.query_sha256)
            or sha256(self.query.encode("utf-8")).hexdigest() != self.query_sha256
        ):
            raise Int2ExperimentError("query_sha256 must commit the exact query")
        if not isinstance(self.documents, tuple) or not self.documents:
            raise Int2ExperimentError("documents must be a non-empty tuple")
        if not all(isinstance(item, RetrievalDocument) for item in self.documents):
            raise Int2ExperimentError("documents contains an invalid document")
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise Int2ExperimentError("document IDs must be unique within a query")

    @classmethod
    def from_text(
        cls,
        *,
        query_id: str,
        query: str,
        documents: tuple[RetrievalDocument, ...],
    ) -> ExperimentQuery:
        return cls(
            query_id=query_id,
            query=query,
            query_sha256=sha256(query.encode("utf-8")).hexdigest(),
            documents=documents,
        )


@dataclass(frozen=True, slots=True)
class RelevanceAnnotation:
    """Scoring-only relevance data that must never enter a retriever request."""

    query_id: str
    relevant_evidence_ids: tuple[str, ...]
    required_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.query_id, "query_id")
        for values, name in (
            (self.relevant_evidence_ids, "relevant_evidence_ids"),
            (self.required_evidence_ids, "required_evidence_ids"),
        ):
            if not isinstance(values, tuple):
                raise Int2ExperimentError(f"{name} must be a tuple")
            if not all(isinstance(item, str) for item in values):
                raise Int2ExperimentError(f"{name} must contain strings")
            if len(values) != len(set(values)):
                raise Int2ExperimentError(f"{name} must not contain duplicates")
            for item in values:
                _identifier(item, name)
        if not set(self.required_evidence_ids).issubset(
            self.relevant_evidence_ids
        ):
            raise Int2ExperimentError(
                "required evidence must be a subset of relevant evidence"
            )


@dataclass(frozen=True, slots=True)
class RelevanceManifest:
    schema_version: str
    catalog_id: str
    annotations: tuple[RelevanceAnnotation, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise Int2ExperimentError("relevance manifest schema_version must be 1.0")
        _identifier(self.catalog_id, "catalog_id")
        if not isinstance(self.annotations, tuple) or not self.annotations:
            raise Int2ExperimentError("annotations must be a non-empty tuple")
        if not all(isinstance(item, RelevanceAnnotation) for item in self.annotations):
            raise Int2ExperimentError("annotations contains an invalid record")
        query_ids = [item.query_id for item in self.annotations]
        if len(query_ids) != len(set(query_ids)):
            raise Int2ExperimentError("relevance query IDs must be unique")

    def for_query(self, query_id: str) -> RelevanceAnnotation:
        for annotation in self.annotations:
            if annotation.query_id == query_id:
                return annotation
        raise Int2ExperimentError(f"no relevance annotation for {query_id!r}")


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    reciprocal_rank: float | None
    all_required_retrieved: bool
    rank_of_first_required: int | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.recall_at_k, "recall_at_k"),
            (self.precision_at_k, "precision_at_k"),
        ):
            if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise Int2ExperimentError(f"{name} must be within [0, 1]")
        if self.reciprocal_rank is not None and not 0.0 <= self.reciprocal_rank <= 1.0:
            raise Int2ExperimentError("reciprocal_rank must be within [0, 1] or null")
        if not isinstance(self.all_required_retrieved, bool):
            raise Int2ExperimentError("all_required_retrieved must be boolean")
        if self.rank_of_first_required is not None and self.rank_of_first_required < 1:
            raise Int2ExperimentError("rank_of_first_required must be positive or null")


@dataclass(frozen=True, slots=True)
class ExperimentRetrievalResult:
    """One configuration cell.

    `retrieval_latency_ms` times ranking, scoring, and top-k selection only.
    Embedding generation happens once per experiment run and is reported at
    experiment level on the `EmbeddingSnapshot`, never per observation.
    """

    configuration: RetrievalConfiguration
    ranked_documents: tuple[RankedDocument, ...]
    retrieval_latency_ms: float
    embedding_source: EmbeddingSource

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, RetrievalConfiguration):
            raise Int2ExperimentError("configuration is invalid")
        if not isinstance(self.ranked_documents, tuple) or not all(
            isinstance(item, RankedDocument) for item in self.ranked_documents
        ):
            raise Int2ExperimentError("ranked_documents is invalid")
        _nonnegative_number(self.retrieval_latency_ms, "retrieval_latency_ms")
        if not isinstance(self.embedding_source, EmbeddingSource):
            raise Int2ExperimentError("embedding_source must be EmbeddingSource")
        semantic = self.configuration.strategy in {
            RetrievalStrategy.SEMANTIC_ONLY,
            RetrievalStrategy.HYBRID,
        }
        expected = (
            EmbeddingSource.PRECOMPUTED if semantic else EmbeddingSource.NOT_USED
        )
        if self.embedding_source is not expected:
            raise Int2ExperimentError(
                f"{self.configuration.strategy.value} requires "
                f"embedding_source {expected.value}"
            )

    @property
    def retrieved_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            item.document.evidence_id
            for item in self.ranked_documents
            if item.document.evidence_id is not None
        )


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    query_id: str
    retrieval: ExperimentRetrievalResult
    metrics: RetrievalMetrics

    def __post_init__(self) -> None:
        _identifier(self.query_id, "query_id")
        if not isinstance(self.retrieval, ExperimentRetrievalResult):
            raise Int2ExperimentError("retrieval is invalid")
        if not isinstance(self.metrics, RetrievalMetrics):
            raise Int2ExperimentError("metrics is invalid")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    buyer_input_tokens: int | None = None
    buyer_output_tokens: int | None = None
    semantic_input_tokens: int | None = None
    semantic_output_tokens: int | None = None
    embedding_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "buyer_input_tokens",
            "buyer_output_tokens",
            "semantic_input_tokens",
            "semantic_output_tokens",
            "embedding_tokens",
        ):
            _nonnegative_int_or_none(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class CostRates:
    """Optional engineering rates; no vendor price is a product default."""

    buyer_input_cost_per_token: float | None = None
    buyer_output_cost_per_token: float | None = None
    semantic_input_cost_per_token: float | None = None
    semantic_output_cost_per_token: float | None = None
    embedding_cost_per_token: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "buyer_input_cost_per_token",
            "buyer_output_cost_per_token",
            "semantic_input_cost_per_token",
            "semantic_output_cost_per_token",
            "embedding_cost_per_token",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_number(value, name)


@dataclass(frozen=True, slots=True)
class CostEstimate:
    estimated_api_cost: float | None
    priced_categories: tuple[str, ...]
    unpriced_categories: tuple[str, ...]


def estimate_api_cost(usage: TokenUsage, rates: CostRates | None) -> CostEstimate:
    """Price only categories with both raw usage and a supplied experiment rate."""

    if not isinstance(usage, TokenUsage):
        raise TypeError("usage must be TokenUsage")
    if rates is not None and not isinstance(rates, CostRates):
        raise TypeError("rates must be CostRates or None")
    pairs = (
        ("buyer_input", usage.buyer_input_tokens, "buyer_input_cost_per_token"),
        ("buyer_output", usage.buyer_output_tokens, "buyer_output_cost_per_token"),
        ("semantic_input", usage.semantic_input_tokens, "semantic_input_cost_per_token"),
        ("semantic_output", usage.semantic_output_tokens, "semantic_output_cost_per_token"),
        ("embedding", usage.embedding_tokens, "embedding_cost_per_token"),
    )
    total = 0.0
    priced: list[str] = []
    unpriced: list[str] = []
    for category, tokens, rate_name in pairs:
        if tokens is None:
            continue
        rate = getattr(rates, rate_name) if rates is not None else None
        if rate is None:
            unpriced.append(category)
            continue
        total += tokens * rate
        priced.append(category)
    return CostEstimate(
        estimated_api_cost=total if priced else None,
        priced_categories=tuple(priced),
        unpriced_categories=tuple(unpriced),
    )


@dataclass(frozen=True, slots=True)
class SelectedRetrievalConfiguration:
    query_id: str
    configuration: RetrievalConfiguration

    def __post_init__(self) -> None:
        _identifier(self.query_id, "query_id")
        if not isinstance(self.configuration, RetrievalConfiguration):
            raise Int2ExperimentError("configuration is invalid")


@dataclass(frozen=True, slots=True)
class DownstreamSelection:
    """A recorded Stage-B selection required before semantic execution."""

    selection_id: str
    recorded_at: datetime
    selections: tuple[SelectedRetrievalConfiguration, ...]
    rationale: str

    def __post_init__(self) -> None:
        _identifier(self.selection_id, "selection_id")
        if (
            not isinstance(self.recorded_at, datetime)
            or self.recorded_at.tzinfo is None
            or self.recorded_at.utcoffset() is None
        ):
            raise Int2ExperimentError("recorded_at must be timezone-aware")
        if not isinstance(self.selections, tuple) or not self.selections:
            raise Int2ExperimentError("selections must be a non-empty tuple")
        if not all(
            isinstance(item, SelectedRetrievalConfiguration)
            for item in self.selections
        ):
            raise Int2ExperimentError("selections contains an invalid item")
        keys = [
            (item.query_id, item.configuration.configuration_id)
            for item in self.selections
        ]
        if len(keys) != len(set(keys)):
            raise Int2ExperimentError("Stage-B selections must be unique")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise Int2ExperimentError("selection rationale must be non-empty")
