"""Import the registered public product datasets into the discovery catalog.

    python scripts/import_discovery_catalog.py

The raw archive lands in `data/import/` (git-ignored). The normalized,
deduplicated, merged catalog and its provenance manifest land in
`data/processed/`.

Sources are merged in the order given. That order fixes each listing's document
id in every frozen index, so re-running with the same sources reproduces the
same catalog bytes and the same indexes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.discovery.ingest import (  # noqa: E402
    MANIFEST_FILENAME,
    REGISTERED_SOURCES,
    import_catalog,
)
from mandateguard.discovery.ingest.sources import DEFAULT_SOURCE_IDS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources",
        nargs="+",
        default=list(DEFAULT_SOURCE_IDS),
        choices=sorted(REGISTERED_SOURCES),
        help="Registered source ids to merge, in index order.",
    )
    parser.add_argument("--import-dir", type=Path, default=REPOSITORY_ROOT / "data" / "import")
    parser.add_argument(
        "--processed-dir", type=Path, default=REPOSITORY_ROOT / "data" / "processed"
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Fail instead of fetching; use a pre-placed archive in --import-dir.",
    )
    args = parser.parse_args(argv)

    reports = import_catalog(
        args.sources,
        import_dir=args.import_dir,
        processed_dir=args.processed_dir,
        repository_root=REPOSITORY_ROOT,
        download=not args.no_download,
    )
    print(json.dumps([report.to_mapping() for report in reports], indent=2, sort_keys=True))
    print(f"manifest: {args.processed_dir / MANIFEST_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
