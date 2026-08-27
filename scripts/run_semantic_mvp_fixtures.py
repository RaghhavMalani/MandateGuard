"""Validate or explicitly run non-benchmark semantic MVP engineering fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = (
    REPOSITORY_ROOT / "fixtures" / "semantic_mvp" / "semantic_cases.jsonl"
)
DEFAULT_ARTIFACTS = (
    REPOSITORY_ROOT / "artifacts" / "engineering" / "semantic_mvp"
)
DEFAULT_CACHE = DEFAULT_ARTIFACTS / "cache"
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.engineering.semantic_fixtures import (  # noqa: E402
    EngineeringExpectation,
    SemanticFamily,
    SemanticMvpFixtureError,
    load_fixture_corpus,
    select_fixtures,
)


def _print_validation(fixtures, selected) -> None:
    families = Counter(item.family for item in fixtures)
    expectations = Counter(item.engineering_expectation for item in fixtures)
    demos = sum(item.demo_priority for item in fixtures)
    print("ENGINEERING FIXTURE DIAGNOSTIC")
    print(
        f"validation=PASS corpus={len(fixtures)} selected={len(selected)} "
        "live=false model_calls=0"
    )
    print(
        "families="
        + ",".join(
            f"{family.value}:{families[family]}" for family in SemanticFamily
        )
    )
    print(
        "engineering_expectations="
        + ",".join(
            f"{expectation.value}:{expectations[expectation]}"
            for expectation in EngineeringExpectation
        )
    )
    print(f"demo_priority={demos}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES,
        help="engineering semantic JSONL corpus",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="validate without importing or calling semantic execution",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="explicitly call the existing frozen semantic authorization path",
    )
    parser.add_argument("--case-id", help="select one fixture_id")
    parser.add_argument(
        "--demo-only",
        action="store_true",
        help="select the nine prioritized demo fixtures",
    )
    parser.add_argument("--limit", type=int, help="limit selected fixtures")
    parser.add_argument(
        "--model-id",
        help="live provider model ID; defaults to MANDATEGUARD_SEMANTIC_MODEL",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=DEFAULT_ARTIFACTS,
        help="non-benchmark live result directory",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="non-benchmark semantic cache directory",
    )
    args = parser.parse_args(argv)

    try:
        fixtures = load_fixture_corpus(args.fixtures)
        selected = select_fixtures(
            fixtures,
            case_id=args.case_id,
            demo_only=args.demo_only,
            limit=args.limit,
        )
    except SemanticMvpFixtureError as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1

    if not args.live:
        _print_validation(fixtures, selected)
        return 0

    model_id = args.model_id or os.environ.get("MANDATEGUARD_SEMANTIC_MODEL")
    if not model_id:
        print(
            "rejected: --live requires --model-id or "
            "MANDATEGUARD_SEMANTIC_MODEL",
            file=sys.stderr,
        )
        return 1

    from mandateguard.engineering.semantic_runner import (
        SemanticMvpLiveError,
        create_openai_semantic_verifier,
        require_engineering_artifact_path,
        run_live_fixtures,
        write_live_results,
    )

    try:
        require_engineering_artifact_path(
            args.artifacts_dir,
            repository_root=REPOSITORY_ROOT,
        )
        require_engineering_artifact_path(
            args.cache_dir,
            repository_root=REPOSITORY_ROOT,
        )
        verifier = create_openai_semantic_verifier(
            model_id=model_id,
            cache_directory=args.cache_dir,
        )
        results = run_live_fixtures(selected, semantic_verifier=verifier)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = args.artifacts_dir / f"semantic-mvp-{timestamp}.jsonl"
        write_live_results(
            results,
            output,
            repository_root=REPOSITORY_ROOT,
        )
    except SemanticMvpLiveError as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1

    print("ENGINEERING FIXTURE DIAGNOSTIC")
    for result in results:
        print(
            f"{result.fixture_id} expected={result.engineering_expectation.value} "
            f"observed={result.semantic_status} action={result.final_action} "
            f"match={str(result.engineering_expectation_match).lower()}"
        )
    matches = sum(result.engineering_expectation_match for result in results)
    print(
        f"live=true cases={len(results)} expectation_matches={matches} "
        f"artifact={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
