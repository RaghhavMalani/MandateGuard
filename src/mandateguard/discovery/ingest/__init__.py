"""Dataset ingestion for the discovery catalog."""

from mandateguard.discovery.ingest.pipeline import (
    CATALOG_FILENAME,
    ImportReport,
    MANIFEST_FILENAME,
    catalog_statistics,
    import_catalog,
    normalize_and_dedup,
)
from mandateguard.discovery.ingest.sources import (
    DEFAULT_SOURCE_IDS,
    REGISTERED_SOURCES,
    DatasetSource,
    get_source,
)

__all__ = [
    "CATALOG_FILENAME",
    "DEFAULT_SOURCE_IDS",
    "MANIFEST_FILENAME",
    "DatasetSource",
    "ImportReport",
    "REGISTERED_SOURCES",
    "catalog_statistics",
    "get_source",
    "import_catalog",
    "normalize_and_dedup",
]
