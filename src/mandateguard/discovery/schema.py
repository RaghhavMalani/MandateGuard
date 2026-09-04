"""Normalized discovery-catalog schema.

A discovery product is a *listing*: something an agent can find and reason
about. It is deliberately **not** merchant authorization evidence. Nothing in
this module may be resolved as trusted evidence, and no field here participates
in a Tier A/B check. The boundary is asserted by
``mandateguard.discovery.trust`` and by the tests that import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Mapping


SCHEMA_VERSION = "discovery-catalog-v1"

#: The one commerce role a discovery listing may play.
DISCOVERY_TRUST_TIER = "DISCOVERY_LISTING"

#: Registered normalized fields, in canonical order.
NORMALIZED_FIELDS: tuple[str, ...] = (
    "catalog_product_id",
    "source",
    "source_product_id",
    "title",
    "description",
    "brand",
    "category_path",
    "price_minor",
    "currency",
    "merchant_or_seller",
    "rating",
    "product_url",
    "raw_source_sha256",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_TITLE = 400
_MAX_DESCRIPTION = 4000
_MAX_CATEGORY_DEPTH = 12


class DiscoverySchemaError(ValueError):
    """A normalized row violates the registered discovery schema."""


def catalog_product_id(source: str, source_product_id: str) -> str:
    """Derive a stable, collision-resistant id from source identity.

    The id is a pure function of ``(source, source_product_id)``, so re-running
    the importer over the same upstream rows reproduces the same catalog ids and
    the same frozen index offsets.
    """

    if not isinstance(source, str) or not _ID_RE.fullmatch(source):
        raise DiscoverySchemaError("source must be a bounded lowercase identifier")
    if not isinstance(source_product_id, str) or not source_product_id:
        raise DiscoverySchemaError("source_product_id must be a non-empty string")
    digest = sha256(f"{source}\x1f{source_product_id}".encode("utf-8")).hexdigest()
    return f"{source}.{digest[:24]}"


@dataclass(frozen=True, slots=True)
class DiscoveryProduct:
    """One normalized listing from an imported public dataset."""

    catalog_product_id: str
    source: str
    source_product_id: str
    title: str
    description: str
    brand: str | None
    category_path: tuple[str, ...]
    price_minor: int | None
    currency: str
    merchant_or_seller: str | None
    raw_source_sha256: str
    rating: float | None = None
    product_url: str | None = None

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.catalog_product_id):
            raise DiscoverySchemaError("catalog_product_id must be a bounded identifier")
        if not _ID_RE.fullmatch(self.source):
            raise DiscoverySchemaError("source must be a bounded lowercase identifier")
        if not isinstance(self.source_product_id, str) or not self.source_product_id:
            raise DiscoverySchemaError("source_product_id must be non-empty")
        if not isinstance(self.title, str) or not self.title.strip():
            raise DiscoverySchemaError("title must be a non-empty string")
        if len(self.title) > _MAX_TITLE:
            raise DiscoverySchemaError("title exceeds the bounded length")
        if not isinstance(self.description, str):
            raise DiscoverySchemaError("description must be a string")
        if len(self.description) > _MAX_DESCRIPTION:
            raise DiscoverySchemaError("description exceeds the bounded length")
        if self.brand is not None and (
            not isinstance(self.brand, str) or not self.brand.strip()
        ):
            raise DiscoverySchemaError("brand must be null or a non-empty string")
        if not isinstance(self.category_path, tuple) or not self.category_path:
            raise DiscoverySchemaError("category_path must be a non-empty tuple")
        if len(self.category_path) > _MAX_CATEGORY_DEPTH:
            raise DiscoverySchemaError("category_path is deeper than the bounded limit")
        if not all(
            isinstance(item, str) and item.strip() for item in self.category_path
        ):
            raise DiscoverySchemaError("category_path segments must be non-empty strings")
        if self.price_minor is not None and (
            isinstance(self.price_minor, bool)
            or not isinstance(self.price_minor, int)
            or self.price_minor < 0
        ):
            raise DiscoverySchemaError("price_minor must be null or a non-negative integer")
        if not _CURRENCY_RE.fullmatch(self.currency):
            raise DiscoverySchemaError("currency must be an ISO-4217 alphabetic code")
        if self.merchant_or_seller is not None and (
            not isinstance(self.merchant_or_seller, str)
            or not self.merchant_or_seller.strip()
        ):
            raise DiscoverySchemaError(
                "merchant_or_seller must be null or a non-empty string"
            )
        if self.rating is not None and (
            isinstance(self.rating, bool)
            or not isinstance(self.rating, (int, float))
            or not 0.0 <= float(self.rating) <= 5.0
        ):
            raise DiscoverySchemaError("rating must be null or within [0, 5]")
        if self.product_url is not None and not str(self.product_url).startswith(
            ("http://", "https://")
        ):
            raise DiscoverySchemaError("product_url must be null or an absolute URL")
        if not _SHA256_RE.fullmatch(self.raw_source_sha256):
            raise DiscoverySchemaError("raw_source_sha256 must be a hex SHA-256 digest")

    @property
    def top_category(self) -> str:
        return self.category_path[0]

    @property
    def leaf_category(self) -> str:
        return self.category_path[-1]

    @property
    def category_text(self) -> str:
        return " > ".join(self.category_path)

    def indexed_text(self) -> str:
        """The text the retrieval layer indexes for this listing."""

        parts = [self.title, self.brand or "", self.category_text, self.description]
        return "\n".join(part for part in parts if part)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "catalog_product_id": self.catalog_product_id,
            "source": self.source,
            "source_product_id": self.source_product_id,
            "title": self.title,
            "description": self.description,
            "brand": self.brand,
            "category_path": list(self.category_path),
            "price_minor": self.price_minor,
            "currency": self.currency,
            "merchant_or_seller": self.merchant_or_seller,
            "rating": self.rating,
            "product_url": self.product_url,
            "raw_source_sha256": self.raw_source_sha256,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> DiscoveryProduct:
        if not isinstance(payload, Mapping):
            raise DiscoverySchemaError("row must be a JSON object")
        unknown = set(payload) - set(NORMALIZED_FIELDS)
        if unknown:
            raise DiscoverySchemaError(f"unknown discovery fields: {sorted(unknown)}")
        missing = {
            name
            for name in NORMALIZED_FIELDS
            if name not in payload and name not in {"rating", "product_url"}
        }
        if missing:
            raise DiscoverySchemaError(f"missing discovery fields: {sorted(missing)}")
        category = payload["category_path"]
        if not isinstance(category, (list, tuple)):
            raise DiscoverySchemaError("category_path must be a list")
        return cls(
            catalog_product_id=payload["catalog_product_id"],
            source=payload["source"],
            source_product_id=payload["source_product_id"],
            title=payload["title"],
            description=payload["description"],
            brand=payload["brand"],
            category_path=tuple(category),
            price_minor=payload["price_minor"],
            currency=payload["currency"],
            merchant_or_seller=payload["merchant_or_seller"],
            rating=payload.get("rating"),
            product_url=payload.get("product_url"),
            raw_source_sha256=payload["raw_source_sha256"],
        )
