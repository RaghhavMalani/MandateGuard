"""Build every frozen discovery artifact from the imported catalog.

    python scripts/train_discovery_models.py

Produces, under `data/models/`:

* `lexical_index.mgdx`   - BM25 inverted index (no training, deterministic build)
* `embedding_index.mgdx` - TF-IDF -> truncated SVD projection and document vectors
* `category_classifier.mgdx` - linear category classifier
* `category_confusion.json`  - confusion matrix over the frozen grouped-family
                               test partition
* `training_report.json`     - every number this run measured

Needs the training dependencies (`requirements-train.txt`). The runtime never
imports them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.discovery.catalog import indexed_streams, load_catalog  # noqa: E402
from mandateguard.discovery.index.lexical import (  # noqa: E402
    build_lexical_index,
    write_lexical_index,
)
from mandateguard.ml.classifier_train import train_classifier  # noqa: E402
from mandateguard.ml.embedding_train import train_embedding_index  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed-dir", type=Path, default=REPOSITORY_ROOT / "data" / "processed"
    )
    parser.add_argument("--models-dir", type=Path, default=REPOSITORY_ROOT / "data" / "models")
    parser.add_argument("--eval-dir", type=Path, default=REPOSITORY_ROOT / "data" / "eval")
    parser.add_argument("--dimensions", type=int, default=192)
    parser.add_argument("--max-features", type=int, default=30_000)
    parser.add_argument("--classifier-max-features", type=int, default=20_000)
    parser.add_argument("--skip-classifier", action="store_true")
    args = parser.parse_args(argv)

    catalog = load_catalog(args.processed_dir)
    print(f"catalog: {len(catalog)} listings, sha256 {catalog.catalog_sha256[:16]}...")
    args.models_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "catalog": {
            "listings": len(catalog),
            "catalog_sha256": catalog.catalog_sha256,
            "catalog_bytes": catalog.source_bytes,
        }
    }

    started = perf_counter()
    built = build_lexical_index(indexed_streams(catalog.products))
    lexical_bytes, lexical_sha = write_lexical_index(
        built, args.models_dir / "lexical_index.mgdx", catalog_sha256=catalog.catalog_sha256
    )
    report["lexical_index"] = {
        "terms": len(built["postings"]),
        "documents": built["document_count"],
        "build_seconds": round(perf_counter() - started, 3),
        "artifact_bytes": lexical_bytes,
        "artifact_sha256": lexical_sha,
    }
    print(f"lexical index: {report['lexical_index']}")

    embedding = train_embedding_index(
        catalog,
        args.models_dir / "embedding_index.mgdx",
        dimensions=args.dimensions,
        max_features=args.max_features,
    )
    report["embedding_index"] = embedding.to_mapping()
    print(f"embedding index: {embedding.to_mapping()}")

    if not args.skip_classifier:
        classifier = train_classifier(
            catalog,
            model_path=args.models_dir / "category_classifier.mgdx",
            split_manifest_path=args.eval_dir / "category_split.frozen.json",
            grouped_split_manifest_path=(
                args.eval_dir / "category_split.grouped.frozen.json"
            ),
            confusion_path=args.models_dir / "category_confusion.json",
            repository_root=REPOSITORY_ROOT,
            max_features=args.classifier_max_features,
        )
        report["category_classifier"] = classifier.to_mapping()
        print(json.dumps(classifier.to_mapping(), indent=2, sort_keys=True))

    path = args.models_dir / "training_report.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"training report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
