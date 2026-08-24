"""D8 tests for the deterministic Tier A/B execution harness.

Every case built here is synthetic and non-registered: the recipe indices sit
far outside the registered range, and one test asserts that none of their
content digests appears in the committed corpus. The committed 1,008 cases are
never executed by this file. Their first registered execution is a separate,
once-only audit event performed by ``scripts/run_tier_ab_benchmark.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mandateguard.benchmark.codec import (
    case_content_sha256,
    case_record_line,
    decode_case,
    encode_timestamp,
)
from mandateguard.benchmark.deterministic_generator import (
    CORPUS_SUBDIRECTORY,
    MANIFEST_PATH,
)
from mandateguard.benchmark.execution import (
    EXPECTED_TOTAL,
    ExecutionPreconditionError,
    apply_first_run_lifecycle,
    build_pre_execution_seal,
    corpus_file_digests,
    derive_run_id,
    evaluate_case,
    execute_case,
    frozen_content_map,
    git_status_porcelain,
    journal_first_run_map,
    manifest_cases_sha256,
    network_blocked,
    read_corpus_records,
    read_journal,
    read_manifest_records,
    require_clean_worktree,
    run_cases,
    validate_pre_execution_seal,
    verify_content_hashes_unchanged,
    write_run_summary,
)
from mandateguard.benchmark.manifest import (
    frozen_preamble,
    manifest_record,
    render_manifest,
)
from mandateguard.benchmark.models import (
    BENCHMARK_FAMILIES,
    CASE_SCHEMA_VERSION,
    GENERATOR_VERSION,
    TIER_A_FAMILIES,
    BenchmarkCase,
    GeneratorAudit,
    TargetExpectation,
)
from mandateguard.benchmark.recipes import build_inputs, default_scenario
from mandateguard.benchmark.results import (
    EXECUTION_SCHEMA_VERSION,
    CaseExecutionResult,
    ExecutionErrorRecord,
    ObservedOutcome,
    RegisteredLabel,
    ResultRecordError,
    build_run_summary,
    encode_result,
    nearest_rank_percentile,
    observed_compositions,
    result_record_line,
    sanitize_error_message,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Deliberately outside the registered index range (Tier A P cases run 0-23,
# Tier B P cases run 0-27), so nothing here can collide with a registered case.
SYNTHETIC_INDEX_BASE = 900

LABEL_RECORDED_AT = datetime(2026, 8, 23, 16, 17, 56, 493960, tzinfo=timezone.utc)
RUN_STARTED_AT = datetime(2026, 8, 24, 9, 0, 0, tzinfo=timezone.utc)

CODE_SHA = "1" * 40
CORPUS_SHA = "2" * 40
PROTOCOL_SHA = "3" * 40
RUN_ID = "9" * 64


# --- synthetic fixtures ----------------------------------------------------


def synthetic_case(family_id: str, index: int) -> BenchmarkCase:
    """A fully evidenced baseline case: every registered invariant holds."""

    scenario = default_scenario(family_id, "P", SYNTHETIC_INDEX_BASE + index)
    return BenchmarkCase(
        case_id=f"{family_id}-P-{SYNTHETIC_INDEX_BASE + index:03d}",
        case_schema_version=CASE_SCHEMA_VERSION,
        evidence_tier=family_id[0],
        family_id=family_id,
        provenance="developer_authored",
        split="dev",
        ground_truth="benign",
        label_source="deterministic_invariant",
        expected_action="ALLOW",
        target_expectation=TargetExpectation(family_id=family_id, status="PASS"),
        evaluation_inputs=build_inputs(scenario),
        label_recorded_at=LABEL_RECORDED_AT,
        generator=GeneratorAudit(
            generator_version=GENERATOR_VERSION,
            generator_seed=index,
            recipe_id=f"{family_id}.synthetic.baseline",
            recipe_parameters={"variant": "synthetic"},
        ),
    )


@pytest.fixture(scope="module")
def synthetic_cases() -> list[BenchmarkCase]:
    return [synthetic_case(family_id, 1) for family_id in BENCHMARK_FAMILIES]


def _clock(start: datetime = RUN_STARTED_AT):
    state = {"tick": 0}

    def now() -> datetime:
        state["tick"] += 1
        return start + timedelta(microseconds=state["tick"])

    return now


def _fake_root(tmp_path: Path, cases: list[BenchmarkCase]) -> Path:
    """A temporary repository shape holding a small non-registered corpus."""

    root = tmp_path / "fake"
    (root / CORPUS_SUBDIRECTORY).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPOSITORY_ROOT / MANIFEST_PATH, root / MANIFEST_PATH)
    (root / "benchmark" / "generated").mkdir(parents=True, exist_ok=True)
    (root / "benchmark" / "generated" / "TIER_AB_GENERATION_SUMMARY.json").write_text(
        json.dumps({"registered_corpus_executed": False}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    by_family: dict[str, list[BenchmarkCase]] = {
        family_id: [] for family_id in BENCHMARK_FAMILIES
    }
    for case in cases:
        by_family[case.family_id].append(case)
    for family_id, family_cases in by_family.items():
        path = root / CORPUS_SUBDIRECTORY / f"{family_id}.jsonl"
        lines = [case_record_line(case) for case in family_cases]
        path.write_text(
            ("\n".join(lines) + "\n") if lines else "", encoding="utf-8", newline="\n"
        )
    preamble = frozen_preamble(root / MANIFEST_PATH)
    records = [manifest_record(case, case_content_sha256(case)) for case in cases]
    (root / MANIFEST_PATH).write_text(
        render_manifest(preamble, records), encoding="utf-8", newline="\n"
    )
    return root


def _result(
    case_id: str,
    *,
    family_id: str = "A1",
    expected_action: str = "ALLOW",
    target_status: str = "PASS",
    final_action: str = "ALLOW",
    tier_a_overrides: dict[str, str] | None = None,
    tier_b: tuple[str, ...] = (),
    tier_a_findings: tuple[str, ...] = (),
    latency_ns: int = 1_000,
    error: ExecutionErrorRecord | None = None,
) -> CaseExecutionResult:
    overrides = tier_a_overrides or {}
    observed = (
        None
        if error is not None
        else ObservedOutcome(
            final_action=final_action,
            tier_a_results=tuple(
                (family, overrides.get(family, "PASS"), None)
                for family in TIER_A_FAMILIES
            ),
            tier_b_finding_families=tier_b,
            tier_a_finding_families=tier_a_findings,
            semantic_decision_present=False,
            semantic_constraints_present=False,
        )
    )
    return CaseExecutionResult(
        case_id=case_id,
        case_content_sha256="a" * 64,
        first_run_at=RUN_STARTED_AT,
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        registered=RegisteredLabel(
            evidence_tier=family_id[0],
            family_id=family_id,
            ground_truth="benign" if expected_action == "ALLOW" else "violation",
            expected_action=expected_action,
            target_family_id=family_id,
            target_status=target_status,
        ),
        observed=observed,
        authorization_latency_ns=0 if error is not None else latency_ns,
        error=error,
    )


# --- the synthetic cases are genuinely not registered ----------------------


def test_synthetic_cases_are_not_in_the_registered_corpus(synthetic_cases):
    registered = set(frozen_content_map(REPOSITORY_ROOT).values())
    registered_ids = set(frozen_content_map(REPOSITORY_ROOT))
    assert len(registered) == EXPECTED_TOTAL
    for case in synthetic_cases:
        assert case_content_sha256(case) not in registered
        assert case.case_id not in registered_ids


def test_the_registered_corpus_is_still_unexecuted_before_the_first_run():
    """Guard: these tests must never be the thing that executes the corpus."""

    records = read_corpus_records(REPOSITORY_ROOT)
    assert len(records) == EXPECTED_TOTAL
    assert len({record["case_id"] for record in records}) == EXPECTED_TOTAL


# --- result record serialization ------------------------------------------


def test_result_record_serializes_to_canonical_json():
    record = json.loads(result_record_line(_result("A1-P-001")))
    assert record["execution_schema_version"] == EXECUTION_SCHEMA_VERSION
    assert record["case_id"] == "A1-P-001"
    assert record["case_content_sha256"] == "a" * 64
    assert record["first_run_at"] == encode_timestamp(RUN_STARTED_AT)
    assert record["execution_run_id"] == RUN_ID
    assert record["execution_code_git_sha"] == CODE_SHA
    assert record["registered"] == {
        "evidence_tier": "A",
        "family_id": "A1",
        "ground_truth": "benign",
        "expected_action": "ALLOW",
        "target_expectation": {"family_id": "A1", "status": "PASS"},
    }
    assert record["actual"]["final_action"] == "ALLOW"
    assert record["actual"]["target_family_status"] == "PASS"
    assert len(record["actual"]["tier_a_results"]) == 8
    assert record["comparison"] == {"target_status_match": True, "action_match": True}
    assert record["error"] is None


def test_result_record_line_is_one_line_and_sorted():
    line = result_record_line(_result("A1-P-001"))
    assert "\n" not in line
    assert line == json.dumps(
        json.loads(line), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def test_result_record_carries_the_full_tier_a_vector_and_all_tier_b_findings():
    result = _result(
        "A7-V-001",
        family_id="A7",
        expected_action="BLOCK",
        target_status="FAIL",
        final_action="BLOCK",
        tier_a_overrides={"A7": "FAIL", "A2": "NOT_EVALUABLE"},
        tier_a_findings=("A7",),
        tier_b=("B6", "B1"),
    )
    record = json.loads(result_record_line(result))
    statuses = {
        item["family_id"]: item["status"] for item in record["actual"]["tier_a_results"]
    }
    assert statuses["A7"] == "FAIL"
    assert statuses["A2"] == "NOT_EVALUABLE"
    assert statuses["A1"] == "PASS"
    assert record["actual"]["tier_b_finding_families"] == ["B6", "B1"]
    assert record["actual"]["tier_a_finding_families"] == ["A7"]


# --- target-family extraction ---------------------------------------------


def test_tier_a_target_status_is_the_emitted_family_status():
    observed = _result(
        "A3-V-001",
        family_id="A3",
        tier_a_overrides={"A3": "FAIL", "A2": "FAIL"},
    ).observed
    assert observed.target_family_status("A3") == "FAIL"
    assert observed.target_family_status("A2") == "FAIL"
    assert observed.target_family_status("A5") == "PASS"


def test_tier_b_target_status_is_fail_only_when_the_family_fired():
    observed = _result("B6-V-001", family_id="B6", tier_b=("B6",)).observed
    assert observed.target_family_status("B6") == "FAIL"
    assert observed.target_family_status("B1") == "PASS"


def test_a_not_evaluable_tier_a_target_is_never_flattened_to_pass():
    result = _result(
        "A1-NE-001",
        family_id="A1",
        expected_action="REVIEW",
        target_status="NOT_EVALUABLE",
        final_action="REVIEW",
        tier_a_overrides={"A1": "NOT_EVALUABLE", "A7": "NOT_EVALUABLE"},
    )
    assert result.target_family_status == "NOT_EVALUABLE"
    assert result.target_status_match is True
    assert result.action_match is True


def test_target_status_extraction_rejects_an_unknown_family():
    with pytest.raises(ResultRecordError):
        _result("A1-P-001").observed.target_family_status("C-DEV-PURPOSE")


# --- action comparison -----------------------------------------------------


def test_action_and_target_comparisons_are_independent():
    mismatch = _result(
        "A4-V-001",
        family_id="A4",
        expected_action="BLOCK",
        target_status="FAIL",
        final_action="ALLOW",
    )
    assert mismatch.target_status_match is False
    assert mismatch.action_match is False

    action_only = _result(
        "A4-V-002",
        family_id="A4",
        expected_action="BLOCK",
        target_status="FAIL",
        final_action="BLOCK",
        tier_a_overrides={"A4": "FAIL"},
    )
    assert action_only.target_status_match is True
    assert action_only.action_match is True


def test_an_extra_finding_does_not_break_a_matching_case():
    """Composition is recorded, not punished, when target and action match."""

    result = _result(
        "A7-V-001",
        family_id="A7",
        expected_action="BLOCK",
        target_status="FAIL",
        final_action="BLOCK",
        tier_a_overrides={"A7": "FAIL"},
        tier_a_findings=("A7",),
        tier_b=("B6",),
    )
    assert result.target_status_match is True
    assert result.action_match is True
    assert result.observed.tier_b_finding_families == ("B6",)


# --- timing record ---------------------------------------------------------


def test_timing_record_shape_is_an_integer_nanosecond_count():
    record = json.loads(result_record_line(_result("A1-P-001", latency_ns=123_456)))
    assert record["timing"] == {"authorization_latency_ns": 123_456}
    assert isinstance(record["timing"]["authorization_latency_ns"], int)


def test_negative_or_float_latency_is_refused():
    with pytest.raises(ResultRecordError):
        _result("A1-P-001", latency_ns=-1)
    with pytest.raises(ResultRecordError):
        CaseExecutionResult(
            case_id="A1-P-001",
            case_content_sha256="a" * 64,
            first_run_at=RUN_STARTED_AT,
            execution_run_id=RUN_ID,
            execution_code_git_sha=CODE_SHA,
            registered=_result("A1-P-001").registered,
            observed=_result("A1-P-001").observed,
            authorization_latency_ns=1.5,  # type: ignore[arg-type]
            error=None,
        )


def test_percentiles_are_integers_with_no_interpolation():
    assert nearest_rank_percentile([10, 20, 30, 40], 50) == 20
    assert nearest_rank_percentile([10, 20, 30, 40], 95) == 40
    assert nearest_rank_percentile([7], 50) == 7
    assert nearest_rank_percentile([], 50) is None


# --- error records ---------------------------------------------------------


def test_error_message_is_flattened_and_bounded():
    message = sanitize_error_message("line one\n   line two\t" + "x" * 500)
    assert "\n" not in message
    assert len(message) <= 256


def test_an_errored_case_records_its_first_run_and_never_counts_as_correct():
    result = _result(
        "A1-V-001",
        expected_action="BLOCK",
        target_status="FAIL",
        error=ExecutionErrorRecord(error_type="ValueError", message="boom"),
    )
    assert result.observed is None
    assert result.final_action is None
    assert result.target_family_status is None
    assert result.target_status_match is False
    assert result.action_match is False
    record = json.loads(result_record_line(result))
    assert record["error"] == {"error_type": "ValueError", "message": "boom"}
    assert record["first_run_at"] == encode_timestamp(RUN_STARTED_AT)
    assert record["actual"]["final_action"] is None


def test_a_result_cannot_carry_both_an_outcome_and_an_error():
    with pytest.raises(ResultRecordError):
        CaseExecutionResult(
            case_id="A1-P-001",
            case_content_sha256="a" * 64,
            first_run_at=RUN_STARTED_AT,
            execution_run_id=RUN_ID,
            execution_code_git_sha=CODE_SHA,
            registered=_result("A1-P-001").registered,
            observed=_result("A1-P-001").observed,
            authorization_latency_ns=1,
            error=ExecutionErrorRecord(error_type="ValueError", message="boom"),
        )


def test_a_raising_case_is_recorded_and_the_run_continues(
    monkeypatch, synthetic_cases, tmp_path
):
    import mandateguard.semantic.orchestration as orchestration

    calls = {"count": 0}
    real = orchestration.authorize_transaction

    def flaky(**keywords):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("synthetic failure  with\nnewlines")
        return real(**keywords)

    monkeypatch.setattr(orchestration, "authorize_transaction", flaky)
    cases = synthetic_cases[:3]
    results = run_cases(
        cases,
        content_map={case.case_id: case_content_sha256(case) for case in cases},
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        journal_path=tmp_path / "journal.jsonl",
        clock=_clock(),
    )
    assert len(results) == 3
    assert results[0].error is not None
    assert results[0].error.error_type == "ValueError"
    assert "\n" not in results[0].error.message
    assert results[0].first_run_at is not None
    assert all(item.error is None for item in results[1:])


# --- run summary arithmetic ------------------------------------------------


@pytest.fixture()
def summary_seal() -> dict:
    return {
        "execution_run_id": RUN_ID,
        "execution_code_git_sha": CODE_SHA,
        "corpus_generation_git_sha": CORPUS_SHA,
        "benchmark_protocol_git_sha": PROTOCOL_SHA,
        "run_started_at": encode_timestamp(RUN_STARTED_AT),
        "pre_execution_corpus_sha256": {"A1.jsonl": "b" * 64},
        "pre_execution_manifest_cases_sha256": "c" * 64,
        "environment": {"python_version": "3.12.0"},
    }


def test_run_summary_arithmetic(summary_seal):
    results = [
        _result("A1-P-001", latency_ns=100),
        _result(
            "A1-V-001",
            expected_action="BLOCK",
            target_status="FAIL",
            final_action="BLOCK",
            tier_a_overrides={"A1": "FAIL"},
            tier_a_findings=("A1",),
            latency_ns=300,
        ),
        _result(
            "A1-V-002",
            expected_action="BLOCK",
            target_status="FAIL",
            final_action="ALLOW",
            latency_ns=200,
        ),
        _result(
            "B6-V-001",
            family_id="B6",
            expected_action="BLOCK",
            target_status="FAIL",
            error=ExecutionErrorRecord(error_type="RuntimeError", message="x"),
        ),
    ]
    summary = build_run_summary(
        seal=summary_seal,
        results=results,
        run_completed_at=RUN_STARTED_AT + timedelta(seconds=5),
        post_metadata_corpus_sha256=None,
        content_hashes_preserved=4,
        first_run_at_populated=4,
    )
    assert summary["total_cases"] == 4
    assert summary["completed_cases"] == 3
    assert summary["execution_error_count"] == 1
    assert summary["target_state_correctness"]["matches"] == 2
    assert summary["target_state_correctness"]["mismatches"] == 2
    assert summary["target_state_correctness"]["mismatched_case_ids"] == [
        "A1-V-002",
        "B6-V-001",
    ]
    assert summary["expected_action_correctness"]["matches"] == 2
    assert summary["expected_action_correctness"]["mismatches"] == 2
    assert summary["actual_action_counts"] == {"ALLOW": 2, "REVIEW": 0, "BLOCK": 1}
    assert summary["per_family"]["A1"]["total"] == 3
    assert summary["per_family"]["B6"]["execution_errors"] == 1
    assert summary["per_tier"]["A"]["total"] == 3
    assert summary["per_tier"]["B"]["total"] == 1
    assert summary["tier_a_by_registered_target_status"]["FAIL"]["total"] == 2
    assert summary["tier_a_by_registered_target_status"]["PASS"]["total"] == 1
    assert summary["tier_b_by_registered_target_status"]["FAIL"]["total"] == 1
    assert summary["latency"]["sample_count"] == 3
    assert summary["latency"]["deterministic_p50_ns"] == 200
    assert summary["latency"]["deterministic_p95_ns"] == 300
    assert summary["semantic_model_call_count"] == 0
    assert summary["razorpay_call_count"] == 0
    assert summary["network_call_count"] == 0
    assert summary["execution_errors"][0]["case_id"] == "B6-V-001"


def test_run_summary_reports_no_headline_accuracy(summary_seal):
    summary = build_run_summary(
        seal=summary_seal,
        results=[_result("A1-P-001")],
        run_completed_at=RUN_STARTED_AT,
        post_metadata_corpus_sha256=None,
        content_hashes_preserved=1,
        first_run_at_populated=1,
    )
    forbidden = {"accuracy", "precision", "recall", "f1", "score", "generalization"}
    assert forbidden.isdisjoint(summary)
    assert "target_state_correctness" in summary
    assert "expected_action_correctness" in summary


def test_observed_compositions_count_real_multi_check_states():
    results = [
        _result(
            "A7-V-001",
            family_id="A7",
            expected_action="BLOCK",
            target_status="FAIL",
            final_action="BLOCK",
            tier_a_overrides={"A7": "FAIL"},
            tier_b=("B6",),
        ),
        _result(
            "A3-V-001",
            family_id="A3",
            expected_action="BLOCK",
            target_status="FAIL",
            final_action="BLOCK",
            tier_a_overrides={"A3": "FAIL", "A2": "FAIL"},
        ),
        _result(
            "B3-V-001",
            family_id="B3",
            expected_action="BLOCK",
            target_status="FAIL",
            final_action="BLOCK",
            tier_a_overrides={"A1": "NOT_EVALUABLE"},
            tier_b=("B3",),
        ),
    ]
    counts = observed_compositions(results)
    assert counts["A7_FAIL_with_B6_FAIL"] == 1
    assert counts["B6_FAIL_with_A7_FAIL"] == 1
    assert counts["A3_FAIL_with_A2_FAIL"] == 1
    assert counts["B3_FAIL_with_A1_NOT_EVALUABLE"] == 1
    assert counts["A6_FAIL_with_dependent_NOT_EVALUABLE"] == 0


# --- pre-execution seal ----------------------------------------------------


def test_run_id_is_deterministic_and_free_of_ambient_randomness():
    first = derive_run_id(
        execution_code_git_sha=CODE_SHA,
        corpus_generation_git_sha=CORPUS_SHA,
        benchmark_protocol_git_sha=PROTOCOL_SHA,
        run_started_at=RUN_STARTED_AT,
    )
    second = derive_run_id(
        execution_code_git_sha=CODE_SHA,
        corpus_generation_git_sha=CORPUS_SHA,
        benchmark_protocol_git_sha=PROTOCOL_SHA,
        run_started_at=RUN_STARTED_AT,
    )
    later = derive_run_id(
        execution_code_git_sha=CODE_SHA,
        corpus_generation_git_sha=CORPUS_SHA,
        benchmark_protocol_git_sha=PROTOCOL_SHA,
        run_started_at=RUN_STARTED_AT + timedelta(seconds=1),
    )
    assert first == second
    assert first != later
    assert len(first) == 64


def test_seal_describes_the_corpus_it_is_about_to_execute(tmp_path, synthetic_cases):
    root = _fake_root(tmp_path, synthetic_cases)
    records = read_corpus_records(root)
    cases = [decode_case(record) for record in records]
    seal = build_pre_execution_seal(
        root=root,
        cases=cases,
        records=records,
        execution_code_git_sha=CODE_SHA,
        corpus_generation_git_sha=CORPUS_SHA,
        benchmark_protocol_git_sha=PROTOCOL_SHA,
        run_started_at=RUN_STARTED_AT,
    )
    assert seal["total_registered_cases"] == len(synthetic_cases)
    assert seal["unique_case_ids"] == len(synthetic_cases)
    assert seal["unique_case_content_sha256"] == len(synthetic_cases)
    assert seal["first_run_at_null_count"] == len(synthetic_cases)
    assert seal["manifest_first_run_at_null_count"] == len(synthetic_cases)
    assert seal["semantic_constraint_case_count"] == 0
    assert seal["pre_execution_corpus_sha256"] == corpus_file_digests(root)
    assert seal["pre_execution_manifest_cases_sha256"] == manifest_cases_sha256(root)
    assert seal["environment"]["python_version"]
    assert len(seal["execution_run_id"]) == 64


def test_seal_validation_refuses_a_corpus_that_is_not_the_registered_one(
    tmp_path, synthetic_cases
):
    root = _fake_root(tmp_path, synthetic_cases)
    records = read_corpus_records(root)
    seal = build_pre_execution_seal(
        root=root,
        cases=[decode_case(record) for record in records],
        records=records,
        execution_code_git_sha=CODE_SHA,
        corpus_generation_git_sha=CORPUS_SHA,
        benchmark_protocol_git_sha=PROTOCOL_SHA,
        run_started_at=RUN_STARTED_AT,
    )
    with pytest.raises(ExecutionPreconditionError):
        validate_pre_execution_seal(seal)


def test_seal_validation_refuses_an_already_executed_corpus():
    seal = {
        "total_registered_cases": EXPECTED_TOTAL,
        "unique_case_ids": EXPECTED_TOTAL,
        "unique_case_content_sha256": EXPECTED_TOTAL,
        "first_run_at_null_count": EXPECTED_TOTAL - 1,
        "manifest_first_run_at_null_count": EXPECTED_TOTAL,
        "semantic_constraint_case_count": 0,
        "pre_execution_corpus_sha256": {},
    }
    with pytest.raises(ExecutionPreconditionError):
        validate_pre_execution_seal(seal)


# --- dirty worktree refusal ------------------------------------------------


def _git_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for arguments in (
        ("init", "-q"),
        ("config", "user.email", "harness@example.invalid"),
        ("config", "user.name", "harness"),
    ):
        subprocess.run(("git", *arguments), cwd=root, check=True, capture_output=True)
    (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=root, check=True, capture_output=True)
    subprocess.run(
        ("git", "commit", "-q", "-m", "seed"), cwd=root, check=True, capture_output=True
    )
    return root


def test_a_clean_worktree_is_accepted(tmp_path):
    root = _git_repository(tmp_path)
    assert git_status_porcelain(root) == ""
    require_clean_worktree(root)


def test_a_dirty_worktree_refuses_execution(tmp_path):
    root = _git_repository(tmp_path)
    (root / "untracked.txt").write_text("scratch\n", encoding="utf-8")
    assert git_status_porcelain(root) != ""
    with pytest.raises(ExecutionPreconditionError):
        require_clean_worktree(root)


def test_a_modified_tracked_file_refuses_execution(tmp_path):
    root = _git_repository(tmp_path)
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ExecutionPreconditionError):
        require_clean_worktree(root)


# --- first_run_at lifecycle on a temporary fake corpus ---------------------


def test_first_run_lifecycle_records_both_mirrors_without_moving_a_digest(
    tmp_path, synthetic_cases
):
    root = _fake_root(tmp_path, synthetic_cases)
    before = frozen_content_map(root)
    assert all(
        record["first_run_at"] is None for record in read_corpus_records(root)
    )
    assert all(
        record["first_run_at"] is None for record in read_manifest_records(root)
    )

    stamps = {
        case.case_id: RUN_STARTED_AT + timedelta(microseconds=index)
        for index, case in enumerate(synthetic_cases)
    }
    outcome = apply_first_run_lifecycle(root, stamps, frozen_content=before)

    assert outcome.corpus_updated == len(synthetic_cases)
    assert outcome.manifest_updated == len(synthetic_cases)
    assert outcome.content_hashes_preserved == len(synthetic_cases)

    corpus = {record["case_id"]: record for record in read_corpus_records(root)}
    manifest = {record["case_id"]: record for record in read_manifest_records(root)}
    for case_id, stamp in stamps.items():
        assert corpus[case_id]["first_run_at"] == encode_timestamp(stamp)
        assert manifest[case_id]["first_run_at"] == encode_timestamp(stamp)
        assert corpus[case_id]["first_run_at"] == manifest[case_id]["first_run_at"]

    preserved, changed = verify_content_hashes_unchanged(root, before)
    assert changed == []
    assert preserved == len(synthetic_cases)


def test_lifecycle_leaves_every_hashed_field_byte_identical(tmp_path, synthetic_cases):
    root = _fake_root(tmp_path, synthetic_cases)
    before = {
        record["case_id"]: {
            key: value for key, value in record.items() if key != "first_run_at"
        }
        for record in read_corpus_records(root)
    }
    apply_first_run_lifecycle(
        root,
        {case.case_id: RUN_STARTED_AT for case in synthetic_cases},
        frozen_content=frozen_content_map(root),
    )
    after = {
        record["case_id"]: {
            key: value for key, value in record.items() if key != "first_run_at"
        }
        for record in read_corpus_records(root)
    }
    assert after == before


def test_lifecycle_refuses_to_overwrite_a_recorded_first_run(
    tmp_path, synthetic_cases
):
    root = _fake_root(tmp_path, synthetic_cases)
    stamps = {case.case_id: RUN_STARTED_AT for case in synthetic_cases}
    apply_first_run_lifecycle(root, stamps, frozen_content=frozen_content_map(root))
    with pytest.raises(ExecutionPreconditionError):
        apply_first_run_lifecycle(
            root,
            {case.case_id: RUN_STARTED_AT + timedelta(days=1) for case in synthetic_cases},
            frozen_content=frozen_content_map(root),
        )


def test_verify_content_hashes_reports_a_changed_digest(tmp_path, synthetic_cases):
    root = _fake_root(tmp_path, synthetic_cases)
    frozen = dict(frozen_content_map(root))
    victim = synthetic_cases[0].case_id
    frozen[victim] = "f" * 64
    preserved, changed = verify_content_hashes_unchanged(root, frozen)
    assert changed == [victim]
    assert preserved == len(synthetic_cases) - 1


# --- run journal -----------------------------------------------------------


def test_the_journal_is_appended_and_flushed_per_case(tmp_path, synthetic_cases):
    journal = tmp_path / "journal.jsonl"
    cases = synthetic_cases[:4]
    run_cases(
        cases,
        content_map={case.case_id: case_content_sha256(case) for case in cases},
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        journal_path=journal,
        clock=_clock(),
    )
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert [json.loads(line)["case_id"] for line in lines] == [
        case.case_id for case in cases
    ]


def test_a_partial_journal_stays_readable_and_is_never_truncated(
    tmp_path, synthetic_cases
):
    journal = tmp_path / "journal.jsonl"
    first = synthetic_cases[:2]
    run_cases(
        first,
        content_map={case.case_id: case_content_sha256(case) for case in first},
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        journal_path=journal,
        clock=_clock(),
    )
    partial = read_journal(journal)
    assert len(partial) == 2
    assert journal_first_run_map(partial).keys() == {case.case_id for case in first}

    rest = synthetic_cases[2:4]
    run_cases(
        rest,
        content_map={case.case_id: case_content_sha256(case) for case in rest},
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        journal_path=journal,
        clock=_clock(),
    )
    resumed = read_journal(journal)
    assert len(resumed) == 4
    assert [record["case_id"] for record in resumed[:2]] == [
        record["case_id"] for record in partial
    ]


def test_read_journal_tolerates_a_missing_file_and_blank_lines(tmp_path):
    assert read_journal(tmp_path / "absent.jsonl") == []
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        result_record_line(_result("A1-P-001")) + "\n\n", encoding="utf-8", newline="\n"
    )
    assert len(read_journal(journal)) == 1


# --- first_run_at is captured per case, not pre-filled --------------------


def test_first_run_at_is_captured_once_per_case_at_policy_entry(
    tmp_path, synthetic_cases
):
    cases = synthetic_cases[:5]
    results = run_cases(
        cases,
        content_map={case.case_id: case_content_sha256(case) for case in cases},
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        journal_path=tmp_path / "journal.jsonl",
        clock=_clock(),
    )
    stamps = [result.first_run_at for result in results]
    assert len(set(stamps)) == len(stamps)
    assert stamps == sorted(stamps)
    assert all(stamp.tzinfo is timezone.utc for stamp in stamps)


def test_a_real_clock_produces_distinct_ordered_timestamps(tmp_path, synthetic_cases):
    cases = synthetic_cases[:3]
    results = run_cases(
        cases,
        content_map={case.case_id: case_content_sha256(case) for case in cases},
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        journal_path=tmp_path / "journal.jsonl",
    )
    stamps = [result.first_run_at for result in results]
    assert stamps == sorted(stamps)
    assert all(stamp.utcoffset().total_seconds() == 0 for stamp in stamps)


# --- containment: no semantic model, no provider, no network --------------


def test_a_synthetic_deterministic_case_touches_no_semantic_path(monkeypatch):
    import mandateguard.semantic.verifier as verifier_module

    def refuse(*_arguments, **_keywords):
        raise AssertionError("the deterministic path must not call the verifier")

    monkeypatch.setattr(verifier_module.SemanticVerifier, "evaluate", refuse)
    monkeypatch.setattr(verifier_module.SemanticVerifier, "make_request", refuse)

    evaluated = evaluate_case(synthetic_case("A1", 2))
    assert evaluated.observed.semantic_decision_present is False
    assert evaluated.observed.semantic_constraints_present is False
    assert evaluated.latency_ns > 0


PROBE_SOURCE = """
import json
import sys

sys.path.insert(0, "__SRC__")

from mandateguard.benchmark.execution import evaluate_case
from mandateguard.benchmark.models import (
    CASE_SCHEMA_VERSION,
    GENERATOR_VERSION,
    BenchmarkCase,
    GeneratorAudit,
    TargetExpectation,
)
from mandateguard.benchmark.recipes import build_inputs, default_scenario
from datetime import datetime, timezone

case = BenchmarkCase(
    case_id="A1-P-907",
    case_schema_version=CASE_SCHEMA_VERSION,
    evidence_tier="A",
    family_id="A1",
    provenance="developer_authored",
    split="dev",
    ground_truth="benign",
    label_source="deterministic_invariant",
    expected_action="ALLOW",
    target_expectation=TargetExpectation(family_id="A1", status="PASS"),
    evaluation_inputs=build_inputs(default_scenario("A1", "P", 907)),
    label_recorded_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    generator=GeneratorAudit(
        generator_version=GENERATOR_VERSION,
        generator_seed=907,
        recipe_id="A1.synthetic.baseline",
        recipe_parameters={"variant": "synthetic"},
    ),
)
evaluated = evaluate_case(case)
watched = ("mandateguard.execution.razorpay", "httpx", "requests", "urllib3", "openai")
print(
    json.dumps(
        {
            "final_action": evaluated.observed.final_action,
            "semantic_decision_present": evaluated.observed.semantic_decision_present,
            "loaded": sorted(
                name
                for name in sys.modules
                if name == "urllib.request" or name.startswith(watched)
            ),
        }
    )
)
"""


def test_a_fresh_process_loads_no_provider_or_network_module(tmp_path):
    """The no-Razorpay/no-network claim, checked in an unpolluted interpreter."""

    source = PROBE_SOURCE.replace(
        "__SRC__", str(REPOSITORY_ROOT / "src").replace("\\", "\\\\")
    )
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        (sys.executable, str(probe)),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["final_action"] == "ALLOW"
    assert payload["semantic_decision_present"] is False
    assert payload["loaded"] == []


def test_a_synthetic_baseline_case_allows_with_a_full_pass_vector():
    evaluated = evaluate_case(synthetic_case("B1", 3))
    assert evaluated.observed.final_action == "ALLOW"
    assert [status for _family, status, _reason in evaluated.observed.tier_a_results] == [
        "PASS"
    ] * 8
    assert evaluated.observed.tier_b_finding_families == ()
    assert evaluated.observed.tier_a_finding_families == ()


def test_the_network_is_blocked_during_a_run():
    import socket

    with network_blocked():
        with pytest.raises(ExecutionPreconditionError):
            socket.create_connection(("127.0.0.1", 9))
        with pytest.raises(ExecutionPreconditionError):
            socket.socket().connect(("127.0.0.1", 9))
    assert socket.create_connection is not None


def test_execute_case_records_the_binding_content_hash(synthetic_cases):
    case = synthetic_cases[0]
    digest = case_content_sha256(case)
    result = execute_case(
        case,
        content_sha256=digest,
        execution_run_id=RUN_ID,
        execution_code_git_sha=CODE_SHA,
        clock=_clock(),
    )
    assert result.case_content_sha256 == digest
    assert json.loads(result_record_line(result))["case_content_sha256"] == digest


# --- summary writing -------------------------------------------------------


def test_write_run_summary_emits_canonical_utf8_lf_json(tmp_path, summary_seal):
    path = tmp_path / "results" / "FIRST_RUN_SUMMARY.json"
    summary = write_run_summary(
        path,
        seal=summary_seal,
        results=[_result("A1-P-001")],
        run_completed_at=RUN_STARTED_AT,
        lifecycle=None,
        content_hashes_preserved=1,
        first_run_at_populated=1,
    )
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8")) == summary
    assert summary["pre_execution_corpus_sha256"] == {"A1.jsonl": "b" * 64}
    assert summary["post_first_run_metadata_corpus_sha256"] is None
    assert summary["generation_summary_disposition"]["treatment"] == (
        "immutable pre-execution generation snapshot"
    )


def test_encode_result_contains_no_float(tmp_path):
    encoded = encode_result(_result("A1-P-001"))
    text = json.dumps(encoded)
    assert "." not in text.split('"timing"')[1].split("}")[0].replace(
        "authorization_latency_ns", ""
    )
