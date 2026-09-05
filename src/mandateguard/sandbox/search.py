"""Retrieval over the sandbox catalogue, and an honest account of a miss.

This is the agent's discovery step. It ranks; it never decides. A candidate that
comes back top of the list has no privileged standing at the authorization gate,
and a candidate the ranker disliked is not thereby unsafe.

Two behaviours matter for the person using it.

**An ordinary request should find ordinary things.** The ranker combines a
lexical signal over the listing text with a category signal built from the
category synonym tables, so "study lamp", "reading light" and "desk lamp" all
reach the same shelf without an embedding model or a network call.

**A miss should explain itself.** When no candidate satisfies every stated
constraint the search does not return an empty list and a shrug: it returns the
closest listings it did find and names the single constraint that excluded each
one, so the person can see whether to change the budget or change the ask.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from math import log
import re
from typing import Any

from mandateguard.sandbox.intent import SandboxIntent
from mandateguard.sandbox.templates import CATEGORIES
from mandateguard.sandbox.universe import SandboxProduct, SandboxUniverse


_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Words that carry no retrieval signal in a shopping instruction.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "for",
        "from", "get", "give", "have", "help", "i", "in", "is", "it", "its", "me",
        "my", "need", "of", "on", "or", "our", "please", "should", "so", "some",
        "something", "that", "the", "their", "them", "then", "there", "these",
        "they", "this", "to", "under", "up", "us", "want", "was", "we", "what",
        "which", "will", "with", "would", "you", "your",
    }
)

#: Field weights. A match in the product name is worth more than the same word
#: appearing in a boilerplate description, and a category-synonym hit is worth
#: most of all because it identifies the shelf rather than one item on it.
_WEIGHT_NAME = 6.0
_WEIGHT_BRAND = 4.0
_WEIGHT_KEYWORD = 3.0
_WEIGHT_DESCRIPTION = 1.0
_WEIGHT_CATEGORY = 40.0
_WEIGHT_EXACT_PHRASE = 18.0

MAX_RESULTS = 10
MIN_RESULTS = 5

#: Explicitly absent product families that are easy for a shared word to
#: misroute (for example, a smartphone request containing "camera"). These
#: rules are retrieval-only. They do not create mandate constraints and never
#: reach authorization.
_ABSENT_FAMILY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:smartphone|android phone|mobile phone|iphone)\b", re.I), "smartphones"),
    (re.compile(r"\b(?:microwave|microwave oven)\b", re.I), "microwave ovens"),
    (re.compile(r"\b(?:gaming console|game console|playstation|xbox)\b", re.I), "gaming consoles"),
    (re.compile(r"\b(?:baby stroller|stroller|pram)\b", re.I), "baby strollers"),
    (re.compile(r"\b(?:dog food|cat food|pet food)\b", re.I), "pet food"),
    (re.compile(r"\b(?:camping tent|two person tent|tent)\b", re.I), "camping tents"),
    (re.compile(r"\b(?:bicycle|commuter bike|road bike)\b", re.I), "bicycles"),
    (re.compile(r"\b(?:refrigerator|fridge)\b", re.I), "refrigerators"),
    (re.compile(r"\b(?:washing machine|washer)\b", re.I), "washing machines"),
    (re.compile(r"\b(?:garden hose|watering hose)\b", re.I), "garden hoses"),
)


@dataclass(frozen=True, slots=True)
class ProductFamilyIntent:
    """A high-confidence discovery family, with authorization authority NONE."""

    label: str
    category_ids: tuple[str, ...]
    matched_phrase: str
    available: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "category_ids": list(self.category_ids),
            "matched_phrase": self.matched_phrase,
            "confidence": "HIGH",
            "available_in_sandbox": self.available,
            "authority": "RETRIEVAL_ONLY_NONE_FOR_AUTHORIZATION",
        }


def tokens(text: str) -> list[str]:
    return [item for item in _TOKEN_RE.findall(text.lower()) if item not in _STOPWORDS]


def _phrases(text: str) -> set[str]:
    """Unigrams and bigrams, so "desk lamp" can beat "desk" plus "lamp"."""

    words = _TOKEN_RE.findall(text.lower())
    result = {word for word in words if word not in _STOPWORDS}
    result.update(f"{first} {second}" for first, second in zip(words, words[1:]))
    return result


def _category_scores(query: str) -> dict[str, float]:
    """How strongly the query names each category, by synonym and label match."""

    present = _phrases(query)
    scores: dict[str, float] = {}
    for category in CATEGORIES:
        best = 0.0
        for synonym in (category.label.lower(), *category.synonyms):
            if synonym in present:
                # A two-word synonym is a far more specific claim than a
                # one-word one: "running shoes" means this shelf, "shoes"
                # merely leans towards it.
                best = max(best, 1.0 if " " in synonym else 0.6)
        if best:
            scores[category.category_id] = best
    return scores


def infer_product_family(
    query: str, category_scores: dict[str, float] | None = None
) -> ProductFamilyIntent | None:
    """Infer a deterministic shelf only where the words make it unambiguous.

    A named absent family wins before catalogue synonyms are considered. This
    prevents a secondary attribute such as "camera" in a smartphone request
    from becoming the requested product. Present families come from the same
    frozen synonym vocabulary used by ranking, so the guard and ranker cannot
    drift into competing taxonomies.
    """

    for pattern, label in _ABSENT_FAMILY_PATTERNS:
        match = pattern.search(query)
        if match is not None:
            return ProductFamilyIntent(
                label=label,
                category_ids=(),
                matched_phrase=match.group(0).lower(),
                available=False,
            )

    words = set(tokens(query))
    normalized_query = " ".join(_TOKEN_RE.findall(query.lower()))
    if "for my desk" in normalized_query:
        return ProductFamilyIntent(
            label="Office accessories",
            category_ids=("office-accessories",),
            matched_phrase="for my desk",
            available=True,
        )
    if "light" in words and words.intersection({"reading", "study", "night"}):
        return ProductFamilyIntent(
            label="Desk lamps",
            category_ids=("lighting-desk-lamps",),
            matched_phrase="light + reading/study",
            available=True,
        )
    if words.intersection({"clean", "cleaning", "cleaner", "mop"}) and words.intersection(
        {"floor", "room", "surface", "bathroom", "tiles"}
    ):
        return ProductFamilyIntent(
            label="Cleaning and home care",
            category_ids=("cleaning-products",),
            matched_phrase="cleaning + home surface",
            available=True,
        )

    scores = category_scores if category_scores is not None else _category_scores(query)
    if not scores:
        return None
    strongest = max(scores.values())
    category_ids = tuple(sorted(key for key, score in scores.items() if score == strongest))
    labels = {
        category.category_id: category.label
        for category in CATEGORIES
        if category.category_id in category_ids
    }
    label = " / ".join(labels[item] for item in category_ids)
    matched = []
    present = _phrases(query)
    for category in CATEGORIES:
        if category.category_id not in category_ids:
            continue
        matched.extend(
            synonym
            for synonym in (category.label.lower(), *category.synonyms)
            if synonym in present
        )
    matched_phrase = sorted(set(matched), key=lambda item: (-len(item), item))[0]
    return ProductFamilyIntent(
        label=label,
        category_ids=category_ids,
        matched_phrase=matched_phrase,
        available=True,
    )


@dataclass(frozen=True, slots=True)
class SearchSignal:
    """Why one listing came back, in terms a person can check."""

    category_match: str | None
    matched_terms: tuple[str, ...]
    brand_match: str | None
    within_budget: bool
    lexical_score: float
    category_score: float
    exact_phrase_match: str | None
    total_score: float

    def to_mapping(self) -> dict[str, Any]:
        return {
            "category_match": self.category_match,
            "matched_terms": list(self.matched_terms),
            "brand_match": self.brand_match,
            "within_budget": self.within_budget,
            "lexical_score": round(self.lexical_score, 4),
            "category_score": round(self.category_score, 4),
            "ranking_method": "LEXICAL_FIELD_WEIGHTED_PLUS_CATEGORY_INTENT_GUARD",
            "exact_phrase_match": self.exact_phrase_match,
            "total_score": round(self.total_score, 4),
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    product: SandboxProduct
    signal: SearchSignal


@dataclass(frozen=True, slots=True)
class NearMiss:
    """A listing that ranked well but failed one stated constraint."""

    product: SandboxProduct
    excluded_by: str
    explanation: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            **self.product.public_mapping(),
            "excluded_by": self.excluded_by,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class SearchResult:
    candidates: tuple[Candidate, ...]
    near_misses: tuple[NearMiss, ...]
    considered: int
    matched_categories: tuple[str, ...]
    product_family: ProductFamilyIntent | None


class SandboxSearch:
    """A small inverted index over the generated catalogue.

    Built once per universe. Pure standard library, no artefact on disk, and no
    network: the sandbox has to work on a free-tier container with nothing
    downloaded.
    """

    __slots__ = ("_universe", "_postings", "_documents", "_document_count", "_by_id")

    def __init__(self, universe: SandboxUniverse) -> None:
        self._universe = universe
        self._documents: dict[str, dict[str, float]] = {}
        self._postings: dict[str, set[str]] = {}
        self._by_id: dict[str, SandboxProduct] = {
            product.catalog_product_id: product for product in universe.products
        }
        for product in universe.products:
            weighted: dict[str, float] = {}
            for token in tokens(product.name):
                weighted[token] = weighted.get(token, 0.0) + _WEIGHT_NAME
            for token in tokens(product.brand):
                weighted[token] = weighted.get(token, 0.0) + _WEIGHT_BRAND
            # A category may publish several phrases containing the same word
            # ("laptop stand", "laptop sleeve", "laptop accessories"). That
            # word is one category hint, not three independent observations.
            # Counting it once prevents an accessory from beating a laptop for
            # the plain query "laptop" merely because its synonym list repeats
            # the token more often.
            keyword_tokens = {
                token for keyword in product.keywords for token in tokens(keyword)
            }
            for token in keyword_tokens:
                weighted[token] = weighted.get(token, 0.0) + _WEIGHT_KEYWORD
            for token in tokens(product.description):
                weighted[token] = weighted.get(token, 0.0) + _WEIGHT_DESCRIPTION
            key = product.catalog_product_id
            self._documents[key] = weighted
            for token in weighted:
                self._postings.setdefault(token, set()).add(key)
        self._document_count = len(self._documents)

    def _inverse_frequency(self, token: str) -> float:
        matches = len(self._postings.get(token, ()))
        if matches == 0:
            return 0.0
        return log(1.0 + self._document_count / matches)

    def search(
        self,
        intent: SandboxIntent,
        *,
        limit: int = MAX_RESULTS,
    ) -> SearchResult:
        """Rank listings for one read instruction, then apply stated filters."""

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
        query = intent.search_text or intent.raw_text
        query_tokens = tokens(query)
        query_phrases = {
            phrase for phrase in _phrases(query) if " " in phrase
        }
        category_scores = _category_scores(query)
        product_family = infer_product_family(query, category_scores)
        brand_hints = {item.lower() for item in intent.brand_hints}

        # Only listings that share at least one query token can score above
        # zero, so the postings union bounds the work by what was asked for
        # rather than by the size of the catalogue.
        reachable: set[str] = set()
        for token in set(query_tokens):
            reachable |= self._postings.get(token, set())
        if product_family is not None:
            if not product_family.available:
                reachable.clear()
            else:
                allowed_categories = set(product_family.category_ids)
                reachable = {
                    catalog_id
                    for catalog_id in reachable
                    if self._by_id[catalog_id].category_id in allowed_categories
                }

        scored: list[tuple[float, SandboxProduct, SearchSignal]] = []
        for catalog_id in reachable:
            product = self._by_id[catalog_id]
            weighted = self._documents[catalog_id]
            lexical = 0.0
            matched: list[str] = []
            for token in query_tokens:
                weight = weighted.get(token)
                if weight:
                    lexical += weight * self._inverse_frequency(token)
                    if token not in matched:
                        matched.append(token)
            category_score = category_scores.get(product.category_id, 0.0) * _WEIGHT_CATEGORY
            brand_match = product.brand if product.brand.lower() in brand_hints else None
            product_name = product.name.lower()
            exact_phrase = next(
                (
                    phrase
                    for phrase in sorted(query_phrases, key=lambda item: (-len(item), item))
                    if phrase in product_name
                ),
                None,
            )
            phrase_score = _WEIGHT_EXACT_PHRASE if exact_phrase else 0.0
            total = (
                lexical
                + category_score
                + phrase_score
                + (_WEIGHT_BRAND if brand_match else 0.0)
            )
            if total <= 0.0:
                continue
            within_budget = (
                intent.max_total_minor is None
                or product.price_minor * intent.quantity <= intent.max_total_minor
            )
            scored.append(
                (
                    total,
                    product,
                    SearchSignal(
                        category_match=(
                            product.category_label
                            if product.category_id in category_scores
                            else None
                        ),
                        matched_terms=tuple(matched[:6]),
                        brand_match=brand_match,
                        within_budget=within_budget,
                        lexical_score=lexical,
                        category_score=category_score,
                        exact_phrase_match=exact_phrase,
                        total_score=total,
                    ),
                )
            )

        # Ties are broken by identity, never by evidence family: the ranker must
        # not know which listings are easy to authorize.
        scored.sort(key=lambda row: (-row[0], row[1].merchant_id, row[1].sku))
        considered = len(scored)

        eligible: list[Candidate] = []
        misses: list[NearMiss] = []
        for total, product, signal in scored:
            reason = _excluded_by(product, intent)
            if reason is None:
                eligible.append(Candidate(product=product, signal=signal))
            elif len(misses) < 6:
                misses.append(
                    NearMiss(product=product, excluded_by=reason[0], explanation=reason[1])
                )
            if len(eligible) >= limit and len(misses) >= 3:
                break

        if not eligible and not misses:
            misses.extend(self._closest_available(query, intent))

        return SearchResult(
            candidates=tuple(eligible[:limit]),
            near_misses=tuple(misses[:4]),
            considered=considered,
            matched_categories=tuple(sorted(category_scores)),
            product_family=product_family,
        )

    def _closest_available(
        self, query: str, intent: SandboxIntent
    ) -> tuple[NearMiss, ...]:
        """Give an honest, bounded fallback when no vocabulary overlaps at all.

        This never turns a fuzzy guess into a candidate. It only populates the
        explanatory near-miss panel, labelled ``NO_RELEVANCE_MATCH``, so an
        unknown request does not end in a dead technical error or pretend that
        a random product is what the person asked for.
        """

        normalized = " ".join(tokens(query)) or query.strip().lower()
        ranked_categories: list[tuple[float, str, str]] = []
        for category in CATEGORIES:
            phrases = (category.label.lower(), *category.synonyms)
            score = max(
                SequenceMatcher(None, normalized, phrase).ratio()
                for phrase in phrases
            )
            ranked_categories.append((score, category.category_id, category.label))
        ranked_categories.sort(key=lambda item: (-item[0], item[1]))

        misses: list[NearMiss] = []
        for _score, category_id, label in ranked_categories[:4]:
            products = sorted(
                (
                    product
                    for product in self._universe.products
                    if product.category_id == category_id
                ),
                key=lambda product: (product.price_minor, product.merchant_id, product.sku),
            )
            if not products:
                continue
            product = products[0]
            constraint = _excluded_by(product, intent)
            misses.append(
                NearMiss(
                    product=product,
                    excluded_by=(constraint[0] if constraint else "NO_RELEVANCE_MATCH"),
                    explanation=(
                        constraint[1]
                        if constraint
                        else (
                            f"{label} is the closest available sandbox category, "
                            "but no product term or category synonym matched your request."
                        )
                    ),
                )
            )
        return tuple(misses)


def _excluded_by(
    product: SandboxProduct, intent: SandboxIntent
) -> tuple[str, str] | None:
    """Name the one stated constraint this listing fails, if any.

    This is a *discovery* filter over what the buyer said, so the results a
    person is offered reflect the request they made. It is not an authorization
    check and shares no code with one: everything it decides here is decided
    again, independently, by the controller once a listing is chosen.
    """

    if (
        intent.max_total_minor is not None
        and product.price_minor * intent.quantity > intent.max_total_minor
    ):
        return (
            "MAX_TOTAL",
            f"Priced at INR {product.price_minor / 100:,.2f}, above your "
            f"INR {intent.max_total_minor / 100:,.2f} limit.",
        )
    if product.recurring and not intent.recurring_allowed:
        return (
            "RECURRENCE_NOT_PERMITTED",
            "This listing is a renewing subscription and you did not ask for a "
            "recurring charge.",
        )
    return None


def excluded_summary(intent: SandboxIntent) -> list[str]:
    """The stated constraints a search applied, for the empty-result panel."""

    lines: list[str] = []
    if intent.max_total_minor is not None:
        lines.append(f"Price at most INR {intent.max_total_minor / 100:,.2f}")
    if not intent.recurring_allowed:
        lines.append("No renewing subscription")
    for item in intent.exclusions:
        lines.append(f"Nothing involving {item}")
    return lines


def category_directory() -> list[dict[str, Any]]:
    """Every shelf in the sandbox, for the "what can I ask for" panel."""

    return [
        {
            "category_id": category.category_id,
            "label": category.label,
            "group": category.group,
            "examples": list(category.synonyms[:4]),
        }
        for category in CATEGORIES
    ]
