"""Preview frozen INT-2 Stage-B cases without model or payment calls."""

from __future__ import annotations

import argparse
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INT2_FIXTURES = REPOSITORY_ROOT / "fixtures" / "engineering" / "int2"
DEFAULT_COMMERCE_FIXTURES = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_INT2_FIXTURES / "stage_b_cases.json",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_INT2_FIXTURES / "retrieval_queries.json",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_COMMERCE_FIXTURES / "merchant_catalog.json",
    )
    parser.add_argument(
        "--terms",
        type=Path,
        default=DEFAULT_COMMERCE_FIXTURES / "merchant_terms.json",
    )
    args = parser.parse_args(arguments)

    from mandateguard.engineering.int2.stage_b_cases import (
        load_stage_b_case_manifest,
        manifest_preview_record,
    )
    from mandateguard.intelligence.store import TrustedCommerceStore

    store = TrustedCommerceStore.from_files(
        catalog_path=args.catalog,
        merchant_terms_path=args.terms,
    )
    manifest = load_stage_b_case_manifest(
        args.cases,
        query_corpus_path=args.queries,
        store=store,
    )
    print("INT-2 STAGE-B FROZEN CASE PREVIEW")
    print(
        f"cases={len(manifest.cases)} manifest_sha256={manifest.manifest_sha256} "
        "semantic_calls=0 buyer_calls=0 razorpay_calls=0"
    )
    for item in manifest_preview_record(manifest):
        print(
            f"{item['query_id']} expectation={item['engineering_expectation']} "
            f"deterministic_action={item['deterministic_action']} "
            f"eligible_evidence_count={item['eligible_evidence_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
