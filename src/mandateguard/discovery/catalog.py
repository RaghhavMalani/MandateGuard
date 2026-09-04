"""Runtime loader for the frozen discovery catalog. Standard library only."""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from mandateguard.discovery.schema import DiscoveryProduct, DiscoverySchemaError


CATALOG_FILENAME = "discovery_catalog.jsonl.gz"
MANIFEST_FILENAME = "discovery_catalog.manifest.json"


class CatalogUnavailableError(RuntimeError):
    """The processed discovery catalog is absent or unreadable.

    Raised, never swallowed: a silently empty catalog would make the product
    report "no product matches your intent" for every intent, which reads as a
    policy result rather than a missing file.
    """


@dataclass(frozen=True, slots=True)
class DiscoveryCatalog:
    """An immutable, positionally indexed view of the discovery listings.

    Position in ``products`` is the document id used by every frozen index, so
    the catalog and its indexes are only valid together. ``catalog_sha256``
    binds them: an index built against different bytes refuses to load.
    """

    products: tuple[DiscoveryProduct, ...]
    catalog_sha256: str
    manifest: Mapping[str, Any]
    source_bytes: int
    positions: Mapping[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.positions:
            object.__setattr__(
                self,
                "positions",
                {
                    product.catalog_product_id: position
                    for position, product in enumerate(self.products)
                },
            )

    def __len__(self) -> int:
        return len(self.products)

    def __iter__(self) -> Iterator[DiscoveryProduct]:
        return iter(self.products)

    def __getitem__(self, document_id: int) -> DiscoveryProduct:
        return self.products[document_id]

    def position(self, catalog_product_id: str) -> int | None:
        return self.positions.get(catalog_product_id)

    def top_categories(self) -> tuple[str, ...]:
        seen: dict[str, int] = {}
        for product in self.products:
            seen[product.top_category] = seen.get(product.top_category, 0) + 1
        return tuple(
            name for name, _ in sorted(seen.items(), key=lambda item: (-item[1], item[0]))
        )

    def statistics(self) -> dict[str, Any]:
        stats = self.manifest.get("statistics")
        return dict(stats) if isinstance(stats, Mapping) else {}

    def provenance(self) -> dict[str, Any]:
        source = self.manifest.get("source")
        source = dict(source) if isinstance(source, Mapping) else {}
        return {
            "source_id": source.get("source_id"),
            "display_name": source.get("display_name"),
            "publisher": source.get("publisher"),
            "landing_page": source.get("landing_page"),
            "licence": source.get("licence"),
            "licence_url": source.get("licence_url"),
            "attribution": source.get("attribution"),
            "notes": source.get("notes"),
            "catalog_sha256": self.catalog_sha256,
            "trust_tier": self.manifest.get("trust_tier", "DISCOVERY_LISTING"),
            "trust_note": self.manifest.get("trust_note", ""),
        }


def load_catalog(processed_dir: Path) -> DiscoveryCatalog:
    """Load the frozen catalog and its manifest from ``processed_dir``."""

    catalog_path = Path(processed_dir) / CATALOG_FILENAME
    manifest_path = Path(processed_dir) / MANIFEST_FILENAME
    try:
        payload = catalog_path.read_bytes()
    except OSError as error:
        raise CatalogUnavailableError(
            f"discovery catalog not found at {catalog_path}. "
            "Run scripts/import_discovery_catalog.py."
        ) from error
    digest = sha256(payload).hexdigest()
    try:
        text = gzip.decompress(payload).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as error:
        raise CatalogUnavailableError("discovery catalog is corrupt") from error
    products: list[DiscoveryProduct] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            products.append(DiscoveryProduct.from_mapping(json.loads(line)))
        except (json.JSONDecodeError, DiscoverySchemaError) as error:
            raise CatalogUnavailableError(
                f"discovery catalog line {number} is invalid: {error}"
            ) from error
    if not products:
        raise CatalogUnavailableError("discovery catalog contains no listings")
    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if manifest.get("import", {}).get("catalog_sha256") not in (None, digest):
        raise CatalogUnavailableError(
            "discovery catalog does not match the digest recorded in its manifest"
        )
    return DiscoveryCatalog(
        products=tuple(products),
        catalog_sha256=digest,
        manifest=manifest,
        source_bytes=len(payload),
    )


def indexed_streams(products: Sequence[DiscoveryProduct]) -> list[list[str]]:
    """Weighted token streams for every listing, in catalog order."""

    from mandateguard.discovery.index.lexical import field_terms

    return [
        field_terms(
            title=product.title,
            brand=product.brand,
            category=product.category_text,
            description=product.description,
        )
        for product in products
    ]
