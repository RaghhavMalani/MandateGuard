"""Deterministic construction of the sandbox commerce universe.

Every product, every identifier and every evidence sentence is a pure function
of ``(WORLD_VERSION, WORLD_SEED, category_id, index)``. There is no wall clock,
no RNG that outlives a call, and no dependence on dictionary or set iteration
order, so two machines that run this module produce byte-identical worlds and
the frozen manifest can prove it.

The generator's one hard rule is that it decides *what a merchant has
published*, and nothing else. It never computes, stores, or hints at an
authorization outcome. A listing in the ``BILLING_UNDECLARED`` family is not "a
REVIEW product"; it is a listing whose seller never wrote down a billing model.
What happens when somebody tries to buy it depends on what they asked for, and
is settled by the controller alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterator

from mandateguard.discovery.schema import catalog_product_id as derive_catalog_id
from mandateguard.intelligence.models import CommerceProduct
from mandateguard.semantic.evidence import SemanticEvidenceEntry

from mandateguard.sandbox.templates import (
    BILLING_CONFLICT_SENTENCE,
    BILLING_ONE_TIME,
    BILLING_RECURRING,
    BILLING_UNDECLARED_SENTENCE,
    BRANDS,
    CATEGORIES,
    CONTENT_CLEAR,
    CONTENT_PROHIBITED,
    CONTENT_UNDECLARED_SENTENCE,
    CURRENCY,
    Category,
    EvidenceFamily,
    FEATURED_PRODUCTS,
    IDENTITY_TEMPLATE,
    MERCHANT_TERMS_TEMPLATE,
    MERCHANTS,
    Merchant,
    PRICE_STEPS,
    PRODUCTS_PER_CATEGORY,
    PURPOSE_TEMPLATE,
    PURPOSE_UNDECLARED_SENTENCE,
    SANDBOX_MERCHANT_PREFIX,
    SERIES,
    SYNTHETIC_NOTICE,
    WORLD_SEED,
    WORLD_VERSION,
)


#: Domain separator. Two generators with the same seed but different domains
#: must not produce correlated draws.
_DOMAIN = b"mandateguard/sandbox-universe/v1"

#: The effective date stamped into every authoritative price record. Fixed, so
#: the world does not drift with the calendar.
EFFECTIVE_FROM = "2026-09-01T00:00:00Z"

#: Evidence version stamped on generated records.
EVIDENCE_VERSION = "v1"

#: The category identifiers this frozen taxonomy owns.
#:
#: A2 compares the product family a mandate authorizes against the family
#: recorded in the committed server-side catalogue. That comparison only means
#: anything for a category this taxonomy defines. A listing filed outside it -
#: a simulated merchant onboarded from a crawled marketplace row, whose only
#: category words are the ones that marketplace happened to print - has no
#: server-owned family. Saying so is what makes A2 report the identity as
#: unavailable instead of inventing one out of the seller's own prose.
TAXONOMY_CATEGORY_IDS: frozenset[str] = frozenset(
    category.category_id for category in CATEGORIES
)

#: Family weights out of 1000, applied per listing. Categories that cannot
#: plausibly carry a family redistribute its weight to ``COMPLETE``, so the
#: realised mix differs slightly per category and is reported, never assumed.
_FAMILY_WEIGHTS: tuple[tuple[EvidenceFamily, int], ...] = (
    (EvidenceFamily.COMPLETE, 720),
    (EvidenceFamily.BILLING_UNDECLARED, 90),
    (EvidenceFamily.AUTHORITY_CONFLICT, 50),
    (EvidenceFamily.RECURRING_DECLARED, 80),
    (EvidenceFamily.PROHIBITED_CONTENT_DECLARED, 60),
)

#: In a subscription or software category a recurring plan is the norm rather
#: than the exception, so those categories draw from a different mix.
_SOFTWARE_FAMILY_WEIGHTS: tuple[tuple[EvidenceFamily, int], ...] = (
    (EvidenceFamily.RECURRING_DECLARED, 620),
    (EvidenceFamily.COMPLETE, 210),
    (EvidenceFamily.BILLING_UNDECLARED, 80),
    (EvidenceFamily.AUTHORITY_CONFLICT, 50),
    (EvidenceFamily.PROHIBITED_CONTENT_DECLARED, 40),
)

_SUBSCRIPTION_FAMILY_WEIGHTS: tuple[tuple[EvidenceFamily, int], ...] = (
    # A product on the explicit Subscriptions shelf is never constructed as a
    # one-time purchase. It either declares recurrence, omits the declaration,
    # or publishes a conflict. That keeps the product words and the merchant's
    # authoritative billing evidence internally consistent.
    (EvidenceFamily.RECURRING_DECLARED, 800),
    (EvidenceFamily.BILLING_UNDECLARED, 120),
    (EvidenceFamily.AUTHORITY_CONFLICT, 80),
)


def _digest(*parts: object) -> bytes:
    payload = b"\x00".join(str(part).encode("utf-8") for part in parts)
    return sha256(_DOMAIN + b"\x00" + str(WORLD_SEED).encode("ascii") + b"\x00" + payload).digest()


def _draw(field: str, category_id: str, index: int, modulus: int) -> int:
    """One reproducible integer in ``[0, modulus)`` for a named field."""

    if modulus <= 0:
        raise ValueError("modulus must be positive")
    return int.from_bytes(_digest(WORLD_VERSION, field, category_id, index), "big") % modulus


@dataclass(frozen=True, slots=True)
class SandboxProduct:
    """One synthetic listing, with everything the surfaces need to show it.

    ``evidence_family`` records how this listing was constructed. It is
    provenance for the generator and for the catalogue view; it is never read on
    a decision path, and the readiness signals shown next to a product are
    derived by scanning the published evidence text, not by reading this field.
    """

    catalog_product_id: str
    merchant_id: str
    merchant_display_name: str
    sku: str
    name: str
    brand: str
    category_id: str
    category_label: str
    category_group: str
    description: str
    price_minor: int
    currency: str
    billing_model: str
    recurring: bool
    purpose_claims: tuple[str, ...]
    exclusion_claims: tuple[str, ...]
    recurrence_declaration: str
    effective_from: str
    evidence_version: str
    evidence_family: EvidenceFamily
    keywords: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @property
    def synthetic(self) -> bool:
        """Always true. Present so callers can assert it rather than assume it."""

        return True

    def commerce_product(self) -> CommerceProduct:
        """Project into the type the trusted store and the controller consume."""

        return CommerceProduct(
            merchant_id=self.merchant_id,
            sku=self.sku,
            name=self.name,
            description=self.description,
            effective_unit_price_minor=self.price_minor,
            currency=self.currency,
            recurring=self.recurring,
            tags=self.keywords,
            evidence_ids=self.evidence_ids,
            # Server-owned families only. See TAXONOMY_CATEGORY_IDS: an
            # onboarded listing's category came off a crawled page, so it is
            # reported absent rather than as a family nothing vouched for.
            product_family=(
                self.category_id
                if self.category_id in TAXONOMY_CATEGORY_IDS
                else None
            ),
        )

    def public_mapping(self) -> dict[str, Any]:
        """What a browser is allowed to see about one candidate."""

        return {
            "catalog_product_id": self.catalog_product_id,
            "merchant_id": self.merchant_id,
            "merchant": self.merchant_display_name,
            "sku": self.sku,
            "name": self.name,
            "brand": self.brand,
            "category_id": self.category_id,
            "category": self.category_label,
            "category_group": self.category_group,
            "description": self.description,
            "price_minor": self.price_minor,
            "currency": self.currency,
            "billing_model": self.billing_model,
            "recurring": self.recurring,
            "purpose_claims": list(self.purpose_claims),
            "exclusion_claims": list(self.exclusion_claims),
            "recurrence_declaration": self.recurrence_declaration,
            "effective_from": self.effective_from,
            "evidence_version": self.evidence_version,
            "world": "SANDBOX",
            "synthetic": True,
        }


def _merchants_for(category_id: str) -> tuple[Merchant, ...]:
    return tuple(item for item in MERCHANTS if category_id in item.categories)


def _price_for(category: Category, index: int) -> int:
    """Spread listings across the band, landing on familiar ``...99`` rupees.

    Prices are whole rupees. A shopper reads 1,299, not 1,264.99, and a judge
    checking "under 2,000" against a candidate should not have to think about
    paise to see whether the ceiling holds.
    """

    span = category.price_high_minor - category.price_low_minor
    step = max(100, span // (PRICE_STEPS - 1))
    slot = _draw("price", category.category_id, index, PRICE_STEPS)
    rupees = (category.price_low_minor + slot * step) // 100
    # Snap down to the nearest ...99, never above the band's own ceiling.
    snapped = max(99, (rupees // 100) * 100 + 99)
    if snapped * 100 > category.price_high_minor:
        snapped = max(99, ((category.price_high_minor // 100) // 100) * 100 + 99)
    return snapped * 100


def _family_for(category: Category, index: int) -> EvidenceFamily:
    if category.category_id == "subscriptions-media":
        weights = _SUBSCRIPTION_FAMILY_WEIGHTS
    elif category.category_id == "software-services":
        weights = _SOFTWARE_FAMILY_WEIGHTS
    else:
        weights = _FAMILY_WEIGHTS
    # Families the category cannot plausibly express fold back into COMPLETE,
    # so the world never contains an implausible artefact such as a syllabus
    # attached to a power bank.
    permitted: list[tuple[EvidenceFamily, int]] = []
    folded = 0
    for family, weight in weights:
        if (
            family is EvidenceFamily.PROHIBITED_CONTENT_DECLARED
            and category.prohibited_content_weight is not None
        ):
            weight = category.prohibited_content_weight
        allowed = (
            (family is not EvidenceFamily.RECURRING_DECLARED or category.allows_recurring)
            and (
                family is not EvidenceFamily.PROHIBITED_CONTENT_DECLARED
                or category.allows_prohibited_content
            )
        )
        if allowed:
            permitted.append((family, weight))
        else:
            folded += weight
    total = sum(weight for _, weight in permitted) + folded
    roll = _draw("family", category.category_id, index, total)
    cursor = 0
    for family, weight in permitted:
        cursor += weight
        if roll < cursor:
            return family
    return EvidenceFamily.COMPLETE


def _subject_for(category: Category, index: int) -> str | None:
    if not category.subjects:
        return None
    return category.subjects[_draw("subject", category.category_id, index, len(category.subjects))]


def _name_for(category: Category, index: int, brand: str, subject: str | None) -> str:
    adjectives = category.adjectives
    adjective = adjectives[_draw("adjective", category.category_id, index, len(adjectives))]
    noun = category.nouns[_draw("noun", category.category_id, index, len(category.nouns))]
    # "Mechanical Mechanical Keyboard" and "True Wireless Wireless Earbuds"
    # read like generator artefacts, because they are. Step to the next
    # adjective whenever *any* adjective token already appears in the noun.
    noun_words = {word.lower() for word in noun.split()}
    if noun_words.intersection(word.lower() for word in adjective.split()):
        position = adjectives.index(adjective)
        for offset in range(1, len(adjectives)):
            candidate = adjectives[(position + offset) % len(adjectives)]
            if not noun_words.intersection(
                word.lower() for word in candidate.split()
            ):
                adjective = candidate
                break
    series = SERIES[_draw("series", category.category_id, index, len(SERIES))]
    if subject is not None:
        # For a course or a book the subject is the product. Leading with the
        # brand would bury the only word a searcher is likely to type.
        return f"{adjective} {subject} {noun} ({brand} {series})"
    return f"{brand} {adjective} {noun} {series}"


def _purposes_for(category: Category, index: int) -> tuple[str, ...]:
    """Choose a contiguous, reproducible slice of the category's purposes."""

    available = category.purposes
    if len(available) <= 3:
        return available
    width = 3 + _draw("purpose-width", category.category_id, index, min(3, len(available) - 2))
    start = _draw("purpose-start", category.category_id, index, len(available))
    return tuple(available[(start + offset) % len(available)] for offset in range(width))


def _evidence_texts(
    *,
    product_name: str,
    category: Category,
    family: EvidenceFamily,
    purposes: tuple[str, ...],
    detail: str,
) -> tuple[str, str, str]:
    """Return the (billing, content, purpose) sentences for one listing."""

    if family is EvidenceFamily.RECURRING_DECLARED:
        return BILLING_RECURRING, CONTENT_CLEAR, PURPOSE_TEMPLATE.format(
            purposes=", ".join(purposes)
        )
    if family is EvidenceFamily.PROHIBITED_CONTENT_DECLARED:
        return BILLING_ONE_TIME, CONTENT_PROHIBITED, PURPOSE_TEMPLATE.format(
            purposes=", ".join(purposes)
        )
    if family is EvidenceFamily.BILLING_UNDECLARED:
        return (
            BILLING_UNDECLARED_SENTENCE,
            CONTENT_UNDECLARED_SENTENCE,
            PURPOSE_UNDECLARED_SENTENCE,
        )
    if family is EvidenceFamily.AUTHORITY_CONFLICT:
        return (
            BILLING_CONFLICT_SENTENCE,
            CONTENT_UNDECLARED_SENTENCE,
            PURPOSE_UNDECLARED_SENTENCE,
        )
    return BILLING_ONE_TIME, CONTENT_CLEAR, PURPOSE_TEMPLATE.format(
        purposes=", ".join(purposes)
    )


#: The exclusion categories a cleared content classification actually names.
#: Shown to the user as the merchant's exclusion claims.
CLEARED_EXCLUSIONS: tuple[str, ...] = (
    "gambling", "betting", "wagering", "casino material", "lottery material",
    "alcohol", "tobacco", "vaping", "weapons", "adult material",
    "crypto trading content", "loan offer", "political advertising",
)


#: Catalog identifiers are derived exactly the way marketplace ones are, so
#: the two namespaces ("sandbox." and the marketplace source prefix) can never
#: collide and an identifier alone says which world a listing came from.
SANDBOX_SOURCE = "sandbox"


def sandbox_catalog_id(merchant_id: str, sku: str) -> str:
    return derive_catalog_id(SANDBOX_SOURCE, f"{merchant_id}/{sku}")


def _rupees(minor: int) -> str:
    return f"{minor // 100:,}.{minor % 100:02d}"


def _generate_category(category: Category) -> Iterator[SandboxProduct]:
    merchants = _merchants_for(category.category_id)
    if not merchants:
        raise ValueError(f"category {category.category_id} has no merchant")
    for index in range(PRODUCTS_PER_CATEGORY):
        merchant = merchants[_draw("merchant", category.category_id, index, len(merchants))]
        brand = BRANDS[_draw("brand", category.category_id, index, len(BRANDS))]
        featured = FEATURED_PRODUCTS.get((category.category_id, index))
        family = featured[2] if featured is not None else _family_for(category, index)
        price = featured[1] if featured is not None else _price_for(category, index)
        purposes = _purposes_for(category, index)
        subject = _subject_for(category, index)
        name = _name_for(category, index, brand, subject)
        sku = featured[0] if featured is not None else f"{category.category_id}-{index:03d}"
        billing_text, content_text, purpose_text = _evidence_texts(
            product_name=name,
            category=category,
            family=family,
            purposes=purposes,
            detail=category.detail,
        )
        recurring = family is EvidenceFamily.RECURRING_DECLARED
        billing_model = (
            "RECURRING"
            if recurring
            else "NOT_DECLARED"
            if family is EvidenceFamily.BILLING_UNDECLARED
            else "CONFLICTED"
            if family is EvidenceFamily.AUTHORITY_CONFLICT
            else "ONE_TIME"
        )
        identity_text = IDENTITY_TEMPLATE.format(
            display_name=merchant.display_name,
            merchant_id=merchant.merchant_id,
            sku=sku,
            price_text=_rupees(price),
            currency=CURRENCY,
            effective_from=EFFECTIVE_FROM,
            version=EVIDENCE_VERSION,
        )
        listing_evidence_id = f"sbev-{sku}-listing-{EVIDENCE_VERSION}"
        terms_evidence_id = f"sbev-{sku}-terms-{EVIDENCE_VERSION}"
        description = (
            f"{name}. {category.detail} Listed by {merchant.display_name} in the "
            f"MandateGuard sandbox. Synthetic listing, not a real product."
        )
        exclusion_claims = (
            CLEARED_EXCLUSIONS
            if content_text == CONTENT_CLEAR
            else ("gambling PRESENT",)
            if content_text == CONTENT_PROHIBITED
            else ()
        )
        recurrence_declaration = (
            "RENEWS_UNTIL_CANCELLED"
            if recurring
            else "NOT_RECORDED"
            if family is EvidenceFamily.BILLING_UNDECLARED
            else "RECORDS_DISAGREE"
            if family is EvidenceFamily.AUTHORITY_CONFLICT
            else "SETTLED_ONCE"
        )
        yield SandboxProduct(
            catalog_product_id=sandbox_catalog_id(merchant.merchant_id, sku),
            merchant_id=merchant.merchant_id,
            merchant_display_name=merchant.display_name,
            sku=sku,
            name=name,
            brand=brand,
            category_id=category.category_id,
            category_label=category.label,
            category_group=category.group,
            description=description,
            price_minor=price,
            currency=CURRENCY,
            billing_model=billing_model,
            recurring=recurring,
            purpose_claims=purposes if purpose_text != PURPOSE_UNDECLARED_SENTENCE else (),
            exclusion_claims=exclusion_claims,
            recurrence_declaration=recurrence_declaration,
            effective_from=EFFECTIVE_FROM,
            evidence_version=EVIDENCE_VERSION,
            evidence_family=family,
            keywords=_keywords(category, brand, subject),
            evidence_ids=(
                f"sbev-{merchant.merchant_id}-terms-{EVIDENCE_VERSION}",
                terms_evidence_id,
                listing_evidence_id,
            ),
        )


def _keywords(category: Category, brand: str, subject: str | None) -> tuple[str, ...]:
    """Search vocabulary carried on the product, capped for the store's limit."""

    seen: list[str] = []
    for value in (
        category.label.lower(),
        category.group.lower(),
        brand.lower(),
        *((subject.lower(),) if subject else ()),
        *category.synonyms,
    ):
        lowered = value.strip().lower()
        if lowered and lowered not in seen:
            seen.append(lowered)
    return tuple(seen[:32])


def _evidence_for(product: SandboxProduct, category: Category) -> tuple[SemanticEvidenceEntry, ...]:
    family = product.evidence_family
    purposes = product.purpose_claims
    billing_text, content_text, purpose_text = _evidence_texts(
        product_name=product.name,
        category=category,
        family=family,
        purposes=purposes or category.purposes[:3],
        detail=category.detail,
    )
    identity_text = IDENTITY_TEMPLATE.format(
        display_name=product.merchant_display_name,
        merchant_id=product.merchant_id,
        sku=product.sku,
        price_text=_rupees(product.price_minor),
        currency=product.currency,
        effective_from=product.effective_from,
        version=product.evidence_version,
    )
    return (
        SemanticEvidenceEntry(
            evidence_id=f"sbev-{product.sku}-terms-{EVIDENCE_VERSION}",
            merchant_id=product.merchant_id,
            sku=product.sku,
            source_kind="product_terms",
            text=f"{identity_text} {billing_text} {SYNTHETIC_NOTICE}",
        ),
        SemanticEvidenceEntry(
            evidence_id=f"sbev-{product.sku}-listing-{EVIDENCE_VERSION}",
            merchant_id=product.merchant_id,
            sku=product.sku,
            source_kind=(
                "course_syllabus"
                if family is EvidenceFamily.PROHIBITED_CONTENT_DECLARED
                else "product_description"
            ),
            text=(
                f"{product.name}. {category.detail} {content_text} {purpose_text} "
                f"{SYNTHETIC_NOTICE}"
            ),
        ),
    )


# Deliberately not ``slots=True``: the two lookup maps below are derived state
# built in ``__post_init__``, not part of the world's identity or its digest,
# and a slotted frozen dataclass has nowhere to put them.
@dataclass(frozen=True)
class SandboxUniverse:
    """The generated world: products, evidence, and how to look either up."""

    world_version: str
    seed: int
    products: tuple[SandboxProduct, ...]
    evidence_entries: tuple[SemanticEvidenceEntry, ...]
    products_sha256: str
    evidence_sha256: str

    def by_identity(self, merchant_id: str, sku: str) -> SandboxProduct | None:
        return self._identity_index.get((merchant_id, sku))

    def by_catalog_id(self, catalog_product_id: str) -> SandboxProduct | None:
        return self._catalog_index.get(catalog_product_id)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_identity_index",
            {(item.merchant_id, item.sku): item for item in self.products},
        )
        object.__setattr__(
            self,
            "_catalog_index",
            {item.catalog_product_id: item for item in self.products},
        )


def _canonical_product_row(product: SandboxProduct) -> str:
    return json.dumps(
        [
            product.merchant_id,
            product.sku,
            product.name,
            product.brand,
            product.category_id,
            product.price_minor,
            product.currency,
            product.recurring,
            product.billing_model,
            product.recurrence_declaration,
            list(product.purpose_claims),
            product.evidence_family.value,
            list(product.evidence_ids),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def build_universe() -> SandboxUniverse:
    """Generate the whole sandbox world. Pure, deterministic, no I/O."""

    products: list[SandboxProduct] = []
    evidence: list[SemanticEvidenceEntry] = []
    merchant_terms_written: set[str] = set()
    merchant_by_id = {item.merchant_id: item for item in MERCHANTS}

    for category in CATEGORIES:
        for product in _generate_category(category):
            products.append(product)
            evidence.extend(_evidence_for(product, category))
            if product.merchant_id not in merchant_terms_written:
                merchant_terms_written.add(product.merchant_id)
                merchant = merchant_by_id[product.merchant_id]
                evidence.append(
                    SemanticEvidenceEntry(
                        evidence_id=f"sbev-{merchant.merchant_id}-terms-{EVIDENCE_VERSION}",
                        merchant_id=merchant.merchant_id,
                        sku=None,
                        source_kind="merchant_terms",
                        text=(
                            MERCHANT_TERMS_TEMPLATE.format(display_name=merchant.display_name)
                            + " "
                            + SYNTHETIC_NOTICE
                        ),
                    )
                )

    ordered_products = tuple(sorted(products, key=lambda item: (item.merchant_id, item.sku)))
    ordered_evidence = tuple(
        sorted(
            evidence,
            key=lambda item: (item.merchant_id, item.sku is not None, item.sku or "", item.evidence_id),
        )
    )
    products_digest = sha256()
    for product in ordered_products:
        products_digest.update(_canonical_product_row(product).encode("utf-8"))
        products_digest.update(b"\n")
    evidence_digest = sha256()
    for entry in ordered_evidence:
        evidence_digest.update(
            json.dumps(
                [entry.evidence_id, entry.merchant_id, entry.sku, entry.source_kind, entry.text],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        evidence_digest.update(b"\n")

    return SandboxUniverse(
        world_version=WORLD_VERSION,
        seed=WORLD_SEED,
        products=ordered_products,
        evidence_entries=ordered_evidence,
        products_sha256=products_digest.hexdigest(),
        evidence_sha256=evidence_digest.hexdigest(),
    )


def universe_manifest(universe: SandboxUniverse) -> dict[str, Any]:
    """A small, comparable description of a generated world.

    This is what the freeze file records and the determinism test compares. It
    contains counts and digests, never outcomes: a manifest that mentioned ALLOW
    would mean the world had been built with a verdict in mind.
    """

    families: dict[str, int] = {}
    categories: dict[str, int] = {}
    merchants: set[str] = set()
    price_min = min(item.price_minor for item in universe.products)
    price_max = max(item.price_minor for item in universe.products)
    for product in universe.products:
        families[product.evidence_family.value] = families.get(product.evidence_family.value, 0) + 1
        categories[product.category_id] = categories.get(product.category_id, 0) + 1
        merchants.add(product.merchant_id)
    approximate_bytes = sum(
        len(product.description.encode("utf-8")) + len(product.name.encode("utf-8"))
        for product in universe.products
    ) + sum(len(entry.text.encode("utf-8")) for entry in universe.evidence_entries)
    return {
        "world_version": universe.world_version,
        "seed": universe.seed,
        "generator": "mandateguard.sandbox.universe.build_universe",
        "product_count": len(universe.products),
        "evidence_count": len(universe.evidence_entries),
        "merchant_count": len(merchants),
        "category_count": len(categories),
        "products_sha256": universe.products_sha256,
        "evidence_sha256": universe.evidence_sha256,
        "evidence_families": dict(sorted(families.items())),
        "products_per_category": dict(sorted(categories.items())),
        "price_minor_range": [price_min, price_max],
        "currency": CURRENCY,
        "text_bytes": approximate_bytes,
        "merchant_id_prefix": SANDBOX_MERCHANT_PREFIX,
        "synthetic": True,
    }
