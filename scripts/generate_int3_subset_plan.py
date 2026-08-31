"""Generate the offline INT-3A subset plan from frozen INT-2 evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INT2_FIXTURES = REPOSITORY_ROOT / "fixtures" / "engineering" / "int2"
COMMERCE_FIXTURES = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"
INT2_ARTIFACTS = REPOSITORY_ROOT / "artifacts" / "engineering" / "int2"
DEFAULT_STAGE_A = INT2_ARTIFACTS / "stage-a-live-20260830T113054Z-1a94a4a"
DEFAULT_STAGE_B = INT2_ARTIFACTS / "stage-b-live-20260830T123856Z-0e4213c"
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "artifacts" / "engineering" / "int3" / "subset_plan.jsonl"
)


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("created-at must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("created-at must include a timezone")
    return parsed


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=INT2_FIXTURES / "stage_b_cases.json")
    parser.add_argument(
        "--queries", type=Path, default=INT2_FIXTURES / "retrieval_queries.json"
    )
    parser.add_argument(
        "--relevance", type=Path, default=INT2_FIXTURES / "relevance_manifest.json"
    )
    parser.add_argument(
        "--catalog", type=Path, default=COMMERCE_FIXTURES / "merchant_catalog.json"
    )
    parser.add_argument(
        "--terms", type=Path, default=COMMERCE_FIXTURES / "merchant_terms.json"
    )
    parser.add_argument(
        "--stage-a-observations",
        type=Path,
        default=DEFAULT_STAGE_A / "retrieval_observations.jsonl",
    )
    parser.add_argument(
        "--stage-b-observations",
        type=Path,
        default=DEFAULT_STAGE_B / "stage_b_observations.jsonl",
    )
    parser.add_argument(
        "--created-at",
        type=_aware_datetime,
        default=None,
        help="Optional timezone-aware timestamp for reproducible regeneration.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(arguments)

    from mandateguard.engineering.int2.fixtures import load_relevance_manifest
    from mandateguard.engineering.int2.stage_b_cases import load_stage_b_case_manifest
    from mandateguard.engineering.int3.artifacts import write_subset_plan_jsonl
    from mandateguard.engineering.int3.models import subset_counts_by_query
    from mandateguard.engineering.int3.subsets import (
        build_subset_plan,
        load_full_evidence_references,
        load_reference_score_surface,
    )
    from mandateguard.intelligence.store import TrustedCommerceStore

    store = TrustedCommerceStore.from_files(
        catalog_path=args.catalog,
        merchant_terms_path=args.terms,
    )
    cases = load_stage_b_case_manifest(
        args.cases,
        query_corpus_path=args.queries,
        store=store,
    )
    relevance = load_relevance_manifest(args.relevance)
    references = load_full_evidence_references(
        args.stage_b_observations,
        cases=cases,
    )
    score_surface = load_reference_score_surface(args.stage_a_observations)
    created_at = args.created_at or datetime.now(timezone.utc)
    plan = build_subset_plan(
        cases=cases,
        references=references,
        created_at=created_at,
    )
    output = write_subset_plan_jsonl(
        plan=plan,
        cases=cases,
        relevance=relevance,
        score_surface=score_surface,
        output_path=args.output,
    )
    counts = subset_counts_by_query(plan)
    print("INT-3A EVIDENCE-SUFFICIENCY PLAN")
    print(
        f"output={output} observations={plan.observation_count} "
        f"queries={len(plan.query_ids)} semantic_provider_calls=0 "
        "evidence_fetch_calls=0 razorpay_calls=0"
    )
    for query_id, count in counts.items():
        print(f"{query_id} subsets={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
