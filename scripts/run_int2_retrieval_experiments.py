"""Run the deterministic, retrieval-only INT-2 Stage-A sweep.

Embeddings are generated once per run, before the configuration matrix is
evaluated. The default provider is offline and makes no network call; live
OpenAI embeddings are opt-in via --live-embeddings.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_ROOT = REPOSITORY_ROOT / "fixtures" / "engineering" / "int2"


class ConfigurationError(RuntimeError):
    """A live-mode prerequisite is missing; no experiment work has started."""


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
    parser.add_argument(
        "--live-embeddings",
        action="store_true",
        help=(
            "Opt in to live OpenAI embeddings. Without this flag the run is "
            "offline and deterministic and makes zero network calls."
        ),
    )
    return parser


def _load_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)


def _live_embedding_provider():
    """Build the live provider, or fail before any experiment work begins."""

    from mandateguard.intelligence.retrieval.embeddings import (
        DEFAULT_EMBEDDING_MODEL,
        OpenAIEmbeddingProvider,
    )

    _load_environment()
    if not os.environ.get("OPENAI_API_KEY"):
        raise ConfigurationError(
            "--live-embeddings requires OPENAI_API_KEY; set it in the "
            "environment or in a local .env file"
        )
    model_id = (
        os.environ.get("MANDATEGUARD_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
    )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ConfigurationError(
            "--live-embeddings requires the OpenAI Python package"
        ) from exc
    return OpenAIEmbeddingProvider(client=OpenAI(), model_id=model_id)


def _offline_embedding_provider():
    from mandateguard.intelligence.retrieval.embeddings import (
        HashingEmbeddingProvider,
    )

    return HashingEmbeddingProvider()


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
    from mandateguard.engineering.int2.sweep import run_stage_a_sweep
    from mandateguard.intelligence.store import TrustedCommerceStore

    # Resolve the provider first so a live run without credentials fails
    # before any fixture is loaded or any observation is produced.
    provider = (
        _live_embedding_provider()
        if args.live_embeddings
        else _offline_embedding_provider()
    )
    print("INT-2 RETRIEVAL-ONLY ENGINEERING EXPERIMENT")
    print(
        f"embedding_provider={type(provider).__name__} "
        f"embedding_model={provider.model_id} "
        f"live_embeddings={str(bool(args.live_embeddings)).lower()}"
    )

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
    result = run_stage_a_sweep(queries, relevance, provider)
    snapshot = result.embedding_snapshot
    paths = write_retrieval_artifacts(
        result.observations,
        args.output,
        repository_root=REPOSITORY_ROOT,
        embedding_snapshot=snapshot,
    )
    print(f"queries={len(queries)} observations={len(result.observations)}")
    print(
        f"unique_query_texts={snapshot.unique_query_texts} "
        f"unique_document_texts={snapshot.unique_document_texts} "
        f"unique_texts_total={snapshot.unique_text_count}"
    )
    print(
        f"embedding_api_calls={snapshot.provider_call_count} "
        f"embedding_input_tokens={snapshot.input_token_count} "
        f"embedding_precompute_latency_ms={snapshot.precompute_latency_ms:.3f}"
    )
    live_calls = snapshot.provider_call_count if args.live_embeddings else 0
    print(f"semantic_calls=0 live_calls={live_calls} benchmark_output=false")
    print("artifacts=" + ",".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as error:
        raise SystemExit(f"configuration error: {error}") from error
