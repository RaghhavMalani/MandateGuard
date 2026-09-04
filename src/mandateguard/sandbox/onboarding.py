"""Simulated merchant onboarding: the bridge a crawled listing cannot cross.

A historical marketplace listing is searchable and unbuyable, and the gap
between those two words is the product. This module shows what closing it costs:
the merchant has to *publish* things - who they are, that they own the SKU, what
the authoritative price is, how billing works, what the content is, what the
listing is for - and only then can an authorization controller have an opinion
worth acting on.

The safety rule this module exists to respect is a single sentence, and every
function below is arranged around it:

    **A crawled row never becomes trusted. A new record is created beside it.**

So: nothing here mutates the marketplace catalogue, nothing here writes into the
shared sandbox world, and nothing copies a crawled *claim* into a trusted field.
The listing contributes neutral discovery attributes - the words a person
searched for, roughly what shelf it sits on - and every authoritative field is
declared afresh, on the record, by the simulated merchant. The result is a
brand-new synthetic merchant and SKU in the visitor's own session, and a
completely fresh authorization run against it.

The original listing is completely unchanged and remains untrusted afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Mapping

from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.semantic.evidence import SemanticEvidenceEntry

from mandateguard.sandbox.session import OnboardedMerchant
from mandateguard.sandbox.templates import (
    ALL_PURPOSES,
    BILLING_CONFLICT_SENTENCE,
    BILLING_ONE_TIME,
    BILLING_RECURRING,
    BILLING_UNDECLARED_SENTENCE,
    CONTENT_CLEAR,
    CONTENT_PROHIBITED,
    CONTENT_UNDECLARED_SENTENCE,
    CURRENCY,
    IDENTITY_TEMPLATE,
    MERCHANT_TERMS_TEMPLATE,
    PURPOSE_TEMPLATE,
    PURPOSE_UNDECLARED_SENTENCE,
    SANDBOX_MERCHANT_PREFIX,
    SYNTHETIC_NOTICE,
)
from mandateguard.sandbox.universe import (
    CLEARED_EXCLUSIONS,
    EFFECTIVE_FROM,
    EVIDENCE_VERSION,
    EvidenceFamily,
    SandboxProduct,
    sandbox_catalog_id,
)


#: Onboarded merchants carry a second marker beyond the sandbox prefix, so an
#: identifier alone says both "synthetic" and "created during a demo session".
ONBOARDED_PREFIX = f"{SANDBOX_MERCHANT_PREFIX}onboarded-"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_TITLE = 120

#: The declarations a merchant is asked to publish. Everything the controller
#: treats as authoritative has to come from this form; nothing may be inferred
#: from the crawled row.
BILLING_CHOICES = ("ONE_TIME", "RECURRING", "NOT_DECLARED", "CONFLICTED")
CONTENT_CHOICES = ("NO_RESTRICTED_CONTENT", "GAMBLING_PRESENT", "NOT_DECLARED")

MAX_PRICE_MINOR = 100_000_000


class OnboardingError(ValueError):
    """The declaration form was incomplete or out of bounds."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.public_message = message
        super().__init__(f"{code}: {message}")


def _slug(text: str, *, fallback: str) -> str:
    value = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return value[:48] or fallback


@dataclass(frozen=True, slots=True)
class NeutralDiscoveryAttributes:
    """The only things a crawled listing is allowed to contribute.

    Words and a shelf. Not a price, not a seller of record, not a billing model,
    not a content classification: those are claims, and a claim scraped off a
    page in 2016 is not evidence anybody has vouched for today.
    """

    listing_id: str
    title: str
    category_label: str
    brand_hint: str | None

    @classmethod
    def from_listing(cls, listing: Mapping[str, Any]) -> NeutralDiscoveryAttributes:
        listing_id = str(listing.get("catalog_product_id") or "").strip()
        title = str(listing.get("title") or "").strip()
        if not listing_id or not title:
            raise OnboardingError(
                "LISTING_NOT_USABLE",
                "That listing has no identifier or title to start from.",
            )
        category_path = listing.get("category_path")
        category_label = ""
        if isinstance(category_path, list) and category_path:
            category_label = str(category_path[-1]).strip()
        if not category_label:
            category_label = str(listing.get("top_category") or "General").strip()
        brand = listing.get("brand")
        brand_hint = str(brand).strip()[:60] if isinstance(brand, str) and brand.strip() else None
        return cls(
            listing_id=listing_id[:128],
            title=title[:_MAX_TITLE],
            category_label=category_label[:60] or "General",
            brand_hint=brand_hint,
        )


@dataclass(frozen=True, slots=True)
class MerchantDeclaration:
    """What the simulated merchant publishes. Every field is required."""

    merchant_display_name: str
    price_minor: int
    billing_model: str
    content_classification: str
    purposes: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: object) -> MerchantDeclaration:
        if not isinstance(value, Mapping):
            raise OnboardingError(
                "DECLARATION_INVALID", "The merchant declaration must be an object."
            )
        expected = {
            "merchant_display_name",
            "price_minor",
            "billing_model",
            "content_classification",
            "purposes",
        }
        if set(value) != expected:
            raise OnboardingError(
                "DECLARATION_INVALID",
                "The merchant declaration must contain exactly: "
                + ", ".join(sorted(expected))
                + ".",
            )
        name = value["merchant_display_name"]
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 60:
            raise OnboardingError(
                "DECLARATION_INVALID",
                "The merchant name must be between 2 and 60 characters.",
            )
        price = value["price_minor"]
        if (
            isinstance(price, bool)
            or not isinstance(price, int)
            or not 1 <= price <= MAX_PRICE_MINOR
        ):
            raise OnboardingError(
                "DECLARATION_INVALID",
                "The authoritative price must be a positive amount in minor units.",
            )
        billing = value["billing_model"]
        if billing not in BILLING_CHOICES:
            raise OnboardingError(
                "DECLARATION_INVALID",
                "The billing model must be one of: " + ", ".join(BILLING_CHOICES) + ".",
            )
        content = value["content_classification"]
        if content not in CONTENT_CHOICES:
            raise OnboardingError(
                "DECLARATION_INVALID",
                "The content classification must be one of: "
                + ", ".join(CONTENT_CHOICES)
                + ".",
            )
        purposes = value["purposes"]
        if (
            not isinstance(purposes, list)
            or len(purposes) > 6
            or any(item not in ALL_PURPOSES for item in purposes)
        ):
            raise OnboardingError(
                "DECLARATION_INVALID",
                "Intended uses must be chosen from the published vocabulary.",
            )
        return cls(
            merchant_display_name=name.strip(),
            price_minor=price,
            billing_model=billing,
            content_classification=content,
            purposes=tuple(dict.fromkeys(purposes)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "merchant_display_name": self.merchant_display_name,
            "price_minor": self.price_minor,
            "billing_model": self.billing_model,
            "content_classification": self.content_classification,
            "purposes": list(self.purposes),
        }


def declaration_form(attributes: NeutralDiscoveryAttributes) -> dict[str, Any]:
    """What the merchant-side form asks for, and what it already knows.

    ``prefilled`` is convenience only. Every value is editable, and the price in
    particular is presented as a field the merchant must assert rather than as
    something carried over from the crawled row.
    """

    return {
        "simulation": True,
        "notice": (
            "SIMULATION. This creates a new synthetic sandbox merchant record. The "
            "original marketplace listing is not modified and does not become trusted."
        ),
        "copied_from_listing": {
            "listing_id": attributes.listing_id,
            "title": attributes.title,
            "category_label": attributes.category_label,
            "brand_hint": attributes.brand_hint,
            "classification": "NEUTRAL_DISCOVERY_ATTRIBUTES",
            "note": (
                "Only the words and the shelf are carried across. Price, seller of "
                "record, billing model and content classification are not copied: the "
                "merchant has to declare them."
            ),
        },
        "generated_declarations": [
            {
                "field": "sku_ownership",
                "label": "SKU ownership",
                "value": "BOUND_TO_NEW_SANDBOX_MERCHANT_AND_SKU",
                "why": (
                    "A new SKU is generated for this synthetic merchant; the crawled "
                    "listing identifier is never reused as trusted identity."
                ),
            },
            {
                "field": "recurrence",
                "label": "Recurrence declaration",
                "value": "DERIVED_FROM_PUBLISHED_BILLING_MODEL",
                "why": "The evidence states whether billing settles once, renews, is absent, or conflicts.",
            },
            {
                "field": "exclusions",
                "label": "Exclusion declaration",
                "value": "DERIVED_FROM_PUBLISHED_CONTENT_CLASSIFICATION",
                "why": "The evidence names cleared restricted categories or declares gambling content present.",
            },
        ],
        "required_declarations": [
            {
                "field": "merchant_display_name",
                "label": "Merchant identity",
                "type": "text",
                "why": "Somebody has to be the seller of record for this SKU.",
            },
            {
                "field": "price_minor",
                "label": "Authoritative price (INR)",
                "type": "integer",
                "why": "The price the controller checks the transaction against.",
            },
            {
                "field": "billing_model",
                "label": "Billing model",
                "type": "choice",
                "choices": list(BILLING_CHOICES),
                "why": "Whether this charge repeats is not something to infer.",
            },
            {
                "field": "content_classification",
                "label": "Content classification",
                "type": "choice",
                "choices": list(CONTENT_CHOICES),
                "why": "A buyer's exclusion can only be checked against a declaration.",
            },
            {
                "field": "purposes",
                "label": "Documented intended use",
                "type": "multi-choice",
                "choices": list(ALL_PURPOSES),
                "why": "A stated purchase purpose needs something to be checked against.",
            },
        ],
        "prefilled": {
            "merchant_display_name": _suggested_name(attributes),
            "price_minor": None,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["general use"],
        },
    }


def _suggested_name(attributes: NeutralDiscoveryAttributes) -> str:
    base = attributes.brand_hint or attributes.category_label or "Sandbox"
    return f"{base[:40]} Sandbox Merchant"[:60]


def _billing_sentence(model: str) -> str:
    return {
        "ONE_TIME": BILLING_ONE_TIME,
        "RECURRING": BILLING_RECURRING,
        "NOT_DECLARED": BILLING_UNDECLARED_SENTENCE,
        "CONFLICTED": BILLING_CONFLICT_SENTENCE,
    }[model]


def _content_sentence(classification: str) -> str:
    return {
        "NO_RESTRICTED_CONTENT": CONTENT_CLEAR,
        "GAMBLING_PRESENT": CONTENT_PROHIBITED,
        "NOT_DECLARED": CONTENT_UNDECLARED_SENTENCE,
    }[classification]


def _family_for(declaration: MerchantDeclaration) -> EvidenceFamily:
    if declaration.content_classification == "GAMBLING_PRESENT":
        return EvidenceFamily.PROHIBITED_CONTENT_DECLARED
    if declaration.billing_model == "RECURRING":
        return EvidenceFamily.RECURRING_DECLARED
    if declaration.billing_model == "CONFLICTED":
        return EvidenceFamily.AUTHORITY_CONFLICT
    if declaration.billing_model == "NOT_DECLARED":
        return EvidenceFamily.BILLING_UNDECLARED
    return EvidenceFamily.COMPLETE


def onboard(
    *,
    attributes: NeutralDiscoveryAttributes,
    declaration: MerchantDeclaration,
    session_id: str,
    now: datetime | None = None,
) -> OnboardedMerchant:
    """Create a new synthetic merchant record beside an untouched listing."""

    moment = now or datetime.now(timezone.utc)
    # Identity is derived from the session as well as the listing, so two
    # visitors onboarding the same listing create two distinct merchants and
    # neither can address the other's SKU.
    declaration_identity = (
        f"{declaration.merchant_display_name}\x00{declaration.price_minor}\x00"
        f"{declaration.billing_model}\x00{declaration.content_classification}\x00"
        + "\x1f".join(declaration.purposes)
    )
    fingerprint = sha256(
        b"mandateguard/sandbox-onboarding/v2\x00"
        + session_id.encode("ascii")
        + b"\x00"
        + attributes.listing_id.encode("utf-8")
        + b"\x00"
        + declaration_identity.encode("utf-8")
    ).hexdigest()[:12]
    slug = _slug(declaration.merchant_display_name, fallback="merchant")
    merchant_id = f"{ONBOARDED_PREFIX}{slug}-{fingerprint}"
    sku = f"onboarded-{_slug(attributes.title, fallback='listing')}-{fingerprint}"
    display_name = f"{declaration.merchant_display_name} (Synthetic)"

    billing_text = _billing_sentence(declaration.billing_model)
    content_text = _content_sentence(declaration.content_classification)
    purpose_text = (
        PURPOSE_TEMPLATE.format(purposes=", ".join(declaration.purposes))
        if declaration.purposes
        else PURPOSE_UNDECLARED_SENTENCE
    )
    identity_text = IDENTITY_TEMPLATE.format(
        display_name=display_name,
        merchant_id=merchant_id,
        sku=sku,
        price_text=f"{declaration.price_minor // 100:,}.{declaration.price_minor % 100:02d}",
        currency=CURRENCY,
        effective_from=EFFECTIVE_FROM,
        version=EVIDENCE_VERSION,
    )
    evidence = (
        SemanticEvidenceEntry(
            evidence_id=f"sbev-{merchant_id}-terms-{EVIDENCE_VERSION}",
            merchant_id=merchant_id,
            sku=None,
            source_kind="merchant_terms",
            text=MERCHANT_TERMS_TEMPLATE.format(display_name=display_name)
            + " "
            + SYNTHETIC_NOTICE,
        ),
        SemanticEvidenceEntry(
            evidence_id=f"sbev-{sku}-terms-{EVIDENCE_VERSION}",
            merchant_id=merchant_id,
            sku=sku,
            source_kind="product_terms",
            text=f"{identity_text} {billing_text} {SYNTHETIC_NOTICE}",
        ),
        SemanticEvidenceEntry(
            evidence_id=f"sbev-{sku}-listing-{EVIDENCE_VERSION}",
            merchant_id=merchant_id,
            sku=sku,
            source_kind="product_description",
            text=(
                f"{attributes.title}. Listed by {display_name} in the MandateGuard "
                f"sandbox. {content_text} {purpose_text} {SYNTHETIC_NOTICE}"
            ),
        ),
    )
    family = _family_for(declaration)
    recurring = declaration.billing_model == "RECURRING"
    product = SandboxProduct(
        catalog_product_id=sandbox_catalog_id(merchant_id, sku),
        merchant_id=merchant_id,
        merchant_display_name=display_name,
        sku=sku,
        name=attributes.title,
        brand=attributes.brand_hint or display_name,
        category_id="onboarded",
        category_label=attributes.category_label,
        category_group="Onboarded",
        description=(
            f"{attributes.title}. Synthetic sandbox listing created by simulated "
            f"merchant onboarding from marketplace listing {attributes.listing_id}. "
            "The original marketplace listing is unchanged and remains untrusted."
        ),
        price_minor=declaration.price_minor,
        currency=CURRENCY,
        billing_model=declaration.billing_model,
        recurring=recurring,
        purpose_claims=declaration.purposes,
        exclusion_claims=(
            CLEARED_EXCLUSIONS
            if declaration.content_classification == "NO_RESTRICTED_CONTENT"
            else ("gambling PRESENT",)
            if declaration.content_classification == "GAMBLING_PRESENT"
            else ()
        ),
        recurrence_declaration=(
            "RENEWS_UNTIL_CANCELLED"
            if recurring
            else "NOT_RECORDED"
            if declaration.billing_model == "NOT_DECLARED"
            else "RECORDS_DISAGREE"
            if declaration.billing_model == "CONFLICTED"
            else "SETTLED_ONCE"
        ),
        effective_from=EFFECTIVE_FROM,
        evidence_version=EVIDENCE_VERSION,
        evidence_family=family,
        keywords=tuple(
            dict.fromkeys(
                [attributes.category_label.lower()]
                + ([attributes.brand_hint.lower()] if attributes.brand_hint else [])
            )
        )[:32]
        or ("onboarded",),
        evidence_ids=tuple(entry.evidence_id for entry in evidence),
    )
    return OnboardedMerchant(
        merchant_id=merchant_id,
        display_name=display_name,
        sku=sku,
        product=product,
        evidence=evidence,
        source_listing_id=attributes.listing_id,
        source_listing_title=attributes.title,
        created_at=moment.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )


def onboarded_store(merchant: OnboardedMerchant) -> TrustedCommerceStore:
    """A trusted store holding exactly one freshly declared listing.

    Authorization for an onboarded listing runs against this store and no other.
    That keeps the onboarded record out of the shared sandbox catalogue - it
    belongs to one session - and makes the claim being tested precise: this
    merchant, this SKU, this evidence, judged on its own.
    """

    return TrustedCommerceStore(
        snapshot_id=f"sandbox-onboarded-{merchant.merchant_id}",
        products=(merchant.product.commerce_product(),),
        evidence_entries=merchant.evidence,
    )
