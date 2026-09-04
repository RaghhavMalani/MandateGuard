"""The custom-intent journey: arbitrary text in, explained candidates out.

This is the whole discovery half of the product in one place:

    intent text
      -> deterministic constraint extraction
      -> structured filters + retrieval over the large catalog
      -> per-candidate explanation, classification, mismatch, anomaly,
         transactability
      -> a status that is honest about what is missing

A listing that came from a public crawl and carries no merchant evidence ends at
``REVIEW REQUIRED``. That is the designed outcome, not a failure: the alternative
is inventing an ``ALLOW`` for a product nobody has vouched for.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from mandateguard.discovery.anomaly import (
    AnomalyAssessment,
    CategoryPriceProfile,
    ProposalContext,
    assess,
    build_price_profiles,
)
from mandateguard.discovery.catalog import (
    CatalogUnavailableError,
    DiscoveryCatalog,
    load_catalog,
)
from mandateguard.discovery.classifier import CategoryClassifier, load_classifier
from mandateguard.discovery.index.embedding import EmbeddingIndex, load_embedding_index
from mandateguard.discovery.index.hybrid import (
    DEFAULT_ALPHA,
    DEFAULT_CANDIDATE_DEPTH,
    DEFAULT_TOP_K,
    HybridDiscoveryRetriever,
    RETRIEVAL_METHOD,
    RetrievedListing,
    StructuredFilter,
)
from mandateguard.discovery.index.lexical import LexicalIndex, load_lexical_index
from mandateguard.discovery.intent import ParsedIntent, parse_intent
from mandateguard.discovery.mismatch import MismatchSignal, evaluate_mismatch
from mandateguard.discovery.transactability import (
    TransactabilityReport,
    assess_listing,
    summarize,
)
from mandateguard.discovery.trust import DISCOVERY_ONLY_STAGES, boundary_declaration


@dataclass(frozen=True, slots=True)
class TrustedListingFacts:
    """What the authorization store knows about a listing, if anything.

    This is the only channel by which trusted knowledge reaches the discovery
    layer, and it carries counts and identities - never evidence text. The
    discovery layer must not be able to quote merchant evidence, because a
    surface that can quote it is a surface someone will eventually treat as
    having produced it.
    """

    evidence_count: int = 0
    merchant_of_record: str | None = None
    recurrence_evidenced: bool = False
    category_declared_by_merchant: bool = False


LEXICAL_INDEX_FILENAME = "lexical_index.mgdx"
EMBEDDING_INDEX_FILENAME = "embedding_index.mgdx"
CLASSIFIER_FILENAME = "category_classifier.mgdx"

#: The four stages a discovery-only listing passes through, and stops at.
STAGE_DISCOVERED, STAGE_MATCHED, STAGE_EVIDENCE, STAGE_REVIEW = DISCOVERY_ONLY_STAGES


@dataclass(frozen=True, slots=True)
class Candidate:
    """One retrieved listing with everything the product needs to explain it."""

    listing: RetrievedListing
    why_matched: tuple[str, str]
    predicted_category: str
    mismatch: MismatchSignal | None
    anomaly: AnomalyAssessment
    transactability: TransactabilityReport
    trusted_evidence_count: int
    stage: str

    @property
    def transactable(self) -> bool:
        return self.trusted_evidence_count > 0

    def to_mapping(self) -> dict[str, Any]:
        product = self.listing.product
        return {
            "catalog_product_id": product.catalog_product_id,
            "source_product_id": product.source_product_id,
            "title": product.title,
            "brand": product.brand,
            "category_path": list(product.category_path),
            "top_category": product.top_category,
            "price_minor": product.price_minor,
            "currency": product.currency,
            "listed_on": product.merchant_or_seller,
            "rating": product.rating,
            "product_url": product.product_url,
            "source": product.source,
            "trust_tier": "DISCOVERY_LISTING",
            "match": {
                "score": round(self.listing.hybrid_score, 4),
                "lexical_score": round(self.listing.lexical_score, 4),
                "dense_score": round(self.listing.dense_score, 4),
                "matched_terms": list(self.listing.matched_terms),
                "headline": self.why_matched[0],
                "detail": self.why_matched[1],
            },
            "classification": {
                "predicted_category": self.predicted_category,
                **(
                    {"mismatch": self.mismatch.to_mapping()}
                    if self.mismatch is not None
                    else {}
                ),
            },
            "anomaly": self.anomaly.to_mapping(),
            "transactability": self.transactability.to_mapping(),
            "trusted_evidence_count": self.trusted_evidence_count,
            "transactable": self.transactable,
            "stage": self.stage,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    intent: ParsedIntent
    candidates: tuple[Candidate, ...]
    considered: int
    filtered_out: Mapping[str, int]
    duplicates_suppressed: int
    retrieval_ms: float
    analysis_ms: float
    total_ms: float
    catalog_listings: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mandate": self.intent.to_mapping(),
            "mandate_plain_english": self.intent.plain_english(),
            "retrieval": {
                "method": RETRIEVAL_METHOD,
                "alpha": DEFAULT_ALPHA,
                "catalog_listings": self.catalog_listings,
                "candidates_considered": self.considered,
                "filtered_out": dict(self.filtered_out),
                "duplicates_suppressed": self.duplicates_suppressed,
                "retrieval_ms": round(self.retrieval_ms, 3),
                "analysis_ms": round(self.analysis_ms, 3),
                "total_ms": round(self.total_ms, 3),
            },
            "candidates": [item.to_mapping() for item in self.candidates],
            "summary": summarize(
                [item.transactability for item in self.candidates]
            ),
            "boundary": boundary_declaration(),
            "stages": list(DISCOVERY_ONLY_STAGES),
        }


def _filter_explanation(reason: str, intent: ParsedIntent) -> str:
    if reason == "ABOVE_PRICE_CEILING":
        ceiling = intent.max_total_minor
        return (
            f"priced above your {intent.currency} {ceiling / 100:,.0f} ceiling"
            if ceiling is not None
            else "priced above the stated ceiling"
        )
    if reason == "PRICE_NOT_PUBLISHED":
        return "no published price to check against your budget"
    if reason == "MATCHES_MANDATE_EXCLUSION":
        return "matched something you excluded"
    if reason == "CURRENCY_MISMATCH":
        return f"not priced in {intent.currency}"
    if reason == "CATEGORY_NOT_REQUESTED":
        return "outside the requested category"
    return reason.replace("_", " ").casefold()


def _why_matched(
    listing: RetrievedListing, intent: ParsedIntent
) -> tuple[str, str]:
    """Plain English for why this listing is in front of the user."""

    product = listing.product
    terms = list(listing.matched_terms)
    if terms:
        quoted = ", ".join(f"“{term}”" for term in terms[:4])
        headline = f"Matches {quoted} in its title, brand, category, or description."
    else:
        headline = "Ranked by overall similarity; no single query word matched exactly."
    parts: list[str] = []
    if product.price_minor is not None and intent.max_total_minor is not None:
        room = intent.max_unit_price_minor
        if room is not None:
            parts.append(
                f"{product.currency} {product.price_minor / 100:,.0f} is within "
                f"your {product.currency} {room / 100:,.0f} per-unit ceiling"
            )
    elif product.price_minor is not None:
        parts.append(f"listed at {product.currency} {product.price_minor / 100:,.0f}")
    parts.append(f"filed under {product.category_text}")
    if intent.exclusions:
        parts.append(
            "carries none of your excluded terms ("
            + ", ".join(intent.exclusions[:3])
            + ")"
        )
    return headline, "; ".join(parts) + "."


class DiscoveryEngine:
    """Loads the frozen artifacts once and serves discovery searches.

    Immutable after construction, so a single instance is shared across request
    threads. Loading is eager and reported: a missing artifact is an error at
    startup, not a silently empty result page at request time.
    """

    __slots__ = (
        "catalog",
        "retriever",
        "classifier",
        "price_profiles",
        "brands",
        "load_seconds",
        "index_bytes",
        "trusted_evidence_lookup",
    )

    def __init__(
        self,
        *,
        catalog: DiscoveryCatalog,
        lexical: LexicalIndex,
        embedding: EmbeddingIndex | None,
        classifier: CategoryClassifier | None,
        load_seconds: float = 0.0,
        trusted_evidence_lookup: Callable[[Any], TrustedListingFacts] | None = None,
    ) -> None:
        self.catalog = catalog
        self.retriever = HybridDiscoveryRetriever(
            lexical=lexical,
            embedding=embedding,
            product_at=lambda document_id: catalog[document_id],
        )
        self.classifier = classifier
        self.price_profiles: dict[str, CategoryPriceProfile] = build_price_profiles(
            catalog
        )
        self.brands = _frequent_brands(catalog)
        self.load_seconds = load_seconds
        self.index_bytes = lexical.index_bytes + (
            embedding.index_bytes if embedding is not None else 0
        )
        self.trusted_evidence_lookup = trusted_evidence_lookup or (
            lambda _product: TrustedListingFacts()
        )

    @classmethod
    def load(
        cls,
        *,
        processed_dir: Path,
        models_dir: Path,
        with_embedding: bool = True,
        with_classifier: bool = True,
        trusted_evidence_lookup: Callable[[Any], TrustedListingFacts] | None = None,
    ) -> DiscoveryEngine:
        started = perf_counter()
        catalog = load_catalog(processed_dir)
        lexical = load_lexical_index(Path(models_dir) / LEXICAL_INDEX_FILENAME)
        embedding = (
            load_embedding_index(Path(models_dir) / EMBEDDING_INDEX_FILENAME)
            if with_embedding
            else None
        )
        classifier = (
            load_classifier(Path(models_dir) / CLASSIFIER_FILENAME)
            if with_classifier
            else None
        )
        return cls(
            catalog=catalog,
            lexical=lexical,
            embedding=embedding,
            classifier=classifier,
            load_seconds=perf_counter() - started,
            trusted_evidence_lookup=trusted_evidence_lookup,
        )

    def parse(self, text: str) -> ParsedIntent:
        return parse_intent(text, known_brands=self.brands)

    def search(
        self,
        text: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        alpha: float = DEFAULT_ALPHA,
        candidate_depth: int = DEFAULT_CANDIDATE_DEPTH,
    ) -> DiscoveryResult:
        started = perf_counter()
        intent = self.parse(text)
        structured = StructuredFilter(
            max_unit_price_minor=intent.max_unit_price_minor,
            currency=intent.currency,
            exclusion_terms=tuple(item.casefold() for item in intent.exclusions),
        )
        retrieval_started = perf_counter()
        outcome = self.retriever.retrieve(
            query=intent.search_text or text,
            structured=structured,
            alpha=alpha,
            top_k=top_k,
            candidate_depth=candidate_depth,
        )
        retrieval_ms = (perf_counter() - retrieval_started) * 1000.0

        analysis_started = perf_counter()
        candidates = tuple(
            self._analyse(listing, intent) for listing in outcome.listings
        )
        analysis_ms = (perf_counter() - analysis_started) * 1000.0
        return DiscoveryResult(
            intent=intent,
            candidates=candidates,
            considered=outcome.candidates_considered,
            filtered_out={
                _filter_explanation(reason, intent): count
                for reason, count in outcome.filtered_out.items()
            },
            duplicates_suppressed=outcome.duplicates_suppressed,
            retrieval_ms=retrieval_ms,
            analysis_ms=analysis_ms,
            total_ms=(perf_counter() - started) * 1000.0,
            catalog_listings=len(self.catalog),
        )

    def _analyse(self, listing: RetrievedListing, intent: ParsedIntent) -> Candidate:
        product = listing.product
        facts = self.trusted_evidence_lookup(product)
        # The classifier was trained on one marketplace's taxonomy. Asking it to
        # adjudicate a merchant's own declared shelf would be asking a model
        # about a question the merchant is the authority on, so it is not asked.
        classify = (
            self.classifier is not None and not facts.category_declared_by_merchant
        )
        mismatch = (
            evaluate_mismatch(product, self.classifier)
            if classify and self.classifier is not None
            else None
        )
        anomaly = assess(
            ProposalContext(
                product=product,
                intent=intent,
                price_profile=self.price_profiles.get(product.top_category),
                mismatch=mismatch,
                trusted_evidence_count=facts.evidence_count,
                expected_merchant=None,
                consent_active=None,
            )
        )
        category_understood = facts.category_declared_by_merchant or (
            self.classifier is not None
            and product.top_category in self.classifier.classes
        )
        transactability = assess_listing(
            product,
            category_understood=category_understood,
            trusted_evidence_count=facts.evidence_count,
            merchant_of_record=facts.merchant_of_record,
            recurrence_evidenced=facts.recurrence_evidenced,
        )
        return Candidate(
            listing=listing,
            why_matched=_why_matched(listing, intent),
            predicted_category=(
                mismatch.predicted_category
                if mismatch is not None
                else (
                    "DECLARED_BY_MERCHANT"
                    if facts.category_declared_by_merchant
                    else "NOT_CLASSIFIED"
                )
            ),
            mismatch=mismatch,
            anomaly=anomaly,
            transactability=transactability,
            trusted_evidence_count=facts.evidence_count,
            # A listing with merchant evidence stops at MATCHED and can be
            # handed to the authorization controller. One without it has already
            # reached the end of what discovery can establish.
            stage=STAGE_MATCHED if facts.evidence_count > 0 else STAGE_EVIDENCE,
        )

    def statistics(self) -> dict[str, Any]:
        stats = self.catalog.statistics()
        return {
            "catalog_listings": len(self.catalog),
            "top_level_categories": stats.get("top_level_categories"),
            "distinct_category_paths": stats.get("distinct_category_paths"),
            "distinct_brands": stats.get("distinct_brands"),
            "listings_with_price": stats.get("listings_with_price"),
            "index_bytes": self.index_bytes,
            "catalog_bytes": self.catalog.source_bytes,
            "cold_load_seconds": round(self.load_seconds, 4),
            "embedding_dimensions": (
                self.retriever.embedding.dimensions
                if self.retriever.embedding is not None
                else None
            ),
            "embedding_vocabulary": (
                len(self.retriever.embedding.terms)
                if self.retriever.embedding is not None
                else None
            ),
            "lexical_terms": len(self.retriever.lexical.terms),
            "classifier_classes": (
                len(self.classifier.classes) if self.classifier is not None else None
            ),
            "provenance": self.catalog.provenance(),
        }


def _frequent_brands(catalog: DiscoveryCatalog, *, minimum: int = 5) -> tuple[str, ...]:
    """Brands common enough to be worth matching an intent against.

    A one-off brand string is far more likely to collide with an ordinary word
    in a sentence than to be what the user meant.
    """

    counts: dict[str, int] = {}
    for product in catalog:
        if product.brand and len(product.brand) >= 3:
            counts[product.brand] = counts.get(product.brand, 0) + 1
    return tuple(
        sorted(name for name, count in counts.items() if count >= minimum)
    )


def try_load(
    *, processed_dir: Path, models_dir: Path, **kwargs: Any
) -> tuple[DiscoveryEngine | None, str | None]:
    """Load the engine, or return why it is unavailable.

    The product must start and serve its authorization journeys whether or not
    the discovery artifacts are present, so this reports rather than raises.
    """

    try:
        engine = DiscoveryEngine.load(
            processed_dir=processed_dir, models_dir=models_dir, **kwargs
        )
    except (CatalogUnavailableError, OSError, ValueError, RuntimeError) as error:
        return None, str(error)
    return engine, None


def preset_intents() -> tuple[Mapping[str, str], ...]:
    """Examples, not the product. Free text is the primary path."""

    return (
        {
            "id": "headphones",
            "label": "HEADPHONES UNDER 5000",
            "intent": "Buy wired headphones under Rs 5000. One-time payment only.",
        },
        {
            "id": "desk-lamp",
            "label": "DESK LAMP, NO SUBSCRIPTION",
            "intent": "Get a desk lamp below Rs 1500 and no subscriptions.",
        },
        {
            "id": "backpack",
            "label": "SCHOOL BACKPACK",
            "intent": "School backpack for kids below Rs 1500, nothing branded luxury.",
        },
    )


def candidates_for_display(result: DiscoveryResult) -> Sequence[Mapping[str, Any]]:
    return [item.to_mapping() for item in result.candidates]
