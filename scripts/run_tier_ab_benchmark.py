"""Execute the registered deterministic Tier A/B benchmark corpus.

Usage::

    python scripts/run_tier_ab_benchmark.py

Every registered case passes through the frozen authorization path exactly
once. The runner scores against the registered labels and never edits them: a
mismatch is a result, not permission to repair the benchmark.

The command refuses to run unless the working tree is clean, because the
recorded ``execution_code_git_sha`` must describe the code that actually ran.
It also refuses to overwrite an existing run journal: an interrupted first
execution is preserved and reported, never deleted and re-run.

``--record-first-run`` writes the observed ``first_run_at`` into the two
registered mirrors (the JSONL corpus and ``benchmark/MANIFEST.yaml``) after a
complete run, verifying afterwards that all 1,008 ``case_content_sha256``
values are unchanged. ``first_run_at`` is audit-only metadata that the manifest
hash policy, PROTOCOL section 6, and the codec content projection all exclude
from the digest.

No semantic model is contacted, no Razorpay call is made, no execution
capability is issued, and the whole run executes inside a socket block.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.benchmark.execution import (  # noqa: E402
    EXPECTED_TOTAL,
    JOURNAL_NAME,
    RESULTS_SUBDIRECTORY,
    SUMMARY_NAME,
    ExecutionPreconditionError,
    apply_first_run_lifecycle,
    build_pre_execution_seal,
    frozen_content_map,
    git_head_sha,
    load_registered_corpus,
    network_blocked,
    read_corpus_records,
    require_clean_worktree,
    run_cases,
    utc_now,
    validate_pre_execution_seal,
    verify_content_hashes_unchanged,
    write_run_summary,
)

# ``mandateguard.semantic.openai_adapter`` is imported by the semantic package
# initializer and is a pure schema/serialization module: importing it contacts
# nothing. What must never load is the Razorpay execution path or any HTTP
# client, because either would mean this run left deterministic policy.
FORBIDDEN_MODULE_PREFIXES = (
    "mandateguard.execution.razorpay",
    "mandateguard.execution.executor",
    "httpx",
    "requests",
    "urllib3",
    "openai",
)


def loaded_forbidden_modules() -> list[str]:
    return sorted(
        name
        for name in sys.modules
        if name == "urllib.request" or name.startswith(FORBIDDEN_MODULE_PREFIXES)
    )


def assert_no_provider_module_loaded() -> None:
    loaded = loaded_forbidden_modules()
    if loaded:
        raise SystemExit(
            "refusing to continue: the deterministic run loaded provider or "
            "network modules " + ", ".join(loaded)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=REPOSITORY_ROOT,
        type=Path,
        help="repository root that contains benchmark/MANIFEST.yaml",
    )
    parser.add_argument(
        "--protocol-git-sha",
        required=True,
        help="git SHA of the frozen benchmark evaluation protocol",
    )
    parser.add_argument(
        "--corpus-git-sha",
        required=True,
        help="git SHA of the frozen corpus generation/repair commit",
    )
    parser.add_argument(
        "--record-first-run",
        action="store_true",
        help="write first_run_at into the registered mirrors after a complete run",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="development only; never valid for a registered execution",
    )
    arguments = parser.parse_args(argv)
    root: Path = arguments.root

    if not arguments.allow_dirty:
        require_clean_worktree(root)
    execution_code_git_sha = git_head_sha(root)

    results_directory = root / RESULTS_SUBDIRECTORY
    journal_path = results_directory / JOURNAL_NAME
    if journal_path.exists():
        raise SystemExit(
            f"refusing to run: {journal_path} already exists. A prior first "
            "execution, complete or interrupted, must be preserved and reported "
            "rather than deleted and re-run."
        )

    records = read_corpus_records(root)
    cases = load_registered_corpus(root)
    content_map = frozen_content_map(root)

    run_started_at = utc_now()
    seal = build_pre_execution_seal(
        root=root,
        cases=cases,
        records=records,
        execution_code_git_sha=execution_code_git_sha,
        corpus_generation_git_sha=arguments.corpus_git_sha,
        benchmark_protocol_git_sha=arguments.protocol_git_sha,
        run_started_at=run_started_at,
    )
    validate_pre_execution_seal(seal)

    results_directory.mkdir(parents=True, exist_ok=True)
    seal_path = results_directory / "PRE_EXECUTION_SEAL.json"
    seal_path.write_text(
        json.dumps(seal, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"execution_run_id          {seal['execution_run_id']}")
    print(f"execution_code_git_sha    {execution_code_git_sha}")
    print(f"corpus_generation_git_sha {arguments.corpus_git_sha}")
    print(f"benchmark_protocol_git_sha {arguments.protocol_git_sha}")
    print(f"run_started_at            {seal['run_started_at']}")
    print(f"registered cases          {seal['total_registered_cases']}")
    print(f"first_run_at null         {seal['first_run_at_null_count']}")
    print(f"semantic constraint cases {seal['semantic_constraint_case_count']}")
    print("--- executing registered corpus ---", flush=True)

    def progress(index: int, _result: object) -> None:
        if index % 100 == 0 or index == len(cases):
            print(f"  {index}/{len(cases)} attempted", flush=True)

    assert_no_provider_module_loaded()
    with network_blocked():
        results = run_cases(
            cases,
            content_map=content_map,
            execution_run_id=seal["execution_run_id"],
            execution_code_git_sha=execution_code_git_sha,
            journal_path=journal_path,
            progress=progress,
        )
    run_completed_at = utc_now()
    assert_no_provider_module_loaded()

    lifecycle = None
    if arguments.record_first_run:
        if len(results) != EXPECTED_TOTAL:
            raise SystemExit(
                f"refusing the lifecycle update: {len(results)} attempted records, "
                f"expected {EXPECTED_TOTAL}"
            )
        lifecycle = apply_first_run_lifecycle(
            root,
            {result.case_id: result.first_run_at for result in results},
            frozen_content=content_map,
        )

    preserved, changed = verify_content_hashes_unchanged(root, content_map)
    if changed:
        raise SystemExit(
            "STOP: case_content_sha256 changed for "
            + ", ".join(changed[:10])
            + f" ({len(changed)} total). Results must not be committed."
        )

    summary = write_run_summary(
        results_directory / SUMMARY_NAME,
        seal=seal,
        results=results,
        run_completed_at=run_completed_at,
        lifecycle=lifecycle,
        content_hashes_preserved=preserved,
        first_run_at_populated=sum(
            1 for record in read_corpus_records(root) if record["first_run_at"]
        ),
    )

    print("--- first registered execution complete ---")
    print(f"run_completed_at          {summary['run_completed_at']}")
    print(f"total / completed / errors  {summary['total_cases']} / "
          f"{summary['completed_cases']} / {summary['execution_error_count']}")
    target = summary["target_state_correctness"]
    action = summary["expected_action_correctness"]
    print(f"target-state correctness    {target['matches']} matched / "
          f"{target['mismatches']} mismatched")
    print(f"expected-action correctness {action['matches']} matched / "
          f"{action['mismatches']} mismatched")
    print(f"actual actions              {summary['actual_action_counts']}")
    print(f"content hashes preserved    {preserved}/{len(content_map)}")
    print(f"first_run_at populated      {summary['first_run_at_populated_count']}")
    print(f"deterministic p50 / p95 ns  {summary['latency']['deterministic_p50_ns']} / "
          f"{summary['latency']['deterministic_p95_ns']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExecutionPreconditionError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        raise SystemExit(2) from error
