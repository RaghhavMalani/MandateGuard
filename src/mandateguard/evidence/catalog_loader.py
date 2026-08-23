"""Strict deterministic loading for PSP-configured catalog fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from mandateguard.evidence.provider import (
    CatalogSourceInvalidError,
    CatalogSourceUnavailableError,
)
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot


_CATALOG_FIELDS = frozenset({"snapshot_id", "merchant_id", "currency", "items"})
_ITEM_FIELDS = frozenset(
    {"sku", "merchant_id", "effective_unit_price_minor", "recurring"}
)


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _require_exact_fields(
    value: object, *, expected: frozenset[str], location: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a JSON object")
    actual = frozenset(value)
    if actual != expected:
        missing = ",".join(sorted(expected - actual)) or "none"
        unknown = ",".join(sorted(actual - expected)) or "none"
        raise ValueError(
            f"{location} has invalid fields (missing={missing}; unknown={unknown})"
        )
    return value


def _decode_catalog_fixture(raw: str) -> CatalogSnapshot:
    decoded = json.loads(
        raw,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_non_json_number,
    )
    catalog = _require_exact_fields(
        decoded,
        expected=_CATALOG_FIELDS,
        location="catalog",
    )
    raw_items = catalog["items"]
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("catalog.items must be a non-empty JSON array")

    items: list[CatalogItem] = []
    for index, raw_item in enumerate(raw_items):
        item = _require_exact_fields(
            raw_item,
            expected=_ITEM_FIELDS,
            location=f"catalog.items[{index}]",
        )
        items.append(
            CatalogItem(
                sku=item["sku"],
                merchant_id=item["merchant_id"],
                effective_unit_price_minor=item["effective_unit_price_minor"],
                recurring=item["recurring"],
            )
        )

    snapshot = CatalogSnapshot(
        snapshot_id=catalog["snapshot_id"],
        merchant_id=catalog["merchant_id"],
        currency=catalog["currency"],
        items=tuple(items),
    )
    if any(item.merchant_id != snapshot.merchant_id for item in snapshot.items):
        raise ValueError("every catalog item must belong to the catalog merchant")
    return snapshot


def load_catalog_fixture(fixture_path: Path) -> CatalogSnapshot:
    """Read a complete catalog from one trusted, PSP-configured local fixture."""

    if not isinstance(fixture_path, Path):
        raise TypeError("fixture_path must be pathlib.Path PSP configuration")
    try:
        raw = fixture_path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise CatalogSourceInvalidError(
            "configured catalog fixture is malformed"
        ) from exc
    except OSError as exc:
        raise CatalogSourceUnavailableError(
            "configured catalog fixture is unavailable"
        ) from exc
    try:
        return _decode_catalog_fixture(raw)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise CatalogSourceInvalidError(
            "configured catalog fixture is malformed"
        ) from exc


@dataclass(frozen=True, slots=True)
class FixtureCatalogEvidenceProvider:
    """Prototype provider backed by a trusted PSP-side fixture path."""

    fixture_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_path, Path):
            raise TypeError("fixture_path must be pathlib.Path PSP configuration")

    def fetch_catalog(self, *, merchant_id: str) -> CatalogSnapshot:
        if not isinstance(merchant_id, str) or not merchant_id or len(merchant_id) > 128:
            raise CatalogSourceInvalidError("requested merchant identity is invalid")
        snapshot = load_catalog_fixture(self.fixture_path)
        if snapshot.merchant_id != merchant_id:
            raise CatalogSourceInvalidError(
                "configured catalog merchant does not match the registered merchant"
            )
        return snapshot
