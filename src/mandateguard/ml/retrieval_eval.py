"""Retrieval quality against the human-authored query set.

Sweeps the lexical/dense blend and reports Recall@5, Recall@10, MRR, latency,
and result diversity for every point, with and without near-duplicate
suppression. It reports what each configuration does rather than asserting which
is better, and it is explicit that this measures *retrieval*, never
authorization accuracy.

Near-duplicate suppression trades measured recall for measured diversity: the
relevance predicates count each duplicate listing as separately relevant, so
collapsing four copies of one bracelet costs recall while making the answer more
useful. Both halves of that trade are in the report.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Mapping, Sequence

from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.index.hybrid import (
    HybridDiscoveryRetriever,
    StructuredFilter,
)
from mandateguard.discovery.intent import parse_intent
from mandateguard.discovery.schema import DiscoveryProduct


EVALUATION_VERSION = "discovery-retrieval-eval-v1"

#: Retrieval quality is not authorization quality. Naming it here so a number
#: lifted out of the report carries the disclaimer with it.
NOT_A_SAFETY_METRIC = (
    "These are retrieval metrics over a product catalog. They say nothing about "
    "authorization correctness, and no retrieval score has ever moved a "
    "MandateGuard decision."
)

CONFIGURATIONS: tuple[tuple[str, float], ...] = (
    ("lexical_only_alpha_1.00", 1.0),
    ("hybrid_alpha_0.90", 0.9),
    ("hybrid_alpha_0.70", 0.7),
    ("hybrid_alpha_0.50", 0.5),
    ("hybrid_alpha_0.30", 0.3),
    ("dense_only_alpha_0.00", 0.0),
)


def _haystack(product: DiscoveryProduct) -> str:
    return (
        f"{product.title} {product.category_text} "
        f"{product.brand or ''} {product.description}"
    ).casefold()


def relevant_documents(
    catalog: DiscoveryCatalog, predicate: Mapping[str, Any]
) -> frozenset[int]:
    """Apply one authored relevance predicate across the whole catalog."""

    require_all = [term.casefold() for term in predicate.get("require_all_terms", ())]
    require_any = [term.casefold() for term in predicate.get("require_any_terms", ())]
    require_title = [term.casefold() for term in predicate.get("require_title_any", ())]
    exclude = [term.casefold() for term in predicate.get("exclude_terms", ())]
    categories = frozenset(predicate.get("categories", ()) or ())
    ceiling = predicate.get("max_price_minor")
    hits: set[int] = set()
    for document_id, product in enumerate(catalog):
        if categories and product.top_category not in categories:
            continue
        if ceiling is not None and (
            product.price_minor is None or product.price_minor > ceiling
        ):
            continue
        blob = _haystack(product)
        title = product.title.casefold()
        if any(term in blob for term in exclude):
            continue
        if require_all and not all(term in blob for term in require_all):
            continue
        if require_any and not any(term in blob for term in require_any):
            continue
        if require_title and not any(term in title for term in require_title):
            continue
        hits.add(document_id)
    return frozenset(hits)


@dataclass(frozen=True, slots=True)
class QueryResult:
    query_id: str
    family: str
    relevant_count: int
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    success_at_10: bool
    reciprocal_rank: float
    latency_ms: float
    distinct_title_fraction: float
    duplicates_suppressed: int


def _score_ranking(
    ranking: Sequence[int], relevant: frozenset[int]
) -> tuple[float, float, float, bool, float]:
    if not relevant:
        return 0.0, 0.0, 0.0, False, 0.0
    top5 = ranking[:5]
    top10 = ranking[:10]
    hits5 = sum(1 for item in top5 if item in relevant)
    hits10 = sum(1 for item in top10 if item in relevant)
    # Capped recall: with a broad relevance set, no ranking of length k could
    # ever retrieve more than k relevant items, so the denominator is capped.
    recall5 = hits5 / min(len(relevant), 5)
    recall10 = hits10 / min(len(relevant), 10)
    precision5 = hits5 / max(1, len(top5))
    reciprocal = 0.0
    for position, document_id in enumerate(ranking, start=1):
        if document_id in relevant:
            reciprocal = 1.0 / position
            break
    return recall5, recall10, precision5, hits10 > 0, reciprocal


def evaluate_retrieval(
    catalog: DiscoveryCatalog,
    retriever: HybridDiscoveryRetriever,
    query_set: Mapping[str, Any],
    *,
    candidate_depth: int = 300,
) -> dict[str, Any]:
    queries = query_set.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("query set contains no queries")
    brands = sorted({product.brand for product in catalog if product.brand})
    relevance: dict[str, frozenset[int]] = {}
    for entry in queries:
        relevance[entry["query_id"]] = relevant_documents(catalog, entry["relevance"])
    empty = [key for key, value in relevance.items() if not value]
    if empty:
        raise ValueError(f"queries with no relevant listing in this catalog: {empty}")

    per_configuration: dict[str, Any] = {}
    for name, alpha in CONFIGURATIONS:
        for deduplicate in (False, True):
            results: list[QueryResult] = []
            for entry in queries:
                parsed = parse_intent(entry["text"], known_brands=brands)
                structured = StructuredFilter(
                    max_unit_price_minor=parsed.max_unit_price_minor,
                    currency=parsed.currency,
                    exclusion_terms=tuple(
                        item.casefold() for item in parsed.exclusions
                    ),
                )
                started = perf_counter()
                outcome = retriever.retrieve(
                    query=parsed.search_text or entry["text"],
                    structured=structured,
                    alpha=alpha,
                    top_k=10,
                    candidate_depth=candidate_depth,
                    deduplicate=deduplicate,
                )
                latency = (perf_counter() - started) * 1000.0
                ranking = [item.document_id for item in outcome.listings]
                recall5, recall10, precision5, success, reciprocal = _score_ranking(
                    ranking, relevance[entry["query_id"]]
                )
                titles = {
                    item.product.title.casefold() for item in outcome.listings
                }
                results.append(
                    QueryResult(
                        query_id=entry["query_id"],
                        family=entry["family"],
                        relevant_count=len(relevance[entry["query_id"]]),
                        recall_at_5=recall5,
                        recall_at_10=recall10,
                        precision_at_5=precision5,
                        success_at_10=success,
                        reciprocal_rank=reciprocal,
                        latency_ms=latency,
                        distinct_title_fraction=(
                            len(titles) / len(outcome.listings)
                            if outcome.listings
                            else 0.0
                        ),
                        duplicates_suppressed=outcome.duplicates_suppressed,
                    )
                )
            suffix = "deduplicated" if deduplicate else "raw"
            per_configuration[f"{name}__{suffix}"] = _summarize(
                f"{name}__{suffix}", alpha, results, deduplicate=deduplicate
            )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "query_set_version": query_set.get("query_set_version"),
        "catalog_sha256": catalog.catalog_sha256,
        "catalog_listings": len(catalog),
        "queries": len(queries),
        "candidate_depth": candidate_depth,
        "disclaimer": NOT_A_SAFETY_METRIC,
        "configurations": per_configuration,
        "relevant_set_sizes": {
            key: len(value) for key, value in sorted(relevance.items())
        },
    }


def _summarize(
    name: str,
    alpha: float,
    results: Sequence[QueryResult],
    *,
    deduplicate: bool = False,
) -> dict[str, Any]:
    def mean(values: Sequence[float]) -> float:
        return round(sum(values) / len(values), 6) if values else 0.0

    families: dict[str, Any] = {}
    for family in sorted({item.family for item in results}):
        subset = [item for item in results if item.family == family]
        families[family] = {
            "queries": len(subset),
            "recall_at_5": mean([item.recall_at_5 for item in subset]),
            "recall_at_10": mean([item.recall_at_10 for item in subset]),
            "mrr": mean([item.reciprocal_rank for item in subset]),
        }
    latencies = sorted(item.latency_ms for item in results)
    return {
        "alpha": alpha,
        "name": name,
        "deduplicated": deduplicate,
        "queries": len(results),
        "recall_at_5": mean([item.recall_at_5 for item in results]),
        "recall_at_10": mean([item.recall_at_10 for item in results]),
        "precision_at_5": mean([item.precision_at_5 for item in results]),
        "success_at_10": mean([1.0 if item.success_at_10 else 0.0 for item in results]),
        "mrr": mean([item.reciprocal_rank for item in results]),
        "distinct_title_fraction": mean(
            [item.distinct_title_fraction for item in results]
        ),
        "mean_duplicates_suppressed": mean(
            [float(item.duplicates_suppressed) for item in results]
        ),
        "by_family": families,
        "latency_ms": {
            "median": round(median(latencies), 3),
            "p95": round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 3),
        },
        "per_query": [
            {
                "query_id": item.query_id,
                "family": item.family,
                "relevant": item.relevant_count,
                "recall_at_10": round(item.recall_at_10, 4),
                "reciprocal_rank": round(item.reciprocal_rank, 4),
            }
            for item in results
        ],
    }


def load_query_set(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
