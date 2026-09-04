"""Adapter: the registered trusted merchant catalog -> discovery listings.

The eight application-registered products are indexed alongside the seventeen
thousand crawled ones, in the same index, ranked by the same scorer. That is
deliberate. The whole point of the transactability diagnostic is only visible
when both kinds of listing appear in one result list and the difference between
them is a property of the listing rather than a property of the page it is on.

Indexing a registered product here does **not** make its trusted evidence
reachable from this module. Evidence still resolves only through
``TrustedCommerceStore``, keyed by merchant and SKU. This adapter copies the
merchandising fields and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Sequence

from mandateguard.discovery.ingest.normalize import collapse_text, raw_row_sha256
from mandateguard.discovery.schema import DiscoveryProduct, catalog_product_id


ADAPTER_ID = "mandateguard_registry_json"
SOURCE_ID = "mandateguard"
CURRENCY = "INR"


def read_rows(text: str) -> list[dict[str, object]]:
    payload = json.loads(text)
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list) or not products:
        raise ValueError("registered catalog contains no products")
    return [dict(item) for item in products]


def normalize_rows(
    rows: Sequence[dict[str, object]],
) -> Iterator[tuple[DiscoveryProduct, dict[str, bool]]]:
    for row in rows:
        merchant_id = collapse_text(str(row.get("merchant_id", "")))
        sku = collapse_text(str(row.get("sku", "")))
        title = collapse_text(str(row.get("name", "")))
        if not merchant_id or not sku or not title:
            continue
        tags = row.get("tags")
        tag_text = (
            ", ".join(str(item) for item in tags) if isinstance(tags, list) else ""
        )
        description = collapse_text(str(row.get("description", "")))
        if tag_text:
            description = f"{description} Tags: {tag_text}.".strip()
        price = row.get("effective_unit_price_minor")
        source_product_id = f"{merchant_id}/{sku}"
        yield (
            DiscoveryProduct(
                catalog_product_id=catalog_product_id(SOURCE_ID, source_product_id),
                source=SOURCE_ID,
                source_product_id=source_product_id,
                title=title[:400],
                description=description[:4000],
                brand=None,
                # A registered product's shelf is the merchant's own statement of
                # what it sells, so the path is deliberately shallow and honest.
                category_path=("Registered Merchant Catalog", merchant_id),
                price_minor=int(price) if isinstance(price, int) else None,
                currency=str(row.get("currency") or CURRENCY),
                merchant_or_seller=merchant_id,
                rating=None,
                product_url=None,
                raw_source_sha256=raw_row_sha256(row),
            ),
            {"registered_product": True},
        )
