"""Strict trusted catalog and merchant-evidence storage for INT-1."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.semantic.evidence import SemanticEvidenceEntry

from mandateguard.intelligence.models import (
    CommerceProduct,
    RetrievalDocument,
    RetrievalSource,
)


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CATALOG_FIELDS = frozenset({"snapshot_id", "products"})
_PRODUCT_FIELDS = frozenset(
    {
        "merchant_id",
        "sku",
        "name",
        "description",
        "effective_unit_price_minor",
        "currency",
        "recurring",
        "tags",
        "evidence_ids",
    }
)
_TERMS_FIELDS = frozenset({"entries"})
_EVIDENCE_FIELDS = frozenset(
    {"evidence_id", "merchant_id", "sku", "source_kind", "text"}
)


class CommerceStoreError(RuntimeError):
    """The registered catalog/evidence source is invalid or unavailable."""


class UnknownProductError(CommerceStoreError):
    pass


class UnknownEvidenceError(CommerceStoreError):
    pass


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _read_json(path: Path) -> object:
    if not isinstance(path, Path):
        raise TypeError("registered store paths must be pathlib.Path")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CommerceStoreError("registered commerce source is unavailable") from exc
    try:
        return json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_json_number,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CommerceStoreError("registered commerce source is malformed") from exc


def _exact(value: object, fields: frozenset[str], location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise CommerceStoreError(f"{location} has unexpected or missing fields")
    return value


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _TOKEN_RE.findall(text))


@dataclass(frozen=True, slots=True, init=False)
class TrustedCommerceStore:
    """Immutable application-registered products and evidence.

    Buyer-selected IDs are merely lookup requests. Every text returned for
    authorization is re-resolved from this store.
    """

    snapshot_id: str
    products: tuple[CommerceProduct, ...]
    evidence_entries: tuple[SemanticEvidenceEntry, ...]
    _products: Mapping[tuple[str, str], CommerceProduct]
    _evidence: Mapping[str, SemanticEvidenceEntry]

    def __init__(
        self,
        *,
        snapshot_id: str,
        products: tuple[CommerceProduct, ...],
        evidence_entries: tuple[SemanticEvidenceEntry, ...],
    ) -> None:
        if not isinstance(snapshot_id, str) or not snapshot_id or len(snapshot_id) > 256:
            raise ValueError("snapshot_id must be a bounded non-empty string")
        if not isinstance(products, tuple) or not products:
            raise ValueError("products must be a non-empty tuple")
        if not all(isinstance(item, CommerceProduct) for item in products):
            raise TypeError("products contains an invalid CommerceProduct")
        if not isinstance(evidence_entries, tuple) or not evidence_entries:
            raise ValueError("evidence_entries must be a non-empty tuple")
        if not all(
            isinstance(item, SemanticEvidenceEntry) for item in evidence_entries
        ):
            raise TypeError("evidence_entries contains an invalid entry")

        product_map: dict[tuple[str, str], CommerceProduct] = {}
        for product in products:
            key = (product.merchant_id, product.sku)
            if key in product_map:
                raise ValueError("merchant/SKU pairs must be unique")
            product_map[key] = product

        evidence_map: dict[str, SemanticEvidenceEntry] = {}
        for entry in evidence_entries:
            if entry.evidence_id in evidence_map:
                raise ValueError("evidence IDs must be globally unique")
            evidence_map[entry.evidence_id] = entry

        for product in products:
            if not product.evidence_ids:
                raise ValueError("every product must register trusted evidence IDs")
            for evidence_id in product.evidence_ids:
                entry = evidence_map.get(evidence_id)
                if entry is None:
                    raise ValueError("product references unknown trusted evidence")
                if entry.merchant_id != product.merchant_id or entry.sku not in {
                    None,
                    product.sku,
                }:
                    raise ValueError("product evidence identity binding is invalid")

        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(
            self,
            "products",
            tuple(sorted(products, key=lambda item: (item.merchant_id, item.sku))),
        )
        object.__setattr__(
            self,
            "evidence_entries",
            tuple(
                sorted(
                    evidence_entries,
                    key=lambda item: (
                        item.merchant_id,
                        item.sku is not None,
                        item.sku or "",
                        item.evidence_id,
                    ),
                )
            ),
        )
        object.__setattr__(self, "_products", MappingProxyType(product_map))
        object.__setattr__(self, "_evidence", MappingProxyType(evidence_map))

    @classmethod
    def from_files(
        cls, *, catalog_path: Path, merchant_terms_path: Path
    ) -> TrustedCommerceStore:
        catalog = _exact(_read_json(catalog_path), _CATALOG_FIELDS, "catalog")
        raw_products = catalog["products"]
        if not isinstance(raw_products, list) or not raw_products:
            raise CommerceStoreError("catalog.products must be a non-empty array")
        products: list[CommerceProduct] = []
        for index, raw_product in enumerate(raw_products):
            item = _exact(
                raw_product, _PRODUCT_FIELDS, f"catalog.products[{index}]"
            )
            products.append(
                CommerceProduct(
                    merchant_id=item["merchant_id"],
                    sku=item["sku"],
                    name=item["name"],
                    description=item["description"],
                    effective_unit_price_minor=item[
                        "effective_unit_price_minor"
                    ],
                    currency=item["currency"],
                    recurring=item["recurring"],
                    tags=item["tags"],
                    evidence_ids=item["evidence_ids"],
                )
            )

        terms = _exact(
            _read_json(merchant_terms_path), _TERMS_FIELDS, "merchant_terms"
        )
        raw_entries = terms["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise CommerceStoreError("merchant_terms.entries must be non-empty")
        evidence: list[SemanticEvidenceEntry] = []
        for index, raw_entry in enumerate(raw_entries):
            entry = _exact(
                raw_entry,
                _EVIDENCE_FIELDS,
                f"merchant_terms.entries[{index}]",
            )
            evidence.append(
                SemanticEvidenceEntry(
                    evidence_id=entry["evidence_id"],
                    merchant_id=entry["merchant_id"],
                    sku=entry["sku"],
                    source_kind=entry["source_kind"],
                    text=entry["text"],
                )
            )
        try:
            return cls(
                snapshot_id=catalog["snapshot_id"],
                products=tuple(products),
                evidence_entries=tuple(evidence),
            )
        except (TypeError, ValueError) as exc:
            raise CommerceStoreError("registered commerce sources are inconsistent") from exc

    def get_product(self, *, merchant_id: str, sku: str) -> CommerceProduct:
        product = self._products.get((merchant_id, sku))
        if product is None:
            raise UnknownProductError("registered product was not found")
        return product

    def search_catalog(
        self,
        query: str,
        *,
        currency: str | None = None,
        max_unit_price_minor: int | None = None,
        merchant_ids: tuple[str, ...] | None = None,
        sku_ids: tuple[str, ...] | None = None,
        recurring: bool | None = None,
        limit: int = 10,
    ) -> tuple[CommerceProduct, ...]:
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("query must be a bounded non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if max_unit_price_minor is not None and (
            isinstance(max_unit_price_minor, bool)
            or not isinstance(max_unit_price_minor, int)
            or max_unit_price_minor < 0
        ):
            raise ValueError("max_unit_price_minor must be non-negative or null")
        query_tokens = _tokens(query)
        ranked: list[tuple[int, str, str, CommerceProduct]] = []
        for product in self.products:
            if currency is not None and product.currency != currency:
                continue
            if (
                max_unit_price_minor is not None
                and product.effective_unit_price_minor > max_unit_price_minor
            ):
                continue
            if merchant_ids is not None and product.merchant_id not in merchant_ids:
                continue
            if sku_ids is not None and product.sku not in sku_ids:
                continue
            if recurring is not None and product.recurring is not recurring:
                continue
            # Exact product naming should outweigh generic purpose/tag overlap.
            score = (
                3 * len(query_tokens & _tokens(product.name))
                + 2 * len(query_tokens & _tokens(" ".join(product.tags)))
                + len(query_tokens & _tokens(product.description))
            )
            ranked.append((-score, product.merchant_id, product.sku, product))
        ranked.sort(key=lambda item: item[:3])
        return tuple(item[3] for item in ranked[:limit])

    def resolve_evidence_ids(
        self,
        evidence_ids: tuple[str, ...],
        *,
        merchant_id: str,
        sku: str,
    ) -> tuple[SemanticEvidenceEntry, ...]:
        if not isinstance(evidence_ids, tuple):
            raise TypeError("evidence_ids must be a tuple")
        resolved: list[SemanticEvidenceEntry] = []
        for evidence_id in evidence_ids:
            entry = self._evidence.get(evidence_id)
            if entry is None:
                raise UnknownEvidenceError("buyer requested an unknown evidence ID")
            if entry.merchant_id != merchant_id or entry.sku not in {None, sku}:
                raise UnknownEvidenceError(
                    "buyer requested evidence outside the proposal identity"
                )
            resolved.append(entry)
        return tuple(resolved)

    def evidence_for_product(
        self, *, merchant_id: str, sku: str
    ) -> tuple[SemanticEvidenceEntry, ...]:
        self.get_product(merchant_id=merchant_id, sku=sku)
        return tuple(
            entry
            for entry in self.evidence_entries
            if entry.merchant_id == merchant_id and entry.sku in {None, sku}
        )

    def retrieval_documents(
        self, *, merchant_id: str, sku: str
    ) -> tuple[RetrievalDocument, ...]:
        return tuple(
            RetrievalDocument(
                document_id=f"evidence.{entry.evidence_id}",
                source_type=RetrievalSource.MERCHANT_EVIDENCE,
                text=entry.text,
                merchant_id=entry.merchant_id,
                sku=entry.sku,
                evidence_id=entry.evidence_id,
            )
            for entry in self.evidence_for_product(
                merchant_id=merchant_id, sku=sku
            )
        )

    def catalog_snapshot(self, *, merchant_id: str) -> CatalogSnapshot:
        merchant_products = tuple(
            product for product in self.products if product.merchant_id == merchant_id
        )
        if not merchant_products:
            raise UnknownProductError("registered merchant was not found")
        currencies = {product.currency for product in merchant_products}
        if len(currencies) != 1:
            raise CommerceStoreError("one merchant snapshot must use one currency")
        return CatalogSnapshot(
            snapshot_id=f"{self.snapshot_id}.{merchant_id}",
            merchant_id=merchant_id,
            currency=currencies.pop(),
            items=tuple(
                CatalogItem(
                    sku=product.sku,
                    merchant_id=product.merchant_id,
                    effective_unit_price_minor=product.effective_unit_price_minor,
                    recurring=product.recurring,
                )
                for product in merchant_products
            ),
        )
