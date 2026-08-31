"""Freeze INT-3 model features and exact-hash live execution plan offline."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INT2_FIXTURES = REPOSITORY_ROOT / "fixtures" / "engineering" / "int2"
COMMERCE_FIXTURES = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"
STAGE_B_RUN = (
    REPOSITORY_ROOT
    / "artifacts"
    / "engineering"
    / "int2"
    / "stage-b-live-20260830T123856Z-0e4213c"
)
INT3_ARTIFACTS = REPOSITORY_ROOT / "artifacts" / "engineering" / "int3"


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("created-at must be ISO-8601") from error
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
        "--catalog", type=Path, default=COMMERCE_FIXTURES / "merchant_catalog.json"
    )
    parser.add_argument(
        "--terms", type=Path, default=COMMERCE_FIXTURES / "merchant_terms.json"
    )
    parser.add_argument(
        "--stage-b-observations",
        type=Path,
        default=STAGE_B_RUN / "stage_b_observations.jsonl",
    )
    parser.add_argument(
        "--prior-observations",
        type=Path,
        default=STAGE_B_RUN / "stage_b_observations.jsonl",
    )
    parser.add_argument("--created-at", type=_aware_datetime, default=None)
    parser.add_argument(
        "--feature-manifest-output",
        type=Path,
        default=INT3_ARTIFACTS / "model_feature_manifest.json",
    )
    parser.add_argument(
        "--execution-plan-output",
        type=Path,
        default=INT3_ARTIFACTS / "subset_live_execution_plan.json",
    )
    args = parser.parse_args(arguments)

    from mandateguard.engineering.int2.stage_b_cases import load_stage_b_case_manifest
    from mandateguard.engineering.int3.artifacts import write_model_feature_manifest
    from mandateguard.engineering.int3.live_plan import (
        build_live_execution_plan,
        live_execution_plan_record,
        load_prior_exact_results,
        write_live_execution_plan,
    )
    from mandateguard.engineering.int3.model_manifest import (
        MODEL_FEATURE_MANIFEST_SHA256,
        MODEL_FEATURE_NAMES,
    )
    from mandateguard.engineering.int3.subsets import (
        build_subset_plan,
        load_full_evidence_references,
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
    references = load_full_evidence_references(
        args.stage_b_observations,
        cases=cases,
    )
    created_at = args.created_at or datetime.now(timezone.utc)
    subset_plan = build_subset_plan(
        cases=cases,
        references=references,
        created_at=created_at,
    )
    prior = load_prior_exact_results(args.prior_observations)
    live_plan = build_live_execution_plan(
        subset_plan=subset_plan,
        prior_results=prior,
        created_at=created_at,
    )
    write_model_feature_manifest(args.feature_manifest_output)
    write_live_execution_plan(live_plan, args.execution_plan_output)
    record = live_execution_plan_record(live_plan)
    print("INT-3 METHODOLOGY FREEZE")
    print(
        f"model_features={len(MODEL_FEATURE_NAMES)} "
        f"model_feature_manifest_sha256={MODEL_FEATURE_MANIFEST_SHA256}"
    )
    print(
        f"observations={live_plan.nominal_observation_count} "
        f"unique_hashes={live_plan.unique_semantic_input_count} "
        f"prior_exact={live_plan.prior_exact_result_unique_input_count} "
        f"new_unique={live_plan.new_unique_input_count} "
        f"predicted_api_calls={live_plan.predicted_new_semantic_api_calls}"
    )
    print(
        f"execution_plan={args.execution_plan_output} "
        f"canonical_sha256={record['canonical_sha256']}"
    )
    print("semantic_provider_calls=0 razorpay_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
