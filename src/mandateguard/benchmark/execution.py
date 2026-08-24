"""First-execution harness for the registered deterministic Tier A/B corpus.

The runner is an observer, not a second policy engine. Each registered case
passes through the frozen authorization path exactly once, via
``mandateguard.semantic.orchestration.authorize_transaction``, and everything
recorded afterwards - final action, the Tier A state vector, the Tier B finding
families - is read back off that single result. No Tier A/B invariant is
re-implemented here, and no registered label is ever recomputed.

Because all 1,008 registered mandates carry ``semantic = ()``, that call is a
deterministic-only path: ``finalize_authorization`` returns before Tier C is
reachable, no semantic verifier is constructed, and no provider is contacted.
The runner asserts that on every case rather than assuming it, and executes the
whole corpus inside a socket block so the "no network" claim is enforced rather
than promised.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Iterator, Mapping, Sequence

from mandateguard.benchmark.codec import (
    case_content_sha256,
    decode_case,
    decode_timestamp,
    encode_timestamp,
)
from mandateguard.benchmark.manifest import (
    MANIFEST_CASE_FIELDS,
    frozen_preamble,
    normalized_manifest_text,
    render_manifest,
)
from mandateguard.benchmark.models import (
    BENCHMARK_FAMILIES,
    BenchmarkCase,
)
from mandateguard.benchmark.results import (
    EXECUTION_SCHEMA_VERSION,
    CaseExecutionResult,
    ExecutionErrorRecord,
    ObservedOutcome,
    RegisteredLabel,
    build_run_summary,
    result_record_line,
)
from mandateguard.core.canonical import canonical_json_bytes, canonical_json_text
from mandateguard.models.finding import TIER_A_FAMILIES as POLICY_TIER_A_FAMILIES


CORPUS_SUBDIRECTORY = Path("benchmark") / "cases" / "tier_ab"
MANIFEST_PATH = Path("benchmark") / "MANIFEST.yaml"
GENERATION_SUMMARY_PATH = (
    Path("benchmark") / "generated" / "TIER_AB_GENERATION_SUMMARY.json"
)
RESULTS_SUBDIRECTORY = Path("benchmark") / "results" / "tier_ab"
JOURNAL_NAME = "first_run.jsonl"
SUMMARY_NAME = "FIRST_RUN_SUMMARY.json"

EXPECTED_TOTAL = 1_008

RUN_ID_DOMAIN = "mandateguard-tier-ab-first-run-v1"

Clock = Callable[[], datetime]


class ExecutionPreconditionError(RuntimeError):
    """Raised when the repository is not in a state that may be executed."""


# --- repository state ------------------------------------------------------


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ExecutionPreconditionError(
            f"git {' '.join(arguments)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout


def git_head_sha(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def git_status_porcelain(root: Path) -> str:
    return _git(root, "status", "--porcelain").strip()


def require_clean_worktree(root: Path) -> None:
    """Refuse to execute from a working tree the recorded SHA cannot describe."""

    status = git_status_porcelain(root)
    if status:
        raise ExecutionPreconditionError(
            "refusing to run the first registered execution from a dirty working "
            "tree; execution_code_git_sha would not describe the code that ran:\n"
            f"{status}"
        )


# --- corpus loading and digests -------------------------------------------


def corpus_path(root: Path, family_id: str) -> Path:
    return root / CORPUS_SUBDIRECTORY / f"{family_id}.jsonl"


def _lf_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def corpus_file_digests(root: Path) -> dict[str, str]:
    """SHA-256 of each family file, LF-normalized, matching the D7 summary."""

    return {
        f"{family_id}.jsonl": hashlib.sha256(
            _lf_bytes(corpus_path(root, family_id))
        ).hexdigest()
        for family_id in BENCHMARK_FAMILIES
    }


def manifest_cases_sha256(root: Path) -> str:
    """Canonical digest of the manifest cases section, preamble excluded."""

    text = normalized_manifest_text((root / MANIFEST_PATH).read_bytes())
    index = text.index("\ncases:\n")
    return hashlib.sha256(text[index + 1 :].encode("utf-8")).hexdigest()


def read_corpus_records(root: Path) -> list[dict[str, Any]]:
    """Raw registered records in registered order: family order, then file order."""

    records: list[dict[str, Any]] = []
    for family_id in BENCHMARK_FAMILIES:
        text = corpus_path(root, family_id).read_text(encoding="utf-8")
        records.extend(json.loads(line) for line in text.splitlines())
    return records


def load_registered_corpus(root: Path) -> list[BenchmarkCase]:
    """Decode every registered case, re-verifying its frozen content digest."""

    return [decode_case(record) for record in read_corpus_records(root)]


def frozen_content_map(root: Path) -> dict[str, str]:
    """The immutable ``case_id -> case_content_sha256`` mapping."""

    return {
        record["case_id"]: record["case_content_sha256"]
        for record in read_corpus_records(root)
    }


def read_manifest_records(root: Path) -> list[dict[str, str | None]]:
    text = normalized_manifest_text((root / MANIFEST_PATH).read_bytes())
    body = text[text.index("\ncases:\n") + len("\ncases:\n") :]
    records: list[dict[str, str | None]] = []
    current: dict[str, str | None] = {}
    for line in body.splitlines():
        if line.startswith("  - "):
            if current:
                records.append(current)
            current = {}
            line = "    " + line[4:]
        key, _, raw = line.strip().partition(": ")
        current[key] = None if raw == "null" else raw.strip('"')
    if current:
        records.append(current)
    return records


# --- pre-execution seal ----------------------------------------------------


def environment_snapshot() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "python_build": " ".join(platform.python_build()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "os_name": os.name,
        "execution_mode": "single process, in-process call, no warm-up, no caching",
    }


def derive_run_id(
    *,
    execution_code_git_sha: str,
    corpus_generation_git_sha: str,
    benchmark_protocol_git_sha: str,
    run_started_at: datetime,
) -> str:
    """Deterministic run identity. No uuid4, no secrets, no ambient randomness.

    A timestamp participates because the execution is itself an audit event:
    two runs of identical code over an identical corpus are different events
    and must not collide onto one identifier.
    """

    identity = {
        "domain": RUN_ID_DOMAIN,
        "execution_schema_version": EXECUTION_SCHEMA_VERSION,
        "execution_code_git_sha": execution_code_git_sha,
        "corpus_generation_git_sha": corpus_generation_git_sha,
        "benchmark_protocol_git_sha": benchmark_protocol_git_sha,
        "run_started_at": encode_timestamp(run_started_at),
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def build_pre_execution_seal(
    *,
    root: Path,
    cases: Sequence[BenchmarkCase],
    records: Sequence[Mapping[str, Any]],
    execution_code_git_sha: str,
    corpus_generation_git_sha: str,
    benchmark_protocol_git_sha: str,
    run_started_at: datetime,
) -> dict[str, Any]:
    """Prove exactly what was about to be executed, before the first policy call."""

    manifest_records = read_manifest_records(root)
    family_counts: dict[str, int] = {family: 0 for family in BENCHMARK_FAMILIES}
    class_counts: dict[str, int] = {}
    ground_truth_counts: dict[str, int] = {}
    expected_action_counts = {"ALLOW": 0, "REVIEW": 0, "BLOCK": 0}
    target_status_counts: dict[str, int] = {}
    for case in cases:
        family_counts[case.family_id] += 1
        case_class = case.case_id.split("-")[1]
        class_counts[case_class] = class_counts.get(case_class, 0) + 1
        ground_truth_counts[case.ground_truth] = (
            ground_truth_counts.get(case.ground_truth, 0) + 1
        )
        expected_action_counts[case.expected_action] += 1
        status = case.target_expectation.status
        target_status_counts[status] = target_status_counts.get(status, 0) + 1

    seal = {
        "execution_schema_version": EXECUTION_SCHEMA_VERSION,
        "execution_code_git_sha": execution_code_git_sha,
        "corpus_generation_git_sha": corpus_generation_git_sha,
        "benchmark_protocol_git_sha": benchmark_protocol_git_sha,
        "run_started_at": encode_timestamp(run_started_at),
        "total_registered_cases": len(cases),
        "unique_case_ids": len({case.case_id for case in cases}),
        "unique_case_content_sha256": len(
            {record["case_content_sha256"] for record in records}
        ),
        "first_run_at_null_count": sum(
            1 for record in records if record["first_run_at"] is None
        ),
        "manifest_first_run_at_null_count": sum(
            1 for record in manifest_records if record["first_run_at"] is None
        ),
        "per_family_counts": family_counts,
        "class_counts": {key: class_counts[key] for key in sorted(class_counts)},
        "ground_truth_counts": {
            key: ground_truth_counts[key] for key in sorted(ground_truth_counts)
        },
        "expected_action_counts": expected_action_counts,
        "registered_target_status_counts": {
            key: target_status_counts[key] for key in sorted(target_status_counts)
        },
        "semantic_constraint_case_count": sum(
            1
            for case in cases
            if case.evaluation_inputs.mandate.payload.constraints.semantic
        ),
        "pre_execution_corpus_sha256": corpus_file_digests(root),
        "pre_execution_manifest_cases_sha256": manifest_cases_sha256(root),
        "generation_summary_sha256": hashlib.sha256(
            _lf_bytes(root / GENERATION_SUMMARY_PATH)
        ).hexdigest(),
        "environment": environment_snapshot(),
    }
    seal["execution_run_id"] = derive_run_id(
        execution_code_git_sha=execution_code_git_sha,
        corpus_generation_git_sha=corpus_generation_git_sha,
        benchmark_protocol_git_sha=benchmark_protocol_git_sha,
        run_started_at=run_started_at,
    )
    return seal


def validate_pre_execution_seal(seal: Mapping[str, Any]) -> None:
    """Refuse to execute unless the seal describes the registered corpus."""

    expectations = {
        "total_registered_cases": EXPECTED_TOTAL,
        "unique_case_ids": EXPECTED_TOTAL,
        "unique_case_content_sha256": EXPECTED_TOTAL,
        "first_run_at_null_count": EXPECTED_TOTAL,
        "manifest_first_run_at_null_count": EXPECTED_TOTAL,
        "semantic_constraint_case_count": 0,
    }
    for key, expected in expectations.items():
        if seal[key] != expected:
            raise ExecutionPreconditionError(
                f"pre-execution seal rejects the run: {key} is {seal[key]!r}, "
                f"expected {expected!r}"
            )
    if len(seal["pre_execution_corpus_sha256"]) != len(BENCHMARK_FAMILIES):
        raise ExecutionPreconditionError("seal does not cover all 18 corpus files")


# --- containment -----------------------------------------------------------


@contextmanager
def network_blocked() -> Iterator[None]:
    """Make the "no network" claim enforced rather than merely asserted."""

    def _refuse(*_arguments: object, **_keywords: object) -> None:
        raise ExecutionPreconditionError(
            "the deterministic benchmark run attempted a network connection"
        )

    saved = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.create_connection,
    )
    socket.socket.connect = _refuse  # type: ignore[method-assign]
    socket.socket.connect_ex = _refuse  # type: ignore[method-assign]
    socket.create_connection = _refuse  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = saved[0]  # type: ignore[method-assign]
        socket.socket.connect_ex = saved[1]  # type: ignore[method-assign]
        socket.create_connection = saved[2]  # type: ignore[assignment]


# --- one case, one frozen policy evaluation -------------------------------


@dataclass(frozen=True, slots=True)
class _Evaluated:
    observed: ObservedOutcome
    latency_ns: int


def _observe(result: Any) -> ObservedOutcome:
    """Read the frozen result. Nothing here decides anything."""

    deterministic = result.deterministic_decision
    tier_a = tuple(
        (item.family.value, item.status.value, item.reason)
        for item in deterministic.tier_a_results
    )
    tier_a_findings = tuple(
        finding.family.value
        for finding in deterministic.findings
        if finding.family in POLICY_TIER_A_FAMILIES
    )
    tier_b_findings = tuple(
        finding.family.value
        for finding in deterministic.findings
        if finding.family not in POLICY_TIER_A_FAMILIES
    )
    return ObservedOutcome(
        final_action=result.final_action.value,
        tier_a_results=tier_a,
        tier_b_finding_families=tier_b_findings,
        tier_a_finding_families=tier_a_findings,
        semantic_decision_present=result.semantic_decision is not None,
        semantic_constraints_present=bool(result.semantic_constraints_present),
    )


def evaluate_case(case: BenchmarkCase) -> _Evaluated:
    """One case, one call into the frozen authorization path, one timing span.

    The import is local so that merely importing this module does not pull the
    policy in: the harness tests exercise serialization and scoring without any
    detector loaded.
    """

    from mandateguard.semantic.orchestration import authorize_transaction

    inputs = case.evaluation_inputs
    started_ns = time.perf_counter_ns()
    result = authorize_transaction(
        mandate=inputs.mandate,
        transaction=inputs.transaction,
        catalog_snapshot=inputs.catalog_snapshot,
        server_time=inputs.server_time,
        nonce_state=inputs.nonce_state,
        committed_hashes=inputs.psp_committed_hashes,
        replay_seed=inputs.replay_seed,
        evaluated_at=inputs.evaluated_at,
        semantic_evidence=None,
        semantic_verifier=None,
    )
    elapsed_ns = time.perf_counter_ns() - started_ns
    observed = _observe(result)
    if observed.semantic_decision_present or observed.semantic_constraints_present:
        raise ExecutionPreconditionError(
            f"case {case.case_id} touched the semantic path; D7 first execution "
            "is deterministic-only"
        )
    return _Evaluated(observed=observed, latency_ns=elapsed_ns)


def _registered_label(case: BenchmarkCase) -> RegisteredLabel:
    return RegisteredLabel(
        evidence_tier=case.evidence_tier,
        family_id=case.family_id,
        ground_truth=case.ground_truth,
        expected_action=case.expected_action,
        target_family_id=case.target_expectation.family_id,
        target_status=case.target_expectation.status,
    )


def execute_case(
    case: BenchmarkCase,
    *,
    content_sha256: str,
    execution_run_id: str,
    execution_code_git_sha: str,
    clock: Clock,
) -> CaseExecutionResult:
    """Attempt one registered case, recording an honest result either way."""

    first_run_at = clock()
    try:
        evaluated = evaluate_case(case)
    except Exception as error:  # noqa: BLE001 - a raise is a result, not a crash
        return CaseExecutionResult(
            case_id=case.case_id,
            case_content_sha256=content_sha256,
            first_run_at=first_run_at,
            execution_run_id=execution_run_id,
            execution_code_git_sha=execution_code_git_sha,
            registered=_registered_label(case),
            observed=None,
            authorization_latency_ns=0,
            error=ExecutionErrorRecord.from_exception(error),
        )
    return CaseExecutionResult(
        case_id=case.case_id,
        case_content_sha256=content_sha256,
        first_run_at=first_run_at,
        execution_run_id=execution_run_id,
        execution_code_git_sha=execution_code_git_sha,
        registered=_registered_label(case),
        observed=evaluated.observed,
        authorization_latency_ns=evaluated.latency_ns,
        error=None,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- the run ---------------------------------------------------------------


def run_cases(
    cases: Sequence[BenchmarkCase],
    *,
    content_map: Mapping[str, str],
    execution_run_id: str,
    execution_code_git_sha: str,
    journal_path: Path,
    clock: Clock = utc_now,
    progress: Callable[[int, CaseExecutionResult], None] | None = None,
) -> list[CaseExecutionResult]:
    """Append every attempted case to the journal immediately, then continue.

    The journal is opened for append and flushed to the OS and to disk after
    each record, so an externally interrupted run leaves a readable partial
    journal rather than nothing. This function never deletes it.
    """

    journal_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[CaseExecutionResult] = []
    with journal_path.open("a", encoding="utf-8", newline="\n") as journal:
        for index, case in enumerate(cases, start=1):
            result = execute_case(
                case,
                content_sha256=content_map[case.case_id],
                execution_run_id=execution_run_id,
                execution_code_git_sha=execution_code_git_sha,
                clock=clock,
            )
            journal.write(result_record_line(result) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
            results.append(result)
            if progress is not None:
                progress(index, result)
    return results


# --- first_run_at lifecycle ------------------------------------------------


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """What the lifecycle write actually did, for the audit summary."""

    corpus_updated: int
    manifest_updated: int
    content_hashes_preserved: int
    content_hashes_total: int
    post_metadata_corpus_sha256: dict[str, str]


def apply_first_run_lifecycle(
    root: Path,
    first_run_by_case_id: Mapping[str, datetime],
    *,
    frozen_content: Mapping[str, str],
) -> LifecycleOutcome:
    """Record ``first_run_at`` in both registered mirrors, hashes untouched.

    ``benchmark/MANIFEST.yaml`` registers ``first_run_at`` as "Null until first
    detector execution, then immutable", and the field is excluded from
    ``case_content_sha256`` by the manifest hash policy, by PROTOCOL section 6,
    and by the codec's content projection. This function therefore writes only
    that one field, and verifies every content digest afterwards.
    """

    corpus_updated = 0
    preserved = 0
    for family_id in BENCHMARK_FAMILIES:
        path = corpus_path(root, family_id)
        lines: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            case_id = record["case_id"]
            if case_id in first_run_by_case_id:
                if record["first_run_at"] is not None:
                    raise ExecutionPreconditionError(
                        f"case {case_id} already records a first run; first_run_at "
                        "is immutable once recorded"
                    )
                record["first_run_at"] = encode_timestamp(first_run_by_case_id[case_id])
                corpus_updated += 1
            lines.append(canonical_json_text(record))
            case = decode_case(record)
            if case_content_sha256(case) != record["case_content_sha256"]:
                raise ExecutionPreconditionError(
                    f"case {case_id} content digest moved during the lifecycle update"
                )
            if record["case_content_sha256"] == frozen_content[case_id]:
                preserved += 1
        _atomic_write(path, "\n".join(lines) + "\n")

    manifest_records = read_manifest_records(root)
    manifest_updated = 0
    for record in manifest_records:
        case_id = record["case_id"]
        if case_id in first_run_by_case_id:
            if record["first_run_at"] is not None:
                raise ExecutionPreconditionError(
                    f"manifest case {case_id} already records a first run"
                )
            record["first_run_at"] = encode_timestamp(first_run_by_case_id[case_id])
            manifest_updated += 1
        if tuple(record) != MANIFEST_CASE_FIELDS:
            raise ExecutionPreconditionError(
                f"manifest case {case_id} fields drifted from the registered schema"
            )
    manifest_path = root / MANIFEST_PATH
    preamble = frozen_preamble(manifest_path)
    _atomic_write(manifest_path, render_manifest(preamble, manifest_records))

    return LifecycleOutcome(
        corpus_updated=corpus_updated,
        manifest_updated=manifest_updated,
        content_hashes_preserved=preserved,
        content_hashes_total=len(frozen_content),
        post_metadata_corpus_sha256=corpus_file_digests(root),
    )


def _atomic_write(path: Path, text: str) -> None:
    """Write through a sibling temporary file so a failure leaves no partial."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def verify_content_hashes_unchanged(
    root: Path, frozen_content: Mapping[str, str]
) -> tuple[int, list[str]]:
    """Compare all 1,008 digests against the mapping captured before execution."""

    current = frozen_content_map(root)
    preserved = 0
    changed: list[str] = []
    for case_id, digest in frozen_content.items():
        if current.get(case_id) == digest:
            preserved += 1
        else:
            changed.append(case_id)
    return preserved, changed


# --- summary ---------------------------------------------------------------


def write_run_summary(
    path: Path,
    *,
    seal: Mapping[str, Any],
    results: Sequence[CaseExecutionResult],
    run_completed_at: datetime,
    lifecycle: LifecycleOutcome | None,
    content_hashes_preserved: int,
    first_run_at_populated: int,
) -> dict[str, Any]:
    summary = build_run_summary(
        seal=seal,
        results=results,
        run_completed_at=run_completed_at,
        post_metadata_corpus_sha256=(
            None if lifecycle is None else lifecycle.post_metadata_corpus_sha256
        ),
        content_hashes_preserved=content_hashes_preserved,
        first_run_at_populated=first_run_at_populated,
    )
    summary["lifecycle"] = (
        None
        if lifecycle is None
        else {
            "corpus_records_updated": lifecycle.corpus_updated,
            "manifest_records_updated": lifecycle.manifest_updated,
            "case_content_sha256_preserved": lifecycle.content_hashes_preserved,
            "case_content_sha256_total": lifecycle.content_hashes_total,
        }
    )
    summary["generation_summary_disposition"] = {
        "path": GENERATION_SUMMARY_PATH.as_posix(),
        "treatment": "immutable pre-execution generation snapshot",
        "rationale": (
            "benchmark/deterministic/README.md documents the file as generation "
            "audit metadata; its corpus_file_sha256 values are the pre-execution "
            "digests and are preserved here rather than overwritten"
        ),
        "superseded_fields": {
            "registered_corpus_executed": True,
            "first_run_null_count": 0,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        path, json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return summary


def read_journal(path: Path) -> list[dict[str, Any]]:
    """Read a possibly partial run journal; an interrupted run stays readable."""

    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def journal_first_run_map(records: Sequence[Mapping[str, Any]]) -> dict[str, datetime]:
    return {
        record["case_id"]: decode_timestamp(record["first_run_at"])
        for record in records
    }
