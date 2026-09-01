"""Frozen INT-3 live execution planning with exact-hash prior-result reuse.

This module never executes a semantic provider. It compares canonical semantic
input SHA-256 values against one immutable INT-2 Stage-B artifact, reuses only
byte-identical inputs, and preregisters one no-retry call per remaining unique
hash.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mandateguard.core.hashing import sha256_canonical
from mandateguard.engineering.int3.model_manifest import (
    MODEL_FEATURE_MANIFEST_SHA256,
)
from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    REFERENCE_ACTIONS,
    SEMANTIC_BEHAVIORS,
    SubsetPlan,
)


PRIOR_RESULTS_SOURCE_COMMIT = "3946aa50c477881b1b085e35b60c9a411b6c8d64"
PRIOR_RESULTS_SOURCE_PATH = (
    "artifacts/engineering/int2/"
    "stage-b-live-20260830T123856Z-0e4213c/stage_b_observations.jsonl"
)
PRIOR_RESULTS_FILE_SHA256 = (
    "fff651eff74cf81dee5b504d6503dba488c1c8992b692c69feccf1c1dbe9b2a6"
)
LIVE_EXECUTION_PLAN_BASE_COMMIT = (
    "7fe60059d857399b4a40a0d85317459d00c3f7ec"
)
LIVE_EXECUTION_PLAN_FILENAME = "subset_live_execution_plan.json"
FROZEN_LIVE_EXECUTION_PLAN_SHA256 = (
    "ed6f5c57cbea9ca0399b021c3516e829fa5cb51f7e025a1f003e9b0b1cfd284d"
)

PRIOR_EXACT_RESULT = "PRIOR_EXACT_RESULT"
NEW_LIVE_EXECUTION_REQUIRED = "NEW_LIVE_EXECUTION_REQUIRED"
PLANNED_EXECUTION_STATUSES = (
    PRIOR_EXACT_RESULT,
    NEW_LIVE_EXECUTION_REQUIRED,
)


class LiveExecutionPlanError(Int3ExperimentError):
    """A frozen input cannot produce the preregistered execution plan."""


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is forbidden: {value}")


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LiveExecutionPlanError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class PriorExactSemanticResult:
    semantic_input_sha256: str
    prior_run_id: str
    prior_observation_id: str
    model_id: str
    prompt_version: str
    detector_version: str
    observed_semantic_behavior: str
    observed_final_action: str
    semantic_verdict: str
    semantic_output_sha256: str
    reason: str
    reason_code: str | None
    provider_response_id: str | None
    constraint_results: tuple[Mapping[str, str], ...]

    def __post_init__(self) -> None:
        _digest(self.semantic_input_sha256, "semantic_input_sha256")
        _digest(self.semantic_output_sha256, "semantic_output_sha256")
        for value, name in (
            (self.prior_run_id, "prior_run_id"),
            (self.prior_observation_id, "prior_observation_id"),
            (self.model_id, "model_id"),
            (self.prompt_version, "prompt_version"),
            (self.detector_version, "detector_version"),
            (self.reason, "reason"),
        ):
            if not isinstance(value, str) or not value:
                raise LiveExecutionPlanError(f"{name} must be non-empty")
        if self.observed_semantic_behavior not in SEMANTIC_BEHAVIORS:
            raise LiveExecutionPlanError("prior semantic behavior is invalid")
        if self.observed_final_action not in REFERENCE_ACTIONS:
            raise LiveExecutionPlanError("prior final action is invalid")
        if self.semantic_verdict != self.observed_semantic_behavior:
            raise LiveExecutionPlanError(
                "prior semantic verdict and behavior must agree"
            )
        if self.reason_code is not None and not isinstance(self.reason_code, str):
            raise LiveExecutionPlanError("reason_code must be a string or null")
        if self.provider_response_id is not None and not isinstance(
            self.provider_response_id, str
        ):
            raise LiveExecutionPlanError(
                "provider_response_id must be a string or null"
            )
        if not isinstance(self.constraint_results, tuple) or not self.constraint_results:
            raise LiveExecutionPlanError("constraint_results must be non-empty")
        frozen: list[Mapping[str, str]] = []
        for item in self.constraint_results:
            if not isinstance(item, Mapping) or set(item) != {
                "constraint_id",
                "status",
                "reason",
            }:
                raise LiveExecutionPlanError("constraint result fields are invalid")
            parsed = {key: item[key] for key in ("constraint_id", "status", "reason")}
            if not all(isinstance(value, str) and value for value in parsed.values()):
                raise LiveExecutionPlanError("constraint result values are invalid")
            frozen.append(MappingProxyType(parsed))
        object.__setattr__(self, "constraint_results", tuple(frozen))

    def record(self) -> dict[str, object]:
        return {
            "observed_semantic_behavior": self.observed_semantic_behavior,
            "observed_final_action": self.observed_final_action,
            "semantic_verdict": self.semantic_verdict,
            "semantic_output_sha256": self.semantic_output_sha256,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "provider_response_id": self.provider_response_id,
            "constraint_results": [dict(item) for item in self.constraint_results],
        }


@dataclass(frozen=True, slots=True, init=False)
class PriorExactResultIndex:
    _by_hash: Mapping[str, PriorExactSemanticResult]

    def __init__(self, values: Mapping[str, PriorExactSemanticResult]) -> None:
        if not isinstance(values, Mapping) or not values:
            raise LiveExecutionPlanError("prior exact result index must be non-empty")
        parsed: dict[str, PriorExactSemanticResult] = {}
        for digest, result in values.items():
            _digest(digest, "prior result key")
            if not isinstance(result, PriorExactSemanticResult):
                raise LiveExecutionPlanError("prior result index value is invalid")
            if digest != result.semantic_input_sha256:
                raise LiveExecutionPlanError("prior result key/hash mismatch")
            parsed[digest] = result
        object.__setattr__(self, "_by_hash", MappingProxyType(parsed))

    def for_hash(self, semantic_input_sha256: str) -> PriorExactSemanticResult | None:
        _digest(semantic_input_sha256, "semantic_input_sha256")
        return self._by_hash.get(semantic_input_sha256)

    @property
    def hashes(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_hash))


def load_prior_exact_results(path: Path) -> PriorExactResultIndex:
    """Load and hash-bind the immutable INT-2 Stage-B execution results."""

    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise LiveExecutionPlanError("prior result artifact is unavailable") from error
    if sha256(raw).hexdigest() != PRIOR_RESULTS_FILE_SHA256:
        raise LiveExecutionPlanError(
            "prior result artifact differs from the frozen source commit"
        )
    try:
        records = tuple(
            json.loads(
                line,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_json_number,
            )
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        )
    except (UnicodeError, ValueError, TypeError) as error:
        raise LiveExecutionPlanError("prior result artifact is malformed") from error
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            raise LiveExecutionPlanError("prior result record must be an object")
        digest = record.get("semantic_input_sha256")
        if record.get("semantic_status") != "EVALUATED" or digest is None:
            continue
        grouped[_digest(digest, "prior semantic_input_sha256")].append(record)
    if not grouped:
        raise LiveExecutionPlanError("prior artifact contains no evaluated inputs")

    indexed: dict[str, PriorExactSemanticResult] = {}
    consistency_fields = (
        "run_id",
        "model_id",
        "prompt_version",
        "detector_version",
        "observed_semantic_behavior",
        "final_action",
        "semantic_verdict",
        "semantic_output_sha256",
        "reason",
        "reason_code",
        "constraint_results",
    )
    for digest, members in grouped.items():
        canonical_ids = {
            item.get("canonical_execution_observation_id") for item in members
        }
        if len(canonical_ids) != 1 or None in canonical_ids:
            raise LiveExecutionPlanError("prior canonical execution provenance conflicts")
        canonical_id = canonical_ids.pop()
        canonical = next(
            (item for item in members if item.get("observation_id") == canonical_id),
            None,
        )
        if canonical is None:
            raise LiveExecutionPlanError("prior canonical observation is absent")
        for name in consistency_fields:
            if len({json.dumps(item.get(name), sort_keys=True) for item in members}) != 1:
                raise LiveExecutionPlanError(
                    f"prior exact-hash records disagree on {name}"
                )
        raw_constraints = canonical.get("constraint_results")
        if not isinstance(raw_constraints, list):
            raise LiveExecutionPlanError("prior constraint results are invalid")
        indexed[digest] = PriorExactSemanticResult(
            semantic_input_sha256=digest,
            prior_run_id=canonical.get("run_id"),
            prior_observation_id=canonical_id,
            model_id=canonical.get("model_id"),
            prompt_version=canonical.get("prompt_version"),
            detector_version=canonical.get("detector_version"),
            observed_semantic_behavior=canonical.get("observed_semantic_behavior"),
            observed_final_action=canonical.get("final_action"),
            semantic_verdict=canonical.get("semantic_verdict"),
            semantic_output_sha256=canonical.get("semantic_output_sha256"),
            reason=canonical.get("reason"),
            reason_code=canonical.get("reason_code"),
            provider_response_id=canonical.get("provider_response_id"),
            constraint_results=tuple(raw_constraints),
        )
    return PriorExactResultIndex(indexed)


@dataclass(frozen=True, slots=True)
class LiveExecutionObservation:
    observation_id: str
    query_id: str
    eligible_evidence_ids: tuple[str, ...]
    subset_evidence_ids: tuple[str, ...]
    subset_size: int
    eligible_size: int
    semantic_input_sha256: str
    execution_status: str
    canonical_plan_observation_id: str
    planned_new_semantic_api_call: bool
    prior_exact_match: bool
    prior_run_id: str | None
    prior_observation_id: str | None
    prior_result: PriorExactSemanticResult | None
    full_reference_semantic_behavior: str
    full_reference_action: str
    decision_stable: None = None

    def __post_init__(self) -> None:
        _digest(self.semantic_input_sha256, "semantic_input_sha256")
        if self.execution_status not in PLANNED_EXECUTION_STATUSES:
            raise LiveExecutionPlanError("execution_status is invalid")
        if self.execution_status == PRIOR_EXACT_RESULT:
            if (
                not self.prior_exact_match
                or self.prior_result is None
                or self.prior_run_id != self.prior_result.prior_run_id
                or self.prior_observation_id
                != self.prior_result.prior_observation_id
                or self.planned_new_semantic_api_call
            ):
                raise LiveExecutionPlanError("prior exact-result plan is inconsistent")
        else:
            if (
                self.prior_exact_match
                or self.prior_result is not None
                or self.prior_run_id is not None
                or self.prior_observation_id is not None
            ):
                raise LiveExecutionPlanError("new execution plan carries prior result data")
        if self.decision_stable is not None:
            raise LiveExecutionPlanError("stability labels remain null in the frozen plan")


@dataclass(frozen=True, slots=True)
class UniqueSemanticInputPlan:
    semantic_input_sha256: str
    execution_status: str
    member_observation_ids: tuple[str, ...]
    canonical_plan_observation_id: str
    planned_new_semantic_api_call: bool
    prior_run_id: str | None
    prior_observation_id: str | None


@dataclass(frozen=True, slots=True)
class LiveExecutionPlan:
    schema_version: str
    created_at: datetime
    model_id: str
    prompt_version: str
    detector_version: str
    observations: tuple[LiveExecutionObservation, ...]
    unique_inputs: tuple[UniqueSemanticInputPlan, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise LiveExecutionPlanError("schema_version must be 1.0")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise LiveExecutionPlanError("created_at must be timezone-aware")
        if len(self.observations) != 62:
            raise LiveExecutionPlanError("live execution plan must cover 62 observations")
        ids = tuple(item.observation_id for item in self.observations)
        if len(ids) != len(set(ids)):
            raise LiveExecutionPlanError("observation IDs must be unique")
        members = tuple(
            member for item in self.unique_inputs for member in item.member_observation_ids
        )
        if len(members) != len(set(members)) or set(members) != set(ids):
            raise LiveExecutionPlanError("unique inputs must partition observations")
        planned = sum(
            1 for item in self.unique_inputs if item.planned_new_semantic_api_call
        )
        if planned != self.predicted_new_semantic_api_calls:
            raise LiveExecutionPlanError("predicted call count is inconsistent")

    @property
    def nominal_observation_count(self) -> int:
        return len(self.observations)

    @property
    def unique_semantic_input_count(self) -> int:
        return len(self.unique_inputs)

    @property
    def prior_exact_result_unique_input_count(self) -> int:
        return sum(
            1 for item in self.unique_inputs if item.execution_status == PRIOR_EXACT_RESULT
        )

    @property
    def prior_exact_result_observation_count(self) -> int:
        return sum(1 for item in self.observations if item.prior_exact_match)

    @property
    def new_unique_input_count(self) -> int:
        return sum(
            1
            for item in self.unique_inputs
            if item.execution_status == NEW_LIVE_EXECUTION_REQUIRED
        )

    @property
    def predicted_new_semantic_api_calls(self) -> int:
        return self.new_unique_input_count


def build_live_execution_plan(
    *,
    subset_plan: SubsetPlan,
    prior_results: PriorExactResultIndex,
    created_at: datetime,
) -> LiveExecutionPlan:
    """Classify every subset by exact immutable prior hash, without execution."""

    if not isinstance(subset_plan, SubsetPlan):
        raise TypeError("subset_plan must be SubsetPlan")
    if not isinstance(prior_results, PriorExactResultIndex):
        raise TypeError("prior_results must be PriorExactResultIndex")
    grouped: dict[str, list[object]] = defaultdict(list)
    for observation in subset_plan.observations:
        grouped[observation.subset_semantic_input_sha256].append(observation)

    observations: list[LiveExecutionObservation] = []
    unique_inputs: list[UniqueSemanticInputPlan] = []
    for digest, members in grouped.items():
        prior = prior_results.for_hash(digest)
        status = PRIOR_EXACT_RESULT if prior is not None else NEW_LIVE_EXECUTION_REQUIRED
        canonical_id = members[0].observation_id
        if prior is not None and (
            prior.model_id != subset_plan.model_id
            or prior.prompt_version != subset_plan.prompt_version
            or prior.detector_version != subset_plan.detector_version
        ):
            raise LiveExecutionPlanError(
                "prior exact input provenance disagrees with the subset plan"
            )
        unique_inputs.append(
            UniqueSemanticInputPlan(
                semantic_input_sha256=digest,
                execution_status=status,
                member_observation_ids=tuple(item.observation_id for item in members),
                canonical_plan_observation_id=canonical_id,
                planned_new_semantic_api_call=prior is None,
                prior_run_id=prior.prior_run_id if prior is not None else None,
                prior_observation_id=(
                    prior.prior_observation_id if prior is not None else None
                ),
            )
        )
        for member in members:
            observations.append(
                LiveExecutionObservation(
                    observation_id=member.observation_id,
                    query_id=member.query_id,
                    eligible_evidence_ids=member.eligible_evidence_ids,
                    subset_evidence_ids=member.subset_evidence_ids,
                    subset_size=member.subset_size,
                    eligible_size=member.eligible_size,
                    semantic_input_sha256=digest,
                    execution_status=status,
                    canonical_plan_observation_id=canonical_id,
                    planned_new_semantic_api_call=(
                        prior is None and member.observation_id == canonical_id
                    ),
                    prior_exact_match=prior is not None,
                    prior_run_id=prior.prior_run_id if prior is not None else None,
                    prior_observation_id=(
                        prior.prior_observation_id if prior is not None else None
                    ),
                    prior_result=prior,
                    full_reference_semantic_behavior=(
                        member.full_reference_semantic_behavior
                    ),
                    full_reference_action=member.full_reference_action,
                )
            )
    by_id = {item.observation_id: item for item in observations}
    ordered_observations = tuple(
        by_id[item.observation_id] for item in subset_plan.observations
    )
    return LiveExecutionPlan(
        schema_version="1.0",
        created_at=created_at,
        model_id=subset_plan.model_id,
        prompt_version=subset_plan.prompt_version,
        detector_version=subset_plan.detector_version,
        observations=ordered_observations,
        unique_inputs=tuple(unique_inputs),
    )


def _prior_result_provenance(
    observation: LiveExecutionObservation,
) -> dict[str, object] | None:
    if observation.prior_result is None:
        return None
    return {
        "source_commit": PRIOR_RESULTS_SOURCE_COMMIT,
        "source_path": PRIOR_RESULTS_SOURCE_PATH,
        "source_file_sha256": PRIOR_RESULTS_FILE_SHA256,
        "prior_run_id": observation.prior_run_id,
        "prior_observation_id": observation.prior_observation_id,
        "exact_match_field": "semantic_input_sha256",
    }


def live_execution_plan_payload(plan: LiveExecutionPlan) -> dict[str, object]:
    """Return the canonical plan payload, excluding its self-hash."""

    if not isinstance(plan, LiveExecutionPlan):
        raise TypeError("plan must be LiveExecutionPlan")
    created_at = plan.created_at.isoformat().replace("+00:00", "Z")
    return {
        "schema_version": plan.schema_version,
        "created_at": created_at,
        "status": "FROZEN_BEFORE_INT3_LIVE_SUBSET_EXECUTION",
        "base_commit": LIVE_EXECUTION_PLAN_BASE_COMMIT,
        "model_id": plan.model_id,
        "prompt_version": plan.prompt_version,
        "detector_version": plan.detector_version,
        "model_feature_manifest_sha256": MODEL_FEATURE_MANIFEST_SHA256,
        "target": {
            "column": "decision_stable",
            "terminology": "SINGLE_EXECUTION_ACTION_STABILITY",
            "definition": (
                "subset_final_action == frozen_full_evidence_final_action"
            ),
        },
        "execution_policy": {
            "deduplication_key": "exact semantic_input_sha256",
            "fuzzy_reuse_allowed": False,
            "new_input_max_attempts": 1,
            "new_input_retries": 0,
            "prior_exact_results_make_api_calls": False,
        },
        "prior_result_source": {
            "source_commit": PRIOR_RESULTS_SOURCE_COMMIT,
            "source_path": PRIOR_RESULTS_SOURCE_PATH,
            "source_file_sha256": PRIOR_RESULTS_FILE_SHA256,
        },
        "nominal_observation_count": plan.nominal_observation_count,
        "unique_semantic_input_count": plan.unique_semantic_input_count,
        "prior_exact_result_unique_input_count": (
            plan.prior_exact_result_unique_input_count
        ),
        "prior_exact_result_observation_count": (
            plan.prior_exact_result_observation_count
        ),
        "new_unique_input_count": plan.new_unique_input_count,
        "predicted_new_semantic_api_calls": (
            plan.predicted_new_semantic_api_calls
        ),
        "observations": [
            {
                "observation_id": item.observation_id,
                "query_id": item.query_id,
                "eligible_evidence_ids": list(item.eligible_evidence_ids),
                "subset_evidence_ids": list(item.subset_evidence_ids),
                "subset_size": item.subset_size,
                "eligible_size": item.eligible_size,
                "semantic_input_sha256": item.semantic_input_sha256,
                "execution_status": item.execution_status,
                "canonical_plan_observation_id": (
                    item.canonical_plan_observation_id
                ),
                "planned_new_semantic_api_call": (
                    item.planned_new_semantic_api_call
                ),
                "prior_exact_match": item.prior_exact_match,
                "prior_run_id": item.prior_run_id,
                "prior_observation_id": item.prior_observation_id,
                "prior_result_provenance": _prior_result_provenance(item),
                "observed_semantic_result": (
                    item.prior_result.record()
                    if item.prior_result is not None
                    else None
                ),
                "full_reference_semantic_behavior": (
                    item.full_reference_semantic_behavior
                ),
                "full_reference_action": item.full_reference_action,
                "decision_stable": None,
            }
            for item in plan.observations
        ],
        "unique_inputs": [
            {
                "semantic_input_sha256": item.semantic_input_sha256,
                "execution_status": item.execution_status,
                "member_observation_ids": list(item.member_observation_ids),
                "canonical_plan_observation_id": (
                    item.canonical_plan_observation_id
                ),
                "planned_new_semantic_api_call": (
                    item.planned_new_semantic_api_call
                ),
                "prior_run_id": item.prior_run_id,
                "prior_observation_id": item.prior_observation_id,
            }
            for item in plan.unique_inputs
        ],
    }


def live_execution_plan_record(plan: LiveExecutionPlan) -> dict[str, object]:
    payload = live_execution_plan_payload(plan)
    return {**payload, "canonical_sha256": sha256_canonical(payload)}


def write_live_execution_plan(plan: LiveExecutionPlan, output_path: Path) -> Path:
    """Exclusively create the frozen plan; never rewrite after outcomes exist."""

    if not isinstance(output_path, Path):
        raise TypeError("output_path must be pathlib.Path")
    record = live_execution_plan_record(plan)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    return output_path
