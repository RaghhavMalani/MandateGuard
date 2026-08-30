"""Run the deterministic, retrieval-only INT-2 Stage-A sweep."""

from __future__ import annotations

import argparse
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_ROOT = REPOSITORY_ROOT / "fixtures" / "engineering" / "int2"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run non-benchmark INT-2 retrieval-only experiments."
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_FIXTURE_ROOT / "retrieval_queries.json",
    )
    parser.add_argument(
        "--relevance",
        type=Path,
        default=DEFAULT_FIXTURE_ROOT / "relevance_manifest.json",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=REPOSITORY_ROOT
        / "fixtures"
        / "agentic_commerce"
        / "merchant_catalog.json",
    )
    parser.add_argument(
        "--terms",
        type=Path,
        default=REPOSITORY_ROOT
        / "fixtures"
        / "agentic_commerce"
        / "merchant_terms.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "engineering" / "int2",
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    from mandateguard.engineering.int2.artifacts import (
        write_retrieval_artifacts,
    )
    from mandateguard.engineering.int2.fixtures import (
        build_experiment_queries,
        load_query_corpus,
        load_relevance_manifest,
    )
    from mandateguard.engineering.int2.retrieval import ExperimentRetriever
    from mandateguard.engineering.int2.sweep import RetrievalSweepHarness
    from mandateguard.intelligence.retrieval.embeddings import (
        HashingEmbeddingProvider,
    )
    from mandateguard.intelligence.store import TrustedCommerceStore

    corpus = load_query_corpus(args.queries)
    relevance = load_relevance_manifest(args.relevance)
    if corpus.catalog_id != relevance.catalog_id:
        raise ValueError("query and relevance catalog IDs do not match")
    store = TrustedCommerceStore.from_files(
        catalog_path=args.catalog,
        merchant_terms_path=args.terms,
    )
    if store.snapshot_id != corpus.catalog_id:
        raise ValueError("query corpus does not identify the configured catalog")
    queries = build_experiment_queries(corpus, store)
    observations = RetrievalSweepHarness(
        ExperimentRetriever(HashingEmbeddingProvider())
    ).run(queries, relevance)
    paths = write_retrieval_artifacts(
        observations,
        args.output,
        repository_root=REPOSITORY_ROOT,
    )
    print("INT-2 RETRIEVAL-ONLY ENGINEERING EXPERIMENT")
    print(f"queries={len(queries)} observations={len(observations)}")
    print("semantic_calls=0 live_calls=0 benchmark_output=false")
    print("artifacts=" + ",".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
