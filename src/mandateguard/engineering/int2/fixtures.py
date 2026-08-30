"""Strict loaders for separate INT-2 query and relevance engineering data."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from mandateguard.engineering.int2.models import (
    ExperimentQuery,
    Int2ExperimentError,
    RelevanceAnnotation,
    RelevanceManifest,
)
from mandateguard.intelligence.models import RetrievalDocument, RetrievalSource
from mandateguard.intelligence.store import TrustedCommerceStore


_QUERY_ROOT_FIELDS = frozenset({"schema_version", "catalog_id", "queries"})
_QUERY_FIELDS = frozenset({"query_id", "merchant_id", "sku", "query"})
_RELEVANCE_ROOT_FIELDS = frozenset(
    {"schema_version", "catalog_id", "annotations"}
)
_ANNOTATION_FIELDS = frozenset(
    {"query_id", "relevant_evidence_ids", "required_evidence_ids"}
)
_FORBIDDEN_VERDICT_FIELDS = frozenset(
    {
        "verdict",
        "semantic_verdict",
        "final_action",
        "expected_action",
        "ground_truth",
        "benchmark_label",
    }
)


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _decode(path: Path) -> object:
    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    try:
        raw = path.read_text(encoding="utf-8")
        decoded = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_json_number,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise Int2ExperimentError(f"cannot parse strict INT-2 JSON {path}") from error
    forbidden = _find_fields(decoded, _FORBIDDEN_VERDICT_FIELDS)
    if forbidden:
        raise Int2ExperimentError(
            "INT-2 retrieval data contains forbidden verdict fields: "
            + ",".join(sorted(forbidden))
        )
    return decoded


def _find_fields(value: object, names: frozenset[str]) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in names:
                found.add(key)
            found.update(_find_fields(item, names))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_fields(item, names))
    return frozenset(found)


def _exact_fields(
    value: object,
    expected: frozenset[str],
    location: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != expected:
        raise Int2ExperimentError(f"{location} has unexpected or missing fields")
    return value


@dataclass(frozen=True, slots=True)
class RetrievalQuerySpec:
    query_id: str
    merchant_id: str
    sku: str
    query: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.query_id, "query_id"),
            (self.merchant_id, "merchant_id"),
            (self.sku, "sku"),
            (self.query, "query"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise Int2ExperimentError(f"{name} must be non-empty")


@dataclass(frozen=True, slots=True)
class RetrievalQueryCorpus:
    schema_version: str
    catalog_id: str
    queries: tuple[RetrievalQuerySpec, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise Int2ExperimentError("query corpus schema_version must be 1.0")
        if not isinstance(self.catalog_id, str) or not self.catalog_id:
            raise Int2ExperimentError("catalog_id must be non-empty")
        if not isinstance(self.queries, tuple) or not self.queries:
            raise Int2ExperimentError("queries must be a non-empty tuple")
        query_ids = [item.query_id for item in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise Int2ExperimentError("query IDs must be unique")


def load_query_corpus(path: Path) -> RetrievalQueryCorpus:
    decoded = _exact_fields(_decode(path), _QUERY_ROOT_FIELDS, "query_corpus")
    raw_queries = decoded["queries"]
    if not isinstance(raw_queries, list) or not raw_queries:
        raise Int2ExperimentError("query_corpus.queries must be non-empty")
    queries: list[RetrievalQuerySpec] = []
    for index, raw_query in enumerate(raw_queries):
        query = _exact_fields(
            raw_query, _QUERY_FIELDS, f"query_corpus.queries[{index}]"
        )
        queries.append(
            RetrievalQuerySpec(
                query_id=query["query_id"],
                merchant_id=query["merchant_id"],
                sku=query["sku"],
                query=query["query"],
            )
        )
    return RetrievalQueryCorpus(
        schema_version=decoded["schema_version"],
        catalog_id=decoded["catalog_id"],
        queries=tuple(queries),
    )


def load_relevance_manifest(path: Path) -> RelevanceManifest:
    decoded = _exact_fields(
        _decode(path), _RELEVANCE_ROOT_FIELDS, "relevance_manifest"
    )
    raw_annotations = decoded["annotations"]
    if not isinstance(raw_annotations, list) or not raw_annotations:
        raise Int2ExperimentError("relevance_manifest.annotations must be non-empty")
    annotations: list[RelevanceAnnotation] = []
    for index, raw_annotation in enumerate(raw_annotations):
        annotation = _exact_fields(
            raw_annotation,
            _ANNOTATION_FIELDS,
            f"relevance_manifest.annotations[{index}]",
        )
        relevant = annotation["relevant_evidence_ids"]
        required = annotation["required_evidence_ids"]
        if not isinstance(relevant, list) or not isinstance(required, list):
            raise Int2ExperimentError("relevance evidence IDs must be arrays")
        annotations.append(
            RelevanceAnnotation(
                query_id=annotation["query_id"],
                relevant_evidence_ids=tuple(relevant),
                required_evidence_ids=tuple(required),
            )
        )
    return RelevanceManifest(
        schema_version=decoded["schema_version"],
        catalog_id=decoded["catalog_id"],
        annotations=tuple(annotations),
    )


def build_experiment_queries(
    corpus: RetrievalQueryCorpus,
    store: TrustedCommerceStore,
) -> tuple[ExperimentQuery, ...]:
    """Build retriever inputs without accepting a relevance manifest argument."""

    if not isinstance(corpus, RetrievalQueryCorpus):
        raise TypeError("corpus must be RetrievalQueryCorpus")
    if not isinstance(store, TrustedCommerceStore):
        raise TypeError("store must be TrustedCommerceStore")
    queries: list[ExperimentQuery] = []
    for spec in corpus.queries:
        store.get_product(merchant_id=spec.merchant_id, sku=spec.sku)
        documents = tuple(
            RetrievalDocument(
                document_id=f"evidence.{entry.evidence_id}",
                source_type=RetrievalSource.MERCHANT_EVIDENCE,
                text=entry.text,
                merchant_id=entry.merchant_id,
                sku=entry.sku,
                evidence_id=entry.evidence_id,
            )
            for entry in store.evidence_entries
            if entry.merchant_id == spec.merchant_id
        )
        queries.append(
            ExperimentQuery.from_text(
                query_id=spec.query_id,
                query=spec.query,
                documents=documents,
            )
        )
    return tuple(queries)
