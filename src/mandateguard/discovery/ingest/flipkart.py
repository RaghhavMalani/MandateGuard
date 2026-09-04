"""Adapter: PromptCloud Flipkart CSV -> normalized discovery listings."""

from __future__ import annotations

from collections import Counter
import csv
from typing import Iterator, Sequence

from mandateguard.discovery.schema import DiscoveryProduct, catalog_product_id
from mandateguard.discovery.ingest.normalize import (
    MIN_TOP_CATEGORY_SUPPORT,
    apply_taxonomy_floor,
    collapse_text,
    normalize_description,
    parse_category_tree,
    parse_price_minor,
    parse_rating,
    raw_row_sha256,
)


ADAPTER_ID = "flipkart_promptcloud_csv"
SOURCE_ID = "flipkart"
CURRENCY = "INR"

#: The listing platform. Not a seller of record: the export carries no seller
#: identity, which is exactly what the transactability diagnostic reports.
MARKETPLACE = "flipkart.com"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "uniq_id",
    "product_url",
    "product_name",
    "product_category_tree",
    "retail_price",
    "discounted_price",
    "description",
    "product_rating",
    "brand",
)


class AdapterError(ValueError):
    """The upstream file does not match the adapter's expected shape."""


def read_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(text.splitlines())
    missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or ())]
    if missing:
        raise AdapterError(f"source file is missing columns: {missing}")
    return [row for row in reader]


def supported_top_categories(rows: Sequence[dict[str, str]]) -> frozenset[str]:
    """Top segments the source itself uses often enough to be a taxonomy node."""

    counts: Counter[str] = Counter()
    for row in rows:
        path = parse_category_tree(row.get("product_category_tree"))
        if path:
            counts[path[0]] += 1
    return frozenset(
        name for name, count in counts.items() if count >= MIN_TOP_CATEGORY_SUPPORT
    )


def normalize_rows(rows: Sequence[dict[str, str]]) -> Iterator[tuple[DiscoveryProduct, dict[str, bool]]]:
    """Yield ``(product, normalization_flags)`` for every usable row."""

    supported = supported_top_categories(rows)
    for row in rows:
        source_product_id = collapse_text(row.get("uniq_id"))
        title = collapse_text(row.get("product_name"))
        if not source_product_id or not title:
            continue
        description, title_stripped, price_stripped = normalize_description(
            row.get("description"), title=title
        )
        path = apply_taxonomy_floor(
            parse_category_tree(row.get("product_category_tree")),
            supported_top_categories=supported,
        )
        price_minor = parse_price_minor(
            row.get("discounted_price"), row.get("retail_price")
        )
        brand = collapse_text(row.get("brand")) or None
        url = collapse_text(row.get("product_url")) or None
        if url is not None and not url.startswith(("http://", "https://")):
            url = None
        product = DiscoveryProduct(
            catalog_product_id=catalog_product_id(SOURCE_ID, source_product_id),
            source=SOURCE_ID,
            source_product_id=source_product_id,
            title=title[:400],
            description=description,
            brand=brand,
            category_path=path,
            price_minor=price_minor,
            currency=CURRENCY,
            merchant_or_seller=MARKETPLACE,
            rating=parse_rating(row.get("product_rating")),
            product_url=url,
            raw_source_sha256=raw_row_sha256(row),
        )
        yield product, {
            "title_prefix_stripped": title_stripped,
            "price_fragment_stripped": price_stripped,
            "taxonomy_demoted": path[0] not in supported,
            "price_missing": price_minor is None,
        }
