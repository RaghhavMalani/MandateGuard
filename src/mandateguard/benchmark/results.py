"""Result records and mechanical scoring for the deterministic Tier A/B run.

This module is an observer. It never decides an action, never re-derives a
Tier A status, and never recomputes a "better" label: it copies the registered
label verbatim, copies what the frozen policy actually emitted, and compares
the two. Every comparison below is an equality test between two strings that
were produced elsewhere.

Nothing here imports the policy. ``execution`` does the single frozen
authorization call and hands the observed values in as plain strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Any, Mapping, Sequence

from mandateguard.benchmark.codec import encode_timestamp
from mandateguard.benchmark.models import (
    BENCHMARK_FAMILIES,
    TIER_A_FAMILIES,
    TIER_B_FAMILIES,
)
from mandateguard.core.canonical import canonical_json_text


EXECUTION_SCHEMA_VERSION = "d8-tier-ab-execution/1.0.0"

ACTIONS = ("ALLOW", "REVIEW", "BLOCK")
TIER_A_STATUS_VALUES = ("PASS", "FAIL", "NOT_EVALUABLE")
TIER_B_STATUS_VALUES = ("PASS", "FAIL")

MAX_ERROR_MESSAGE_CHARS = 256

# Compositions the D7 generation README recorded before any execution, plus the
# generic "did anything else fire" counters. Each is a mechanical count over
# observed output; none of them changes how a case is scored.
COMPOSITION_KEYS = (
    "A7_FAIL_with_B6_FAIL",
    "B6_FAIL_with_A7_FAIL",
    "A2_FAIL_with_dependent_NOT_EVALUABLE",
    "A3_FAIL_with_A2_FAIL",
    "A6_FAIL_with_dependent_NOT_EVALUABLE",
    "B3_FAIL_with_A1_NOT_EVALUABLE",
    "A1_NOT_EVALUABLE_with_A7_NOT_EVALUABLE",
    "A7_NOT_EVALUABLE_with_A6_NOT_EVALUABLE",
)

_A2_DEPENDENTS = ("A1", "A7", "A8")
_A6_DEPENDENTS = ("A1", "A2", "A3", "A7", "A8")


class ResultRecordError(ValueError):
    """Raised when a result record cannot be built or serialized."""


def sanitize_error_message(message: object) -> str:
    """Collapse an exception message to one bounded, single-line string.

    Exception text can carry evaluation-input fragments and platform paths. The
    benchmark only needs to know that a case raised and roughly why, so the
    message is flattened and truncated rather than stored verbatim.
    """

    text = "" if message is None else str(message)
    flattened = " ".join(text.split())
    if len(flattened) > MAX_ERROR_MESSAGE_CHARS:
        flattened = flattened[: MAX_ERROR_MESSAGE_CHARS - 1] + "…"
    return flattened


@dataclass(frozen=True, slots=True)
class ExecutionErrorRecord:
    """A typed, sanitized record of one case that raised inside policy."""

    error_type: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.error_type, str) or not self.error_type:
            raise ResultRecordError("error_type must be a non-empty string")
        if not isinstance(self.message, str):
            raise ResultRecordError("message must be a string")
        if len(self.message) > MAX_ERROR_MESSAGE_CHARS:
            raise ResultRecordError("message exceeds the sanitized bound")

    @classmethod
    def from_exception(cls, error: BaseException) -> ExecutionErrorRecord:
        return cls(
            error_type=type(error).__name__,
            message=sanitize_error_message(error),
        )


@dataclass(frozen=True, slots=True)
class RegisteredLabel:
    """The frozen label, copied field for field. Never derived from output."""

    evidence_tier: str
    family_id: str
    ground_truth: str
    expected_action: str
    target_family_id: str
    target_status: str

    def __post_init__(self) -> None:
        if self.family_id not in BENCHMARK_FAMILIES:
            raise ResultRecordError("family_id must be a registered Tier A/B family")
        if self.target_family_id != self.family_id:
            raise ResultRecordError("target_expectation must name the case family")
        if self.expected_action not in ACTIONS:
            raise ResultRecordError("expected_action must be ALLOW, REVIEW, or BLOCK")


@dataclass(frozen=True, slots=True)
class ObservedOutcome:
    """Exactly what the frozen authorization path emitted for one case."""

    final_action: str
    tier_a_results: tuple[tuple[str, str, str | None], ...]
    tier_b_finding_families: tuple[str, ...]
    tier_a_finding_families: tuple[str, ...]
    semantic_decision_present: bool
    semantic_constraints_present: bool

    def __post_init__(self) -> None:
        if self.final_action not in ACTIONS:
            raise ResultRecordError("final_action must be ALLOW, REVIEW, or BLOCK")
        families = tuple(family for family, _status, _reason in self.tier_a_results)
        if families != TIER_A_FAMILIES:
            raise ResultRecordError(
                "tier_a_results must carry A1-A8 exactly once in canonical order"
            )
        for _family, status, _reason in self.tier_a_results:
            if status not in TIER_A_STATUS_VALUES:
                raise ResultRecordError(f"unregistered Tier A status {status!r}")
        for family in self.tier_b_finding_families:
            if family not in TIER_B_FAMILIES:
                raise ResultRecordError(f"{family} is not a Tier B family")

    def tier_a_status(self, family_id: str) -> str:
        for family, status, _reason in self.tier_a_results:
            if family == family_id:
                return status
        raise ResultRecordError(f"{family_id} is absent from the Tier A state vector")

    def target_family_status(self, family_id: str) -> str:
        """Tier A: the emitted A-family status. Tier B: FAIL iff it fired."""

        if family_id in TIER_A_FAMILIES:
            return self.tier_a_status(family_id)
        if family_id in TIER_B_FAMILIES:
            return "FAIL" if family_id in self.tier_b_finding_families else "PASS"
        raise ResultRecordError(f"{family_id} is not a Tier A/B family")


@dataclass(frozen=True, slots=True)
class CaseExecutionResult:
    """One attempted registered execution, complete enough to audit alone."""

    case_id: str
    case_content_sha256: str
    first_run_at: datetime
    execution_run_id: str
    execution_code_git_sha: str
    registered: RegisteredLabel
    observed: ObservedOutcome | None
    authorization_latency_ns: int
    error: ExecutionErrorRecord | None

    def __post_init__(self) -> None:
        if (self.observed is None) == (self.error is None):
            raise ResultRecordError(
                "a result records either an observed outcome or an execution error"
            )
        if isinstance(self.authorization_latency_ns, bool) or not isinstance(
            self.authorization_latency_ns, int
        ):
            raise ResultRecordError("authorization_latency_ns must be an integer")
        if self.authorization_latency_ns < 0:
            raise ResultRecordError("authorization_latency_ns must not be negative")

    @property
    def target_family_status(self) -> str | None:
        if self.observed is None:
            return None
        return self.observed.target_family_status(self.registered.target_family_id)

    @property
    def final_action(self) -> str | None:
        return None if self.observed is None else self.observed.final_action

    @property
    def target_status_match(self) -> bool:
        """An errored case never counts as a match."""

        return self.target_family_status == self.registered.target_status

    @property
    def action_match(self) -> bool:
        return self.final_action == self.registered.expected_action


def encode_result(result: CaseExecutionResult) -> dict[str, Any]:
    observed = result.observed
    return {
        "execution_schema_version": EXECUTION_SCHEMA_VERSION,
        "case_id": result.case_id,
        "case_content_sha256": result.case_content_sha256,
        "first_run_at": encode_timestamp(result.first_run_at),
        "execution_run_id": result.execution_run_id,
        "execution_code_git_sha": result.execution_code_git_sha,
        "registered": {
            "evidence_tier": result.registered.evidence_tier,
            "family_id": result.registered.family_id,
            "ground_truth": result.registered.ground_truth,
            "expected_action": result.registered.expected_action,
            "target_expectation": {
                "family_id": result.registered.target_family_id,
                "status": result.registered.target_status,
            },
        },
        "actual": {
            "final_action": result.final_action,
            "target_family_status": result.target_family_status,
            "tier_a_results": (
                []
                if observed is None
                else [
                    {"family_id": family, "status": status, "reason": reason}
                    for family, status, reason in observed.tier_a_results
                ]
            ),
            "tier_a_finding_families": (
                [] if observed is None else list(observed.tier_a_finding_families)
            ),
            "tier_b_finding_families": (
                [] if observed is None else list(observed.tier_b_finding_families)
            ),
            "semantic_decision_present": (
                None if observed is None else observed.semantic_decision_present
            ),
            "semantic_constraints_present": (
                None if observed is None else observed.semantic_constraints_present
            ),
        },
        "comparison": {
            "target_status_match": result.target_status_match,
            "action_match": result.action_match,
        },
        "timing": {"authorization_latency_ns": result.authorization_latency_ns},
        "error": (
            None
            if result.error is None
            else {
                "error_type": result.error.error_type,
                "message": result.error.message,
            }
        ),
    }


def result_record_line(result: CaseExecutionResult) -> str:
    """One canonical JSON line: UTF-8, sorted keys, no insignificant whitespace."""

    return canonical_json_text(encode_result(result))


def nearest_rank_percentile(values: Sequence[int], percentile: int) -> int | None:
    """Integer nearest-rank percentile; no interpolation, so no floats appear."""

    if not 0 < percentile <= 100:
        raise ResultRecordError("percentile must be in (0, 100]")
    ordered = sorted(values)
    if not ordered:
        return None
    rank = ceil(percentile * len(ordered) / 100)
    return ordered[max(rank, 1) - 1]


def _empty_family_tally() -> dict[str, int]:
    return {
        "total": 0,
        "target_status_matches": 0,
        "target_status_mismatches": 0,
        "action_matches": 0,
        "action_mismatches": 0,
        "execution_errors": 0,
    }


def _tally(bucket: dict[str, int], result: CaseExecutionResult) -> None:
    bucket["total"] += 1
    if result.error is not None:
        bucket["execution_errors"] += 1
    if result.target_status_match:
        bucket["target_status_matches"] += 1
    else:
        bucket["target_status_mismatches"] += 1
    if result.action_match:
        bucket["action_matches"] += 1
    else:
        bucket["action_mismatches"] += 1


def observed_compositions(results: Sequence[CaseExecutionResult]) -> dict[str, int]:
    """Count the real multi-check states the frozen policy actually produced."""

    counts = {key: 0 for key in COMPOSITION_KEYS}
    for result in results:
        observed = result.observed
        if observed is None:
            continue
        status = observed.tier_a_status
        tier_b = observed.tier_b_finding_families
        if status("A7") == "FAIL" and "B6" in tier_b:
            counts["A7_FAIL_with_B6_FAIL"] += 1
            counts["B6_FAIL_with_A7_FAIL"] += 1
        if status("A2") == "FAIL" and any(
            status(family) == "NOT_EVALUABLE" for family in _A2_DEPENDENTS
        ):
            counts["A2_FAIL_with_dependent_NOT_EVALUABLE"] += 1
        if status("A3") == "FAIL" and status("A2") == "FAIL":
            counts["A3_FAIL_with_A2_FAIL"] += 1
        if status("A6") == "FAIL" and any(
            status(family) == "NOT_EVALUABLE" for family in _A6_DEPENDENTS
        ):
            counts["A6_FAIL_with_dependent_NOT_EVALUABLE"] += 1
        if "B3" in tier_b and status("A1") == "NOT_EVALUABLE":
            counts["B3_FAIL_with_A1_NOT_EVALUABLE"] += 1
        if status("A1") == "NOT_EVALUABLE" and status("A7") == "NOT_EVALUABLE":
            counts["A1_NOT_EVALUABLE_with_A7_NOT_EVALUABLE"] += 1
        if status("A7") == "NOT_EVALUABLE" and status("A6") == "NOT_EVALUABLE":
            counts["A7_NOT_EVALUABLE_with_A6_NOT_EVALUABLE"] += 1
    return counts


def extra_finding_counts(results: Sequence[CaseExecutionResult]) -> dict[str, int]:
    """How often a real finding fired outside the registered target family."""

    with_extra = 0
    with_extra_not_evaluable = 0
    for result in results:
        observed = result.observed
        if observed is None:
            continue
        target = result.registered.target_family_id
        fired = set(observed.tier_a_finding_families) | set(
            observed.tier_b_finding_families
        )
        if fired - {target}:
            with_extra += 1
        not_evaluable = {
            family
            for family, status, _reason in observed.tier_a_results
            if status == "NOT_EVALUABLE"
        }
        if not_evaluable - {target}:
            with_extra_not_evaluable += 1
    return {
        "cases_with_a_finding_outside_the_target_family": with_extra,
        "cases_with_a_not_evaluable_outside_the_target_family": with_extra_not_evaluable,
    }


def build_run_summary(
    *,
    seal: Mapping[str, Any],
    results: Sequence[CaseExecutionResult],
    run_completed_at: datetime,
    post_metadata_corpus_sha256: Mapping[str, str] | None,
    content_hashes_preserved: int,
    first_run_at_populated: int,
) -> dict[str, Any]:
    """Mechanical arithmetic only. No headline accuracy field is produced."""

    per_family = {family: _empty_family_tally() for family in BENCHMARK_FAMILIES}
    per_tier = {"A": _empty_family_tally(), "B": _empty_family_tally()}
    tier_a_by_target = {
        status: _empty_family_tally() for status in TIER_A_STATUS_VALUES
    }
    tier_b_by_target = {
        status: _empty_family_tally() for status in TIER_B_STATUS_VALUES
    }
    action_counts = {action: 0 for action in ACTIONS}
    observed_status_counts = {status: 0 for status in TIER_A_STATUS_VALUES}

    errors: list[dict[str, str]] = []
    target_mismatched: list[str] = []
    action_mismatched: list[str] = []
    latencies: list[int] = []

    for result in results:
        family = result.registered.family_id
        tier = result.registered.evidence_tier
        _tally(per_family[family], result)
        _tally(per_tier[tier], result)
        if tier == "A":
            _tally(tier_a_by_target[result.registered.target_status], result)
        else:
            _tally(tier_b_by_target[result.registered.target_status], result)
        if result.error is not None:
            errors.append(
                {
                    "case_id": result.case_id,
                    "error_type": result.error.error_type,
                    "message": result.error.message,
                }
            )
        else:
            action_counts[result.final_action] += 1
            latencies.append(result.authorization_latency_ns)
            status = result.target_family_status
            if status in observed_status_counts:
                observed_status_counts[status] += 1
        if not result.target_status_match:
            target_mismatched.append(result.case_id)
        if not result.action_match:
            action_mismatched.append(result.case_id)

    completed = len(results) - len(errors)
    return {
        "execution_schema_version": EXECUTION_SCHEMA_VERSION,
        "execution_run_id": seal["execution_run_id"],
        "execution_code_git_sha": seal["execution_code_git_sha"],
        "corpus_generation_git_sha": seal["corpus_generation_git_sha"],
        "benchmark_protocol_git_sha": seal["benchmark_protocol_git_sha"],
        "run_started_at": seal["run_started_at"],
        "run_completed_at": encode_timestamp(run_completed_at),
        "total_cases": len(results),
        "completed_cases": completed,
        "execution_error_count": len(errors),
        "execution_errors": errors,
        "target_state_correctness": {
            "matches": sum(1 for item in results if item.target_status_match),
            "mismatches": len(target_mismatched),
            "mismatched_case_ids": target_mismatched,
        },
        "expected_action_correctness": {
            "matches": sum(1 for item in results if item.action_match),
            "mismatches": len(action_mismatched),
            "mismatched_case_ids": action_mismatched,
        },
        "per_tier": per_tier,
        "per_family": per_family,
        "tier_a_by_registered_target_status": tier_a_by_target,
        "tier_b_by_registered_target_status": tier_b_by_target,
        "observed_target_family_status_counts": observed_status_counts,
        "actual_action_counts": action_counts,
        "observed_compositions": observed_compositions(results),
        "extra_observed_state": extra_finding_counts(results),
        "semantic_model_call_count": 0,
        "cases_with_a_semantic_decision": sum(
            1
            for item in results
            if item.observed is not None and item.observed.semantic_decision_present
        ),
        "cases_with_semantic_constraints": sum(
            1
            for item in results
            if item.observed is not None and item.observed.semantic_constraints_present
        ),
        "razorpay_call_count": 0,
        "execution_capability_issue_count": 0,
        "network_call_count": 0,
        "first_run_at_populated_count": first_run_at_populated,
        "case_content_sha256_preserved_count": content_hashes_preserved,
        "pre_execution_corpus_sha256": dict(seal["pre_execution_corpus_sha256"]),
        "pre_execution_manifest_cases_sha256": seal[
            "pre_execution_manifest_cases_sha256"
        ],
        "post_first_run_metadata_corpus_sha256": (
            None
            if post_metadata_corpus_sha256 is None
            else dict(post_metadata_corpus_sha256)
        ),
        "environment": dict(seal["environment"]),
        "latency": {
            "measurement": (
                "perf_counter_ns around the single frozen authorization call; "
                "excludes JSONL parsing, fixture reconstruction, serialization, "
                "manifest updating, and scoring"
            ),
            "population": "completed deterministic-only cases",
            "sample_count": len(latencies),
            "deterministic_p50_ns": nearest_rank_percentile(latencies, 50),
            "deterministic_p95_ns": nearest_rank_percentile(latencies, 95),
            "deterministic_min_ns": min(latencies) if latencies else None,
            "deterministic_max_ns": max(latencies) if latencies else None,
        },
        "pre_execution_seal": dict(seal),
    }
