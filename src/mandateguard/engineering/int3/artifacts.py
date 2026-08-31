"""Strict, network-free INT-3A plan artifact construction.

Only ``subset_plan.jsonl`` is writable in this milestone.  Live subset result
and labeled CSV writers intentionally do not live here because no subset has
been semantically executed yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from mandateguard.engineering.int2.models import RelevanceManifest
from mandateguard.engineering.int2.stage_b_cases import StageBCaseManifest
from mandateguard.engineering.int3.dataset import (
    SufficiencyDataset,
    SufficiencyDatasetRow,
    build_dataset,
)
from mandateguard.engineering.int3.features import (
    FEATURE_NAMES,
    RetrievalScoreSurface,
    extract_subset_features,
)
from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    SubsetObservation,
    SubsetPlan,
)
from mandateguard.engineering.int3.subsets import build_subset_feature_input


SUBSET_PLAN_FILENAME = "subset_plan.jsonl"
FUTURE_SUBSET_RESULTS_FILENAME = "subset_results.jsonl"
FUTURE_SUFFICIENCY_DATASET_FILENAME = "sufficiency_dataset.csv"


def _created_at(value: SubsetPlan) -> str:
    return value.created_at.isoformat().replace("+00:00", "Z")


def _features_for_observation(
    observation: SubsetObservation,
    *,
    cases: StageBCaseManifest,
    relevance: RelevanceManifest,
    score_surface: RetrievalScoreSurface | None,
) -> Mapping[str, float]:
    case = cases.for_query(observation.query_id)
    return extract_subset_features(
        build_subset_feature_input(
            case,
            observation.subset_evidence_ids,
            relevance=relevance,
            score_surface=score_surface,
        )
    )


def build_unlabeled_sufficiency_dataset(
    *,
    plan: SubsetPlan,
    cases: StageBCaseManifest,
    relevance: RelevanceManifest,
    score_surface: RetrievalScoreSurface | None,
) -> SufficiencyDataset:
    """Build the strict feature dataset with every target left null."""

    if not isinstance(plan, SubsetPlan):
        raise TypeError("plan must be SubsetPlan")
    if not isinstance(cases, StageBCaseManifest):
        raise TypeError("cases must be StageBCaseManifest")
    if not isinstance(relevance, RelevanceManifest):
        raise TypeError("relevance must be RelevanceManifest")
    if tuple(case.query_id for case in cases.cases) != plan.query_ids:
        raise Int3ExperimentError("plan and cases must cover the same queries in order")
    rows = []
    for observation in plan.observations:
        features = _features_for_observation(
            observation,
            cases=cases,
            relevance=relevance,
            score_surface=score_surface,
        )
        rows.append(
            SufficiencyDatasetRow(
                observation_id=observation.observation_id,
                query_id=observation.query_id,
                subset_mask=observation.subset_mask,
                subset_size=observation.subset_size,
                eligible_size=observation.eligible_size,
                subset_evidence_ids=observation.subset_evidence_ids,
                features=features,
                decision_stable=None,
            )
        )
    return build_dataset(rows)


def subset_plan_record(
    *,
    plan: SubsetPlan,
    observation: SubsetObservation,
    features: Mapping[str, float],
) -> dict[str, object]:
    """Serialize one planned subset with explicit null future result fields."""

    if not isinstance(plan, SubsetPlan):
        raise TypeError("plan must be SubsetPlan")
    if not isinstance(observation, SubsetObservation):
        raise TypeError("observation must be SubsetObservation")
    if frozenset(features) != frozenset(FEATURE_NAMES):
        raise Int3ExperimentError("features must cover exactly FEATURE_NAMES")
    reference = plan.reference_for_query(observation.query_id)
    return {
        "schema_version": plan.schema_version,
        "plan_created_at": _created_at(plan),
        "observation_id": observation.observation_id,
        "query_id": observation.query_id,
        "eligible_evidence_ids": list(observation.eligible_evidence_ids),
        "subset_evidence_ids": list(observation.subset_evidence_ids),
        "subset_size": observation.subset_size,
        "eligible_size": observation.eligible_size,
        "subset_mask": observation.subset_mask,
        "case_family": observation.case_family.value,
        "full_reference_semantic_behavior": (
            observation.full_reference_semantic_behavior
        ),
        "full_reference_action": observation.full_reference_action,
        "full_reference_semantic_input_sha256": (
            observation.full_reference_semantic_input_sha256
        ),
        "subset_semantic_input_sha256": observation.subset_semantic_input_sha256,
        "sku_scoped_selected_evidence_ids": list(
            observation.sku_scoped_selected_evidence_ids
        ),
        "matches_full_reference_semantic_input": (
            observation.matches_full_reference_semantic_input
        ),
        "is_full_evidence_subset": observation.is_full_evidence_subset,
        "canonical_observation_id": observation.canonical_observation_id,
        "planned_semantic_call": observation.planned_semantic_call,
        "semantic_status": observation.semantic_status,
        "future_subset_observed_semantic_behavior": (
            observation.observed_semantic_behavior
        ),
        "future_subset_observed_final_action": observation.observed_final_action,
        "decision_stable": observation.decision_stable,
        "features": {name: float(features[name]) for name in FEATURE_NAMES},
        "reference_provenance": {
            "source_run_id": reference.source_run_id,
            "source_observation_id": reference.source_observation_id,
            "model_id": reference.model_id,
            "prompt_version": reference.prompt_version,
            "detector_version": reference.detector_version,
        },
        "external_calls": {
            "semantic_provider_calls": 0,
            "evidence_fetch_calls": 0,
            "razorpay_calls": 0,
        },
    }


def write_subset_plan_jsonl(
    *,
    plan: SubsetPlan,
    cases: StageBCaseManifest,
    relevance: RelevanceManifest,
    score_surface: RetrievalScoreSurface | None,
    output_path: Path,
) -> Path:
    """Exclusively create the sole INT-3A artifact: ``subset_plan.jsonl``."""

    if not isinstance(output_path, Path):
        raise TypeError("output_path must be pathlib.Path")
    dataset = build_unlabeled_sufficiency_dataset(
        plan=plan,
        cases=cases,
        relevance=relevance,
        score_surface=score_surface,
    )
    if not dataset.unlabeled_rows or dataset.labeled_rows:
        raise Int3ExperimentError("INT-3A plan rows must all be unlabeled")
    features_by_id = {
        row.observation_id: row.features for row in dataset.rows
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        for observation in plan.observations:
            record = subset_plan_record(
                plan=plan,
                observation=observation,
                features=features_by_id[observation.observation_id],
            )
            stream.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return output_path
