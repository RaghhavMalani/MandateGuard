"""Import pipeline: fetch -> adapt -> normalize -> dedup -> commit with provenance.

The raw archive stays under ``data/import/`` and is never committed. What the
repository carries is the normalized, deduplicated catalog plus a manifest that
records where the data came from, under what licence, and exactly which bytes
produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
import urllib.request
import zipfile

from mandateguard.discovery.schema import (
    DiscoveryProduct,
    NORMALIZED_FIELDS,
    SCHEMA_VERSION,
)
from mandateguard.discovery.ingest import flipkart, registry
from mandateguard.discovery.ingest.sources import (
    DEFAULT_SOURCE_IDS,
    DatasetSource,
    get_source,
)


CATALOG_FILENAME = "discovery_catalog.jsonl.gz"
MANIFEST_FILENAME = "discovery_catalog.manifest.json"

ADAPTERS: dict[str, Callable[[Sequence[Any]], Iterable[tuple[DiscoveryProduct, dict[str, bool]]]]] = {
    flipkart.ADAPTER_ID: flipkart.normalize_rows,
    registry.ADAPTER_ID: registry.normalize_rows,
}
READERS: dict[str, Callable[[str], list[Any]]] = {
    flipkart.ADAPTER_ID: flipkart.read_rows,
    registry.ADAPTER_ID: registry.read_rows,
}


class ImportError_(RuntimeError):
    """The import could not be completed safely."""


@dataclass(frozen=True, slots=True)
class ImportReport:
    source_id: str
    raw_bytes: int
    raw_sha256: str
    member_sha256: str
    rows_read: int
    rows_normalized: int
    rows_deduplicated: int
    rows_committed: int
    catalog_bytes: int
    catalog_sha256: str
    normalization_counts: dict[str, int]

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "raw_bytes": self.raw_bytes,
            "raw_sha256": self.raw_sha256,
            "member_sha256": self.member_sha256,
            "rows_read": self.rows_read,
            "rows_normalized": self.rows_normalized,
            "rows_deduplicated": self.rows_deduplicated,
            "rows_committed": self.rows_committed,
            "catalog_bytes": self.catalog_bytes,
            "catalog_sha256": self.catalog_sha256,
            "normalization_counts": dict(self.normalization_counts),
        }


def download_raw(source: DatasetSource, destination: Path, *, timeout: float = 300.0) -> Path:
    """Fetch the registered archive into ``data/import/`` if it is not present."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    request = urllib.request.Request(
        source.download_url,
        headers={"User-Agent": "mandateguard-discovery-import/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        raise ImportError_(f"download for {source.source_id} was empty")
    destination.write_bytes(payload)
    return destination


def extract_member(source: DatasetSource, archive_path: Path) -> tuple[str, str]:
    """Return ``(text, member_sha256)`` for the registered member file."""

    raw = archive_path.read_bytes()
    if source.archive:
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            if source.member_filename not in bundle.namelist():
                raise ImportError_(
                    f"{source.member_filename} is not in the downloaded archive"
                )
            member = bundle.read(source.member_filename)
    else:
        member = raw
    return member.decode("utf-8", errors="replace"), sha256(member).hexdigest()


def _dedup_key(product: DiscoveryProduct) -> tuple[str, str, str, int | None]:
    return (
        product.title.casefold(),
        product.description.casefold(),
        (product.brand or "").casefold(),
        product.price_minor,
    )


def normalize_and_dedup(
    source: DatasetSource, text: str
) -> tuple[list[DiscoveryProduct], int, int, dict[str, int]]:
    """Adapt, validate, and drop exact duplicate listings.

    Two listings that carry the same title, description, brand, and price are
    the same listing published twice. Variants that differ in any of those
    fields are kept: a real catalogue contains sibling SKUs, and collapsing them
    would understate the retrieval problem rather than solve it.
    """

    reader = READERS.get(source.adapter)
    adapter = ADAPTERS.get(source.adapter)
    if reader is None or adapter is None:
        raise ImportError_(f"no adapter registered for {source.adapter!r}")
    rows = reader(text)
    counts: dict[str, int] = {}
    seen: set[tuple[str, str, str, int | None]] = set()
    products: list[DiscoveryProduct] = []
    normalized = 0
    for product, flags in adapter(rows):
        normalized += 1
        for name, hit in flags.items():
            if hit:
                counts[name] = counts.get(name, 0) + 1
        key = _dedup_key(product)
        if key in seen:
            continue
        seen.add(key)
        products.append(product)
    products.sort(key=lambda item: item.catalog_product_id)
    return products, len(rows), normalized, counts


def write_catalog(products: Sequence[DiscoveryProduct], path: Path) -> tuple[int, str]:
    """Write the deterministic gzip JSONL catalog; return ``(bytes, sha256)``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    # mtime=0 keeps the archive byte-identical across re-imports of the same rows.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, compresslevel=9) as handle:
        for product in products:
            line = json.dumps(
                product.to_mapping(),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            handle.write(line.encode("utf-8"))
            handle.write(b"\n")
    payload = buffer.getvalue()
    path.write_bytes(payload)
    return len(payload), sha256(payload).hexdigest()


def _source_path(
    source: DatasetSource, *, import_dir: Path, repository_root: Path
) -> Path:
    """Where this source lives: a downloaded archive, or a repository fixture."""

    if not source.download_url:
        return repository_root / "fixtures" / "agentic_commerce" / source.member_filename
    suffix = "zip" if source.archive else "csv"
    return import_dir / f"{source.source_id}-raw.{suffix}"


def import_catalog(
    source_ids: Sequence[str] = DEFAULT_SOURCE_IDS,
    *,
    import_dir: Path,
    processed_dir: Path,
    repository_root: Path,
    download: bool = True,
) -> list[ImportReport]:
    """Import every named source into one merged, deduplicated catalog.

    Order is the caller's and it matters: a listing's position in the committed
    file is its document id in every frozen index, so re-running with the same
    sources in the same order reproduces byte-identical indexes.
    """

    reports: list[ImportReport] = []
    merged: list[DiscoveryProduct] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    manifest_sources: list[dict[str, object]] = []
    for source_id in source_ids:
        source = get_source(source_id)
        path = _source_path(
            source, import_dir=import_dir, repository_root=repository_root
        )
        if source.download_url and download:
            download_raw(source, path)
        if not path.exists():
            raise ImportError_(
                f"{path} is absent. Run the importer with downloads enabled, or "
                "place the registered file there manually."
            )
        raw = path.read_bytes()
        text, member_sha = extract_member(source, path)
        products, rows_read, normalized, counts = normalize_and_dedup(source, text)
        if not products:
            raise ImportError_(f"import of {source_id} produced no listings")
        kept: list[DiscoveryProduct] = []
        for product in products:
            key = _dedup_key(product)
            if key in seen:
                continue
            seen.add(key)
            kept.append(product)
        merged.extend(kept)
        reports.append(
            ImportReport(
                source_id=source.source_id,
                raw_bytes=len(raw),
                raw_sha256=sha256(raw).hexdigest(),
                member_sha256=member_sha,
                rows_read=rows_read,
                rows_normalized=normalized,
                rows_deduplicated=normalized - len(kept),
                rows_committed=len(kept),
                catalog_bytes=0,
                catalog_sha256="",
                normalization_counts=counts,
            )
        )
        manifest_sources.append({"source": source.to_mapping(), "import": {}})
    catalog_bytes, catalog_sha = write_catalog(merged, processed_dir / CATALOG_FILENAME)
    reports = [
        replace(report, catalog_bytes=catalog_bytes, catalog_sha256=catalog_sha)
        for report in reports
    ]
    for entry, report in zip(manifest_sources, reports, strict=True):
        entry["import"] = report.to_mapping()
    write_manifest(manifest_sources, merged, processed_dir / MANIFEST_FILENAME)
    return reports


def catalog_statistics(products: Sequence[DiscoveryProduct]) -> dict[str, object]:
    top_counts: dict[str, int] = {}
    leaf: set[str] = set()
    priced = 0
    brands: set[str] = set()
    for product in products:
        top_counts[product.top_category] = top_counts.get(product.top_category, 0) + 1
        leaf.add(product.category_text)
        priced += product.price_minor is not None
        if product.brand:
            brands.add(product.brand.casefold())
    prices = sorted(
        product.price_minor for product in products if product.price_minor is not None
    )
    by_source: dict[str, int] = {}
    for product in products:
        by_source[product.source] = by_source.get(product.source, 0) + 1
    return {
        "listings": len(products),
        "listings_by_source": dict(sorted(by_source.items())),
        "top_level_categories": len(top_counts),
        "distinct_category_paths": len(leaf),
        "distinct_brands": len(brands),
        "listings_with_price": priced,
        "listings_without_price": len(products) - priced,
        "price_minor_min": prices[0] if prices else None,
        "price_minor_median": prices[len(prices) // 2] if prices else None,
        "price_minor_max": prices[-1] if prices else None,
        "top_category_support": dict(
            sorted(top_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
    }


def write_manifest(
    sources: Sequence[Mapping[str, object]],
    products: Sequence[DiscoveryProduct],
    path: Path,
) -> None:
    primary: Mapping[str, object] = sources[0] if sources else {}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "normalized_fields": list(NORMALIZED_FIELDS),
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "trust_tier": "DISCOVERY_LISTING",
        "trust_note": (
            "This catalog is a discovery surface. It is not merchant "
            "authorization evidence and cannot satisfy a Tier A/B check or a "
            "semantic constraint. A registered merchant product indexed here is "
            "discoverable through this catalog; its trusted evidence still "
            "resolves only through the authorization store."
        ),
        "sources": list(sources),
        "source": primary.get("source", {}),
        "import": primary.get("import", {}),
        "statistics": catalog_statistics(products),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
