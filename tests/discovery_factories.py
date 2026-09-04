"""Small factories for discovery-layer tests."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.schema import DiscoveryProduct, catalog_product_id


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def build_product(
    *,
    source: str = "flipkart",
    source_product_id: str = "row-0001",
    title: str = "StudyGlow Desk Lamp",
    description: str = "A compact task lamp for reading desks and focused workspaces.",
    brand: str | None = "StudyGlow",
    category_path: tuple[str, ...] = ("Home Decor & Festive Needs", "Lighting", "Table Lamps"),
    price_minor: int | None = 129900,
    currency: str = "INR",
    merchant_or_seller: str | None = "flipkart.com",
    rating: float | None = 4.2,
    product_url: str | None = "https://example.invalid/p/studyglow",
) -> DiscoveryProduct:
    return DiscoveryProduct(
        catalog_product_id=catalog_product_id(source, source_product_id),
        source=source,
        source_product_id=source_product_id,
        title=title,
        description=description,
        brand=brand,
        category_path=category_path,
        price_minor=price_minor,
        currency=currency,
        merchant_or_seller=merchant_or_seller,
        rating=rating,
        product_url=product_url,
        raw_source_sha256=_digest(f"{source}:{source_product_id}"),
    )


def build_catalog(products: tuple[DiscoveryProduct, ...] | None = None) -> DiscoveryCatalog:
    items = products or (
        build_product(source_product_id="row-0001"),
        build_product(
            source_product_id="row-0002",
            title="Aurora Focus Lamp",
            description="Dimmable focus lamp with a warm reading mode.",
            brand="Aurora",
            price_minor=149900,
        ),
        build_product(
            source_product_id="row-0003",
            title="Field Notebook Set",
            description="Three stitched notebooks for handwritten study notes.",
            brand="Fieldwork",
            category_path=("Pens & Stationery", "Notebooks"),
            price_minor=49900,
        ),
    )
    manifest: dict[str, Any] = {
        "trust_tier": "DISCOVERY_LISTING",
        "trust_note": "Discovery surface only.",
        "statistics": {
            "listings": len(items),
            "top_level_categories": len({item.top_category for item in items}),
        },
        "source": {
            "source_id": "flipkart",
            "display_name": "Test catalog",
            "licence": "CC BY-SA 4.0",
        },
    }
    return DiscoveryCatalog(
        products=items,
        catalog_sha256=_digest("test-catalog"),
        manifest=manifest,
        source_bytes=1024,
    )
