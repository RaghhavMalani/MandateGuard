"""Offline-only planning for the frozen INT-2 Stage-B condition matrix."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from mandateguard.engineering.int2.downstream import selected_semantic_evidence
from mandateguard.engineering.int2.stage_b_cases import StageBCaseManifest
from mandateguard.semantic.models import semantic_input_sha256
from mandateguard.semantic.verifier import (
    SEMANTIC_DETECTOR_VERSION,
    SEMANTIC_PROMPT_VERSION,
    build_semantic_request,
)


EXPECTED_CONDITIONS = (
    ("A", "NO_RETRIEVAL CONTROL", "no_retrieval", None, 1),
    ("B", "LEXICAL BASELINE", "lexical_only", None, 5),
    ("C", "SEMANTIC BASELINE", "semantic_only", None, 3),
    ("D", "BEST HYBRID", "hybrid", 0.0, 3),
    ("E", "PRODUCTION DEFAULT", "hybrid", 0.4, 5),
    ("F", "LOW-EVIDENCE STRESS CONDITION", "lexical_only", None, 1),
)


class StageBPlanError(ValueError):
    """The frozen inputs cannot produce the exact nominal Stage-B plan."""


@dataclass(frozen=True, slots=True)
class StageBCondition:
    label: str
    role: str
    configuration_id: str
    strategy: str
    alpha: float | None
    top_k: int


@dataclass(frozen=True, slots=True)
class PlannedStageBObservation:
    observation_id: str
    condition_label: str
    condition_role: str
    configuration_id: str
    strategy: str
    alpha: float | None
    top_k: int
    query_id: str
    engineering_expectation: str
    expected_final_action: str
    retrieved_evidence_ids: tuple[str, ...]
    selected_trusted_evidence_ids: tuple[str, ...]
    semantic_input_sha256: str | None
    semantic_status: str
    planned_semantic_call: bool
    semantic_execution: str
    canonical_observation_id: str | None


@dataclass(frozen=True, slots=True)
class StageBEquivalenceClass:
    semantic_input_sha256: str
    canonical_observation_id: str
    member_observation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StageBExecutionPlan:
    model_id: str
    prompt_version: str
    detector_version: str
    observations: tuple[PlannedStageBObservation, ...]
    equivalence_classes: tuple[StageBEquivalenceClass, ...]
    nominal_observation_count: int
    unique_semantic_input_count: int
    predicted_semantic_api_calls: int
    duplicate_reused_observation_count: int
    evidence_insufficient_no_call_count: int

    def __post_init__(self) -> None:
        if self.nominal_observation_count != 36 or len(self.observations) != 36:
            raise StageBPlanError("Stage-B plan must contain 36 nominal observations")
        if self.predicted_semantic_api_calls != self.unique_semantic_input_count:
            raise StageBPlanError("predicted calls must equal unique semantic inputs")


def _load_conditions(path: Path) -> tuple[StageBCondition, ...]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise StageBPlanError("cannot load frozen Stage-B selection") from error
    raw = decoded.get("selections") if isinstance(decoded, dict) else None
    if not isinstance(raw, list) or len(raw) != 6:
        raise StageBPlanError("frozen Stage-B selection must contain six conditions")
    conditions: list[StageBCondition] = []
    for index, (item, expected) in enumerate(zip(raw, EXPECTED_CONDITIONS, strict=True)):
        if not isinstance(item, dict):
            raise StageBPlanError(f"selection[{index}] must be an object")
        label, role, strategy, alpha, top_k = expected
        actual = (
            item.get("label"),
            item.get("role"),
            item.get("strategy"),
            item.get("alpha"),
            item.get("top_k"),
        )
        if actual != expected:
            raise StageBPlanError(f"selection[{index}] is not the frozen condition")
        configuration_id = item.get("configuration_id")
        if not isinstance(configuration_id, str) or not configuration_id:
            raise StageBPlanError("selection configuration_id is invalid")
        conditions.append(
            StageBCondition(
                label=label,
                role=role,
                configuration_id=configuration_id,
                strategy=strategy,
                alpha=alpha,
                top_k=top_k,
            )
        )
    return tuple(conditions)


def _load_stage_a_observations(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeError, ValueError) as error:
        raise StageBPlanError("cannot load frozen Stage-A observations") from error
    if len(records) != 192:
        raise StageBPlanError("frozen Stage-A observations must contain 192 records")
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise StageBPlanError("Stage-A observation must be an object")
        key = (record.get("configuration_id"), record.get("query_id"))
        if not all(isinstance(item, str) and item for item in key):
            raise StageBPlanError("Stage-A observation identity is invalid")
        if key in indexed:
            raise StageBPlanError("duplicate Stage-A observation identity")
        indexed[key] = record
    return indexed


def _production_evidence(
    indexed: dict[tuple[str, str], dict[str, Any]], query_id: str
) -> tuple[str, ...]:
    try:
        source = indexed[("hybrid.alpha-0.00.k-5", query_id)]
    except KeyError as error:
        raise StageBPlanError(
            "production ranking requires the frozen hybrid alpha=0 k=5 surface"
        ) from error
    raw_documents = source.get("ranked_documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise StageBPlanError("production ranking source is incomplete")
    ranked: list[tuple[float, float, float, str, str]] = []
    for item in raw_documents:
        if not isinstance(item, dict):
            raise StageBPlanError("ranked document is invalid")
        lexical = item.get("lexical_score")
        semantic = item.get("semantic_score")
        document_id = item.get("document_id")
        evidence_id = item.get("evidence_id")
        if (
            isinstance(lexical, bool)
            or not isinstance(lexical, (int, float))
            or isinstance(semantic, bool)
            or not isinstance(semantic, (int, float))
            or not isinstance(document_id, str)
            or not isinstance(evidence_id, str)
        ):
            raise StageBPlanError("ranked document scores are invalid")
        combined = 0.4 * float(lexical) + 0.6 * float(semantic)
        ranked.append(
            (-combined, -float(lexical), -float(semantic), document_id, evidence_id)
        )
    ranked.sort(key=lambda item: item[:4])
    evidence: list[str] = []
    for item in ranked:
        if item[4] not in evidence:
            evidence.append(item[4])
    return tuple(evidence[:5])


def _retrieved_evidence(
    condition: StageBCondition,
    query_id: str,
    indexed: dict[tuple[str, str], dict[str, Any]],
) -> tuple[str, ...]:
    if condition.label == "E":
        return _production_evidence(indexed, query_id)
    try:
        record = indexed[(condition.configuration_id, query_id)]
    except KeyError as error:
        raise StageBPlanError(
            f"missing Stage-A observation for {condition.label}/{query_id}"
        ) from error
    evidence = record.get("retrieved_evidence_ids")
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) and item for item in evidence
    ):
        raise StageBPlanError("retrieved_evidence_ids is invalid")
    return tuple(dict.fromkeys(evidence))


def build_stage_b_execution_plan(
    *,
    selection_path: Path,
    stage_a_observations_path: Path,
    cases: StageBCaseManifest,
    model_id: str,
) -> StageBExecutionPlan:
    """Build all hashes and equivalence classes without evaluating a model."""

    if not isinstance(cases, StageBCaseManifest):
        raise TypeError("cases must be StageBCaseManifest")
    if not isinstance(model_id, str) or not model_id:
        raise StageBPlanError("model_id must be non-empty")
    conditions = _load_conditions(selection_path)
    indexed = _load_stage_a_observations(stage_a_observations_path)
    drafts: list[dict[str, object]] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    for condition in conditions:
        for frozen_case in cases.cases:
            case = frozen_case.downstream_case
            retrieved = _retrieved_evidence(condition, frozen_case.query_id, indexed)
            semantic_evidence = selected_semantic_evidence(case, retrieved)
            observation_id = f"{condition.label}:{frozen_case.query_id}"
            if semantic_evidence is None:
                semantic_hash = None
                selected_ids: tuple[str, ...] = ()
            else:
                request = build_semantic_request(
                    mandate=case.scenario.mandate,
                    transaction=case.scenario.transaction,
                    catalog_snapshot=case.scenario.catalog_snapshot,
                    semantic_evidence=semantic_evidence,
                    model_id=model_id,
                    prompt_version=SEMANTIC_PROMPT_VERSION,
                    detector_version=SEMANTIC_DETECTOR_VERSION,
                )
                semantic_hash = semantic_input_sha256(request)
                selected_ids = tuple(
                    item.evidence_id for item in request.selected_evidence
                )
                hashes[semantic_hash].append(observation_id)
            drafts.append(
                {
                    "observation_id": observation_id,
                    "condition": condition,
                    "case": frozen_case,
                    "retrieved": retrieved,
                    "selected": selected_ids,
                    "semantic_hash": semantic_hash,
                }
            )
    canonical_by_hash = {key: members[0] for key, members in hashes.items()}
    observations: list[PlannedStageBObservation] = []
    for draft in drafts:
        condition = draft["condition"]
        frozen_case = draft["case"]
        assert isinstance(condition, StageBCondition)
        semantic_hash = draft["semantic_hash"]
        if semantic_hash is None:
            execution = "NOT_EVALUATED"
            planned_call = False
            canonical = None
            semantic_status = "NOT_EVALUATED"
        else:
            assert isinstance(semantic_hash, str)
            canonical = canonical_by_hash[semantic_hash]
            planned_call = draft["observation_id"] == canonical
            execution = (
                "EXECUTED_PLANNED"
                if planned_call
                else "REUSED_IDENTICAL_INPUT_PLANNED"
            )
            semantic_status = "PLANNED"
        observations.append(
            PlannedStageBObservation(
                observation_id=draft["observation_id"],
                condition_label=condition.label,
                condition_role=condition.role,
                configuration_id=condition.configuration_id,
                strategy=condition.strategy,
                alpha=condition.alpha,
                top_k=condition.top_k,
                query_id=frozen_case.query_id,
                engineering_expectation=(
                    frozen_case.engineering_expectation.value
                ),
                expected_final_action=frozen_case.expected_final_action,
                retrieved_evidence_ids=draft["retrieved"],
                selected_trusted_evidence_ids=draft["selected"],
                semantic_input_sha256=semantic_hash,
                semantic_status=semantic_status,
                planned_semantic_call=planned_call,
                semantic_execution=execution,
                canonical_observation_id=canonical,
            )
        )
    equivalence = tuple(
        StageBEquivalenceClass(
            semantic_input_sha256=semantic_hash,
            canonical_observation_id=members[0],
            member_observation_ids=tuple(members),
        )
        for semantic_hash, members in sorted(hashes.items())
    )
    unique_count = len(equivalence)
    nonempty_count = sum(
        item.semantic_input_sha256 is not None for item in observations
    )
    insufficient_count = len(observations) - nonempty_count
    return StageBExecutionPlan(
        model_id=model_id,
        prompt_version=SEMANTIC_PROMPT_VERSION,
        detector_version=SEMANTIC_DETECTOR_VERSION,
        observations=tuple(observations),
        equivalence_classes=equivalence,
        nominal_observation_count=len(observations),
        unique_semantic_input_count=unique_count,
        predicted_semantic_api_calls=unique_count,
        duplicate_reused_observation_count=nonempty_count - unique_count,
        evidence_insufficient_no_call_count=insufficient_count,
    )


def stage_b_execution_plan_record(plan: StageBExecutionPlan) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "experiment_stage": "INT-2 Stage B planning only",
        "model_id": plan.model_id,
        "prompt_version": plan.prompt_version,
        "detector_version": plan.detector_version,
        "nominal_observation_count": plan.nominal_observation_count,
        "unique_semantic_input_count": plan.unique_semantic_input_count,
        "predicted_semantic_api_calls": plan.predicted_semantic_api_calls,
        "duplicate_reused_observation_count": (
            plan.duplicate_reused_observation_count
        ),
        "evidence_insufficient_no_call_count": (
            plan.evidence_insufficient_no_call_count
        ),
        "observations": [
            {
                "observation_id": item.observation_id,
                "condition_label": item.condition_label,
                "condition_role": item.condition_role,
                "configuration_id": item.configuration_id,
                "strategy": item.strategy,
                "alpha": item.alpha,
                "top_k": item.top_k,
                "query_id": item.query_id,
                "engineering_expectation": item.engineering_expectation,
                "expected_final_action": item.expected_final_action,
                "retrieved_evidence_ids": list(item.retrieved_evidence_ids),
                "selected_trusted_evidence_ids": list(
                    item.selected_trusted_evidence_ids
                ),
                "semantic_input_sha256": item.semantic_input_sha256,
                "semantic_status": item.semantic_status,
                "planned_semantic_call": item.planned_semantic_call,
                "semantic_execution": item.semantic_execution,
                "canonical_observation_id": item.canonical_observation_id,
            }
            for item in plan.observations
        ],
        "equivalence_classes": [
            {
                "semantic_input_sha256": item.semantic_input_sha256,
                "canonical_observation_id": item.canonical_observation_id,
                "member_observation_ids": list(item.member_observation_ids),
            }
            for item in plan.equivalence_classes
        ],
    }


def write_stage_b_execution_plan(plan: StageBExecutionPlan, output_path: Path) -> Path:
    """Exclusively create a future plan artifact; this function makes no API call."""

    if not isinstance(plan, StageBExecutionPlan) or not isinstance(output_path, Path):
        raise TypeError("plan and output_path have invalid types")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            stage_b_execution_plan_record(plan),
            stream,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        stream.write("\n")
    return output_path
