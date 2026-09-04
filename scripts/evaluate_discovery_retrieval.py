"""Measure retrieval quality against the frozen, human-authored query set.

    python scripts/evaluate_discovery_retrieval.py

Writes `artifacts/engineering/discovery/retrieval_evaluation.json`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.discovery.catalog import load_catalog  # noqa: E402
from mandateguard.discovery.index.embedding import load_embedding_index  # noqa: E402
from mandateguard.discovery.index.hybrid import HybridDiscoveryRetriever  # noqa: E402
from mandateguard.discovery.index.lexical import load_lexical_index  # noqa: E402
from mandateguard.ml.retrieval_eval import evaluate_retrieval, load_query_set  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=REPOSITORY_ROOT / "data" / "processed")
    parser.add_argument("--models-dir", type=Path, default=REPOSITORY_ROOT / "data" / "models")
    parser.add_argument(
        "--queries", type=Path, default=REPOSITORY_ROOT / "data" / "eval" / "retrieval_queries.json"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "engineering" / "discovery" / "retrieval_evaluation.json",
    )
    parser.add_argument("--candidate-depth", type=int, default=300)
    args = parser.parse_args(argv)

    catalog = load_catalog(args.processed_dir)
    retriever = HybridDiscoveryRetriever(
        lexical=load_lexical_index(args.models_dir / "lexical_index.mgdx"),
        embedding=load_embedding_index(args.models_dir / "embedding_index.mgdx"),
        product_at=lambda document_id: catalog[document_id],
    )
    report = evaluate_retrieval(
        catalog,
        retriever,
        load_query_set(args.queries),
        candidate_depth=args.candidate_depth,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    header = (
        f"{'configuration':34} {'R@5':>7} {'R@10':>7} {'P@5':>7} {'MRR':>7} "
        f"{'lit@10':>7} {'par@10':>7} {'distinct':>8} {'p50 ms':>7}"
    )
    print(header)
    print("-" * len(header))
    for name, block in report["configurations"].items():
        print(
            f"{name:34} {block['recall_at_5']:7.4f} {block['recall_at_10']:7.4f} "
            f"{block['precision_at_5']:7.4f} {block['mrr']:7.4f} "
            f"{block['by_family']['literal']['recall_at_10']:7.4f} "
            f"{block['by_family']['paraphrase']['recall_at_10']:7.4f} "
            f"{block['distinct_title_fraction']:8.4f} "
            f"{block['latency_ms']['median']:7.2f}"
        )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
