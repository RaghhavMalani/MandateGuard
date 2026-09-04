"""Hybrid retrieval over the large discovery catalog.

The pipeline is retrieve-then-rerank, chosen so the dense stage stays cheap
enough for pure Python:

1. **Structured filters** - price ceiling, currency, category, and mandate
   exclusions. Deterministic, applied first, never traded off against a score.
2. **Lexical candidate generation** - BM25 over the frozen inverted index,
   producing ``candidate_depth`` documents.
3. **Dense rerank** - cosine similarity in the frozen LSA space, computed only
   for those candidates.
4. **Hybrid score** - ``alpha * lexical + (1 - alpha) * dense``, with both terms
   min-max normalized within the candidate set so alpha means what it says.
5. **Near-duplicate suppression** - a candidate that is the same product as one
   already selected is skipped, using document-to-document similarity in the
   frozen embedding space.

Step 1 is not a ranking signal. A listing above the stated price ceiling is not
"ranked lower"; it is not a candidate, because the ceiling came from the user.

What the frozen evaluation actually found
-----------------------------------------
On this catalog the dense contribution to *ranking* is not an improvement: the
alpha sweep is monotone in the wrong direction, and the paraphrase family scores
near zero for every blend. So ``DEFAULT_ALPHA`` is 1.0, and what the embedding
index actually earns its place with is step 5, where it raises the fraction of
distinct products in a top-8 result from 0.82 to 1.00. A dense full-scan
*fallback* was considered and deliberately not shipped: the lexical vocabulary
(min_df=1) is a strict superset of the embedding vocabulary (min_df=3) over the
same corpus and analyzer, so a query the dense index could encode is always a
query BM25 has postings for, and the fallback could never execute. The numbers,
including the negative ones, are in `docs/DISCOVERY_RETRIEVAL.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from mandateguard.discovery.index.analyzer import analyze, analyze_unique
from mandateguard.discovery.index.embedding import EmbeddingIndex
from mandateguard.discovery.index.lexical import LexicalIndex
from mandateguard.discovery.schema import DiscoveryProduct


#: Selected by the frozen retrieval evaluation, not by preference. On this
#: catalog the sweep over alpha in {0.0 .. 1.0} put every dense contribution at
#: or below the lexical baseline, so the default blend is lexical. The dense
#: index still runs: it suppresses near-duplicate results, which is measurable.
#: See docs/DISCOVERY_RETRIEVAL.md.
DEFAULT_ALPHA = 1.0
DEFAULT_TOP_K = 8
DEFAULT_CANDIDATE_DEPTH = 300
#: Two listings this close in the frozen LSA space, whose titles also agree,
#: are the same product twice. Both conditions are required - see
#: ``_is_duplicate`` for why one is not enough.
DEFAULT_DUPLICATE_SIMILARITY = 0.985
#: Jaccard overlap of the two titles' analyzed tokens.
TITLE_AGREEMENT = 0.6
RETRIEVAL_METHOD = "BM25_WITH_STRUCTURED_FILTERS_AND_FROZEN_LSA_RERANK"


@dataclass(frozen=True, slots=True)
class StructuredFilter:
    """Hard, deterministic constraints applied before any score is compared."""

    max_unit_price_minor: int | None = None
    currency: str | None = None
    categories: tuple[str, ...] = ()
    exclusion_terms: tuple[str, ...] = ()
    require_price: bool = False

    def rejection(self, product: DiscoveryProduct) -> str | None:
        """Why this listing is not a candidate, or ``None`` if it is one."""

        if self.currency and product.currency != self.currency:
            return "CURRENCY_MISMATCH"
        if self.require_price and product.price_minor is None:
            return "PRICE_NOT_PUBLISHED"
        if self.max_unit_price_minor is not None:
            if product.price_minor is None:
                return "PRICE_NOT_PUBLISHED"
            if product.price_minor > self.max_unit_price_minor:
                return "ABOVE_PRICE_CEILING"
        if self.categories and product.top_category not in self.categories:
            return "CATEGORY_NOT_REQUESTED"
        if self.exclusion_terms:
            haystack = (
                f"{product.title} {product.category_text} "
                f"{product.brand or ''} {product.description}"
            ).casefold()
            for term in self.exclusion_terms:
                if term and term in haystack:
                    return "MATCHES_MANDATE_EXCLUSION"
        return None


@dataclass(frozen=True, slots=True)
class RetrievedListing:
    document_id: int
    product: DiscoveryProduct
    lexical_score: float
    dense_score: float
    hybrid_score: float
    matched_terms: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "catalog_product_id": self.product.catalog_product_id,
            "lexical_score": round(self.lexical_score, 6),
            "dense_score": round(self.dense_score, 6),
            "hybrid_score": round(self.hybrid_score, 6),
            "matched_terms": list(self.matched_terms),
        }


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    listings: tuple[RetrievedListing, ...]
    query_terms: tuple[str, ...]
    candidates_considered: int
    filtered_out: Mapping[str, int]
    alpha: float
    top_k: int
    candidate_depth: int
    lexical_ms: float
    dense_ms: float
    total_ms: float
    dense_available: bool
    duplicates_suppressed: int = 0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "method": RETRIEVAL_METHOD,
            "query_terms": list(self.query_terms),
            "candidates_considered": self.candidates_considered,
            "filtered_out": dict(self.filtered_out),
            "alpha": self.alpha,
            "top_k": self.top_k,
            "candidate_depth": self.candidate_depth,
            "duplicates_suppressed": self.duplicates_suppressed,
            "lexical_ms": round(self.lexical_ms, 3),
            "dense_ms": round(self.dense_ms, 3),
            "total_ms": round(self.total_ms, 3),
            "dense_available": self.dense_available,
            "listings": [item.to_mapping() for item in self.listings],
        }


def _normalize(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high - low <= 1e-12:
        return [1.0 if high > 0.0 else 0.0 for _ in values]
    span = high - low
    return [(value - low) / span for value in values]


@dataclass(frozen=True, slots=True)
class HybridDiscoveryRetriever:
    """Frozen-index retriever. Holds no mutable state; safe to share threads."""

    lexical: LexicalIndex
    embedding: EmbeddingIndex | None
    product_at: Callable[[int], DiscoveryProduct]

    def retrieve(
        self,
        *,
        query: str,
        structured: StructuredFilter | None = None,
        alpha: float = DEFAULT_ALPHA,
        top_k: int = DEFAULT_TOP_K,
        candidate_depth: int = DEFAULT_CANDIDATE_DEPTH,
        deduplicate: bool = True,
        duplicate_similarity: float = DEFAULT_DUPLICATE_SIMILARITY,
    ) -> RetrievalOutcome:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
            raise TypeError("alpha must be numeric")
        if not 0.0 <= float(alpha) <= 1.0:
            raise ValueError("alpha must be within [0, 1]")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if (
            isinstance(candidate_depth, bool)
            or not isinstance(candidate_depth, int)
            or candidate_depth < top_k
        ):
            raise ValueError("candidate_depth must be an integer of at least top_k")
        started = perf_counter()
        filters = structured or StructuredFilter()
        terms = analyze_unique(query)
        query_terms = tuple(terms)

        lexical_started = perf_counter()
        # Over-fetch so that filtering does not starve the candidate set.
        raw = self.lexical.score(terms, limit=candidate_depth * 4) if terms else []
        rejected: dict[str, int] = {}
        candidates: list[tuple[int, float]] = []
        for document_id, score in raw:
            reason = filters.rejection(self.product_at(document_id))
            if reason is not None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            candidates.append((document_id, score))
            if len(candidates) >= candidate_depth:
                break
        lexical_ms = (perf_counter() - lexical_started) * 1000.0

        dense_started = perf_counter()
        query_vector = (
            self.embedding.encode_terms(terms)
            if self.embedding is not None and terms
            else None
        )
        dense_scores = [0.0] * len(candidates)
        if query_vector is not None:
            embedding = self.embedding
            assert embedding is not None
            dense_scores = [
                embedding.similarity(query_vector, document_id)
                for document_id, _ in candidates
            ]
        dense_ms = (perf_counter() - dense_started) * 1000.0

        lexical_normalized = _normalize([score for _, score in candidates])
        dense_normalized = _normalize(dense_scores)
        blend = float(alpha) if query_vector is not None else 1.0
        scored: list[tuple[float, int, int]] = []
        for position, (document_id, _) in enumerate(candidates):
            hybrid = (
                blend * lexical_normalized[position]
                + (1.0 - blend) * dense_normalized[position]
            )
            scored.append((hybrid, document_id, position))
        scored.sort(key=lambda item: (-item[0], item[1]))

        suppress = (
            deduplicate
            and self.embedding is not None
            and 0.0 < float(duplicate_similarity) <= 1.0
        )
        listings: list[RetrievedListing] = []
        suppressed = 0
        for hybrid, document_id, position in scored:
            if len(listings) >= top_k:
                break
            if suppress and self._is_duplicate(
                document_id, listings, float(duplicate_similarity)
            ):
                suppressed += 1
                continue
            product = self.product_at(document_id)
            listings.append(
                RetrievedListing(
                    document_id=document_id,
                    product=product,
                    lexical_score=lexical_normalized[position],
                    dense_score=dense_scores[position],
                    hybrid_score=hybrid,
                    matched_terms=tuple(
                        term for term in terms if term in _document_terms(product)
                    ),
                )
            )
        return RetrievalOutcome(
            listings=tuple(listings),
            query_terms=query_terms,
            candidates_considered=len(candidates),
            filtered_out=rejected,
            alpha=blend,
            top_k=top_k,
            candidate_depth=candidate_depth,
            lexical_ms=lexical_ms,
            dense_ms=dense_ms,
            total_ms=(perf_counter() - started) * 1000.0,
            dense_available=query_vector is not None,
            duplicates_suppressed=suppressed,
        )

    def _is_duplicate(
        self,
        document_id: int,
        selected: Sequence[RetrievedListing],
        threshold: float,
    ) -> bool:
        """Is this listing the same product as one already shown?

        A catalog crawl repeats the same product across sizes, colours, and
        seller listings. Showing eight of them is a worse answer than showing
        eight different products.

        Two independent signals have to agree before anything is suppressed: the
        embedding must place the listings almost on top of each other, *and*
        their titles must substantially overlap. Requiring both means a
        degenerate or badly fitted embedding - one that maps everything to the
        same direction - can hide a genuinely different product from the user,
        which is a worse failure than showing a duplicate. An exact title match
        is sufficient on its own.
        """

        embedding = self.embedding
        if embedding is None or not selected:
            return False
        product = self.product_at(document_id)
        title = product.title.casefold()
        tokens = frozenset(analyze(product.title))
        for chosen in selected:
            if title == chosen.product.title.casefold():
                return True
            if (
                embedding.document_similarity(document_id, chosen.document_id)
                < threshold
            ):
                continue
            other = frozenset(analyze(chosen.product.title))
            if not tokens or not other:
                continue
            overlap = len(tokens & other) / len(tokens | other)
            if overlap >= TITLE_AGREEMENT:
                return True
        return False


def _document_terms(product: DiscoveryProduct) -> frozenset[str]:
    return frozenset(analyze(product.indexed_text()))
