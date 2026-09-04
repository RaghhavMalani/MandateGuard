"""Dataset ingestion: schema, normalization, dedup, and provenance."""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path

import pytest

from mandateguard.discovery.catalog import CatalogUnavailableError, load_catalog
from mandateguard.discovery.ingest import flipkart, registry
from mandateguard.discovery.ingest.normalize import (
    MIN_TOP_CATEGORY_SUPPORT,
    UNCATEGORIZED,
    apply_taxonomy_floor,
    normalize_description,
    parse_category_tree,
    parse_price_minor,
    parse_rating,
    raw_row_sha256,
)
from mandateguard.discovery.ingest.pipeline import normalize_and_dedup, write_catalog
from mandateguard.discovery.ingest.sources import get_source
from mandateguard.discovery.schema import (
    DiscoveryProduct,
    DiscoverySchemaError,
    NORMALIZED_FIELDS,
    catalog_product_id,
)

from tests.discovery_factories import build_product


COLUMNS = (
    "uniq_id",
    "crawl_timestamp",
    "product_url",
    "product_name",
    "product_category_tree",
    "pid",
    "retail_price",
    "discounted_price",
    "image",
    "is_FK_Advantage_product",
    "description",
    "product_rating",
    "overall_rating",
    "brand",
    "product_specifications",
)


def _csv(rows: list[dict[str, str]]) -> str:
    """Render rows the way the upstream export does, quoting included."""

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _row(
    uniq_id: str,
    name: str,
    tree: str,
    price: str = "1299",
    description: str = "A compact desk lamp.",
    brand: str = "StudyGlow",
    rating: str = "4",
) -> dict[str, str]:
    return {
        "uniq_id": uniq_id,
        "crawl_timestamp": "2016-03-25 22:59:23 +0000",
        "product_url": f"http://example.invalid/p/{uniq_id}",
        "product_name": name,
        "product_category_tree": tree,
        "pid": f"PID{uniq_id}",
        "retail_price": "1999",
        "discounted_price": price,
        "image": "[]",
        "is_FK_Advantage_product": "False",
        "description": description,
        "product_rating": rating,
        "overall_rating": rating,
        "brand": brand,
        "product_specifications": "{}",
    }


def test_the_registered_normalized_schema_is_the_one_the_brief_asked_for() -> None:
    assert NORMALIZED_FIELDS == (
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


def test_catalog_product_id_is_a_pure_function_of_source_identity() -> None:
    first = catalog_product_id("flipkart", "abc123")
    assert first == catalog_product_id("flipkart", "abc123")
    assert first != catalog_product_id("mandateguard", "abc123")
    assert first.startswith("flipkart.")


def test_a_row_missing_provenance_is_rejected() -> None:
    with pytest.raises(DiscoverySchemaError):
        DiscoveryProduct(
            catalog_product_id="flipkart.abc",
            source="flipkart",
            source_product_id="abc",
            title="Lamp",
            description="",
            brand=None,
            category_path=("Lighting",),
            price_minor=100,
            currency="INR",
            merchant_or_seller=None,
            raw_source_sha256="not-a-digest",
        )


def test_price_parsing_prefers_the_discounted_price_and_returns_minor_units() -> None:
    assert parse_price_minor("499.0", "1099") == 49900
    assert parse_price_minor("", "1,099") == 109900
    assert parse_price_minor("", "") is None
    assert parse_price_minor("not a number") is None


def test_rating_outside_the_scale_is_dropped_rather_than_clamped() -> None:
    assert parse_rating("4.5") == 4.5
    assert parse_rating("No rating available") is None
    assert parse_rating("9") is None


def test_description_normalization_removes_the_repeated_title_and_stale_price() -> None:
    text, title_stripped, price_stripped = normalize_description(
        "StudyGlow Desk Lamp\n   Price: Rs. 1299\n\tA compact task lamp.",
        title="StudyGlow Desk Lamp",
    )
    assert title_stripped is True
    assert price_stripped is True
    assert text == "A compact task lamp."


def test_description_normalization_keeps_a_description_that_is_only_the_title() -> None:
    """Stripping everything would leave the listing with no description at all."""

    text, stripped, _ = normalize_description("Desk Lamp", title="Desk Lamp")
    assert stripped is False
    assert text == "Desk Lamp"


def test_category_tree_parsing_handles_the_python_literal_column() -> None:
    assert parse_category_tree('["Clothing >> Kids\' Clothing >> Track Pants"]') == (
        "Clothing",
        "Kids' Clothing",
        "Track Pants",
    )
    assert parse_category_tree("") == ()
    assert parse_category_tree("[]") == ()


def test_a_top_segment_the_source_barely_uses_is_demoted_not_accepted() -> None:
    supported = frozenset({"Clothing"})
    assert apply_taxonomy_floor(("Clothing", "Kurtas"), supported_top_categories=supported) == (
        "Clothing",
        "Kurtas",
    )
    demoted = apply_taxonomy_floor(
        ("Vishudh Printed Women's Straight Kurta",), supported_top_categories=supported
    )
    assert demoted[0] == UNCATEGORIZED


def test_raw_row_digest_commits_to_the_upstream_row() -> None:
    row = {"b": "2", "a": "1"}
    assert raw_row_sha256(row) == raw_row_sha256({"a": "1", "b": "2"})
    assert raw_row_sha256(row) != raw_row_sha256({"a": "1", "b": "3"})


def test_the_flipkart_adapter_normalizes_a_row_into_the_registered_schema() -> None:
    text = _csv(
        [
            _row(f"id{index:04d}", "StudyGlow Desk Lamp", '["Home Decor >> Lighting"]')
            for index in range(MIN_TOP_CATEGORY_SUPPORT + 1)
        ]
    )
    rows = flipkart.read_rows(text)
    products = [product for product, _ in flipkart.normalize_rows(rows)]
    assert len(products) == MIN_TOP_CATEGORY_SUPPORT + 1
    product = products[0]
    assert product.source == "flipkart"
    assert product.currency == "INR"
    assert product.price_minor == 129900
    assert product.category_path == ("Home Decor", "Lighting")
    assert product.merchant_or_seller == "flipkart.com"


def test_the_adapter_refuses_a_file_that_is_not_the_registered_shape() -> None:
    with pytest.raises(flipkart.AdapterError):
        flipkart.read_rows("a,b,c\n1,2,3\n")


def test_identical_listings_are_deduplicated_and_variants_are_kept() -> None:
    source = get_source("flipkart")
    rows = [
        _row(f"id{index:04d}", "StudyGlow Desk Lamp", '["Home Decor >> Lighting"]')
        for index in range(MIN_TOP_CATEGORY_SUPPORT + 1)
    ]
    # A genuine sibling SKU: same name, different price.
    rows.append(
        _row("idvar", "StudyGlow Desk Lamp", '["Home Decor >> Lighting"]', price="1499")
    )
    products, rows_read, normalized, _ = normalize_and_dedup(source, _csv(rows))
    assert rows_read == MIN_TOP_CATEGORY_SUPPORT + 2
    assert normalized == MIN_TOP_CATEGORY_SUPPORT + 2
    # All the identical rows collapse to one; the price variant survives.
    assert len(products) == 2
    assert {product.price_minor for product in products} == {129900, 149900}


def test_the_registry_adapter_marks_registered_products_by_merchant_and_sku() -> None:
    payload = json.dumps(
        {
            "snapshot_id": "snap",
            "products": [
                {
                    "merchant_id": "merchant-scholarly",
                    "sku": "studyglow-desk-lamp",
                    "name": "StudyGlow Desk Lamp",
                    "description": "A compact task lamp.",
                    "effective_unit_price_minor": 129900,
                    "currency": "INR",
                    "recurring": False,
                    "tags": ["desk lamp", "reading"],
                    "evidence_ids": ["a", "b"],
                }
            ],
        }
    )
    products = [product for product, _ in registry.normalize_rows(registry.read_rows(payload))]
    assert len(products) == 1
    product = products[0]
    assert product.source == "mandateguard"
    assert product.source_product_id == "merchant-scholarly/studyglow-desk-lamp"
    assert product.merchant_or_seller == "merchant-scholarly"
    assert "desk lamp" in product.description


def test_the_committed_catalog_is_byte_identical_for_the_same_rows(tmp_path: Path) -> None:
    products = (build_product(source_product_id="a"), build_product(source_product_id="b"))
    first_bytes, first_digest = write_catalog(products, tmp_path / "one.jsonl.gz")
    second_bytes, second_digest = write_catalog(products, tmp_path / "two.jsonl.gz")
    assert first_bytes == second_bytes
    assert first_digest == second_digest


def test_loading_a_catalog_round_trips_every_normalized_field(tmp_path: Path) -> None:
    products = (build_product(source_product_id="a"), build_product(source_product_id="b"))
    _, digest = write_catalog(products, tmp_path / "discovery_catalog.jsonl.gz")
    (tmp_path / "discovery_catalog.manifest.json").write_text(
        json.dumps({"import": {"catalog_sha256": digest}, "trust_tier": "DISCOVERY_LISTING"}),
        encoding="utf-8",
    )
    catalog = load_catalog(tmp_path)
    assert len(catalog) == 2
    assert catalog.catalog_sha256 == digest
    assert catalog[0].to_mapping() == products[0].to_mapping()
    assert catalog.position(products[1].catalog_product_id) == 1


def test_a_catalog_that_disagrees_with_its_manifest_digest_is_refused(tmp_path: Path) -> None:
    write_catalog((build_product(),), tmp_path / "discovery_catalog.jsonl.gz")
    (tmp_path / "discovery_catalog.manifest.json").write_text(
        json.dumps({"import": {"catalog_sha256": "0" * 64}}), encoding="utf-8"
    )
    with pytest.raises(CatalogUnavailableError):
        load_catalog(tmp_path)


def test_a_missing_catalog_is_an_error_not_an_empty_result(tmp_path: Path) -> None:
    with pytest.raises(CatalogUnavailableError) as error:
        load_catalog(tmp_path)
    assert "import_discovery_catalog" in str(error.value)


def test_a_corrupt_catalog_line_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    path = tmp_path / "discovery_catalog.jsonl.gz"
    path.write_bytes(gzip.compress(b'{"catalog_product_id": "broken"}\n'))
    with pytest.raises(CatalogUnavailableError):
        load_catalog(tmp_path)
