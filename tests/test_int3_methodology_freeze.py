from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from mandateguard.core.hashing import sha256_canonical
from mandateguard.engineering.int2.fixtures import load_relevance_manifest
from mandateguard.engineering.int2.stage_b_cases import load_stage_b_case_manifest
from mandateguard.engineering.int3.artifacts import (
    build_unlabeled_sufficiency_dataset,
    write_model_feature_manifest,
)
from mandateguard.engineering.int3.features import FEATURE_NAMES
from mandateguard.engineering.int3.live_plan import (
    FROZEN_LIVE_EXECUTION_PLAN_SHA256,
    LIVE_EXECUTION_PLAN_BASE_COMMIT,
    NEW_LIVE_EXECUTION_REQUIRED,
    PRIOR_EXACT_RESULT,
    PRIOR_RESULTS_FILE_SHA256,
    PRIOR_RESULTS_SOURCE_COMMIT,
    PRIOR_RESULTS_SOURCE_PATH,
    build_live_execution_plan,
    live_execution_plan_record,
    load_prior_exact_results,
    write_live_execution_plan,
)
from mandateguard.engineering.int3.model import SufficiencyModel
from mandateguard.engineering.int3.model_manifest import (
    DIAGNOSTIC_ONLY_FEATURE_NAMES,
    MODEL_FEATURE_MANIFEST_BASE_COMMIT,
    MODEL_FEATURE_MANIFEST_SHA256,
    MODEL_FEATURE_NAMES,
    MODEL_PIPELINE_SPEC,
    ORACLE_DIAGNOSTIC_ONLY_FEATURE_NAMES,
    model_feature_manifest_payload,
)
from mandateguard.engineering.int3.models import Int3ExperimentError
from mandateguard.engineering.int3.splits import (
    assert_no_query_leakage,
    leave_one_query_out_folds,
)
from mandateguard.engineering.int3.subsets import (
    build_subset_plan,
    load_full_evidence_references,
    load_reference_score_surface,
)
from mandateguard.intelligence.store import TrustedCommerceStore


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INT2_FIXTURES = REPOSITORY_ROOT / "fixtures" / "engineering" / "int2"
COMMERCE_FIXTURES = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"
STAGE_A_RUN = (
    REPOSITORY_ROOT
    / "artifacts"
    / "engineering"
    / "int2"
    / "stage-a-live-20260830T113054Z-1a94a4a"
)
STAGE_B_RUN = (
    REPOSITORY_ROOT
    / "artifacts"
    / "engineering"
    / "int2"
    / "stage-b-live-20260830T123856Z-0e4213c"
)
PRIOR_PATH = STAGE_B_RUN / "stage_b_observations.jsonl"
MODEL_FEATURE_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "artifacts"
    / "engineering"
    / "int3"
    / "model_feature_manifest.json"
)
LIVE_EXECUTION_PLAN_PATH = (
    REPOSITORY_ROOT
    / "artifacts"
    / "engineering"
    / "int3"
    / "subset_live_execution_plan.json"
)


def _methodology() -> SimpleNamespace:
    store = TrustedCommerceStore.from_files(
        catalog_path=COMMERCE_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=COMMERCE_FIXTURES / "merchant_terms.json",
    )
    cases = load_stage_b_case_manifest(
        INT2_FIXTURES / "stage_b_cases.json",
        query_corpus_path=INT2_FIXTURES / "retrieval_queries.json",
        store=store,
    )
    references = load_full_evidence_references(PRIOR_PATH, cases=cases)
    created_at = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)
    subset_plan = build_subset_plan(
        cases=cases,
        references=references,
        created_at=created_at,
    )
    prior = load_prior_exact_results(PRIOR_PATH)
    live_plan = build_live_execution_plan(
        subset_plan=subset_plan,
        prior_results=prior,
        created_at=created_at,
    )
    return SimpleNamespace(
        store=store,
        cases=cases,
        subset_plan=subset_plan,
        prior=prior,
        live_plan=live_plan,
    )


@pytest.fixture(scope="module")
def methodology():
    return _methodology()


def test_model_feature_manifest_excludes_oracles_identity_and_outcomes():
    assert len(FEATURE_NAMES) == 36
    assert len(MODEL_FEATURE_NAMES) == 14
    assert ORACLE_DIAGNOSTIC_ONLY_FEATURE_NAMES == (
        "required_annotation_fraction",
        "relevant_annotation_fraction",
    )
    assert not set(ORACLE_DIAGNOSTIC_ONLY_FEATURE_NAMES) & set(MODEL_FEATURE_NAMES)
    assert set(MODEL_FEATURE_NAMES) | set(DIAGNOSTIC_ONLY_FEATURE_NAMES) == set(
        FEATURE_NAMES
    )
    serialized = " ".join(MODEL_FEATURE_NAMES).lower()
    for forbidden in (
        "query_id",
        "case_id",
        "expected",
        "full_reference",
        "observed",
        "decision_stable",
        "relevant_annotation",
        "required_annotation",
    ):
        assert forbidden not in serialized


def test_every_model_feature_is_preregistered_runtime_pre_semantic():
    payload = model_feature_manifest_payload()
    entries = payload["ordered_model_features"]
    assert tuple(item["position"] for item in entries) == tuple(range(14))
    assert tuple(item["name"] for item in entries) == MODEL_FEATURE_NAMES
    assert all(
        item["availability"] == "RUNTIME_PRE_SEMANTIC_INFERENCE"
        for item in entries
    )
    assert all("query" not in item["derivation"].lower() for item in entries)
    assert payload["base_commit"] == MODEL_FEATURE_MANIFEST_BASE_COMMIT


def test_case_family_diagnostics_are_runtime_derived_but_not_model_inputs():
    payload = model_feature_manifest_payload()
    note = payload["case_family_note"]
    assert "runtime mandate" in note
    assert "never query/case identity" in note
    assert not any(name.startswith("case_family_") for name in MODEL_FEATURE_NAMES)
    assert {
        "constraint_family_purpose",
        "constraint_family_exclusion",
    }.issubset(MODEL_FEATURE_NAMES)


def test_model_feature_manifest_order_and_sha_are_stable(tmp_path):
    payload = model_feature_manifest_payload()
    assert sha256_canonical(payload) == MODEL_FEATURE_MANIFEST_SHA256
    output = tmp_path / "model_feature_manifest.json"
    write_model_feature_manifest(output)
    record = json.loads(output.read_text(encoding="utf-8"))
    manifest_sha = record.pop("manifest_sha256")
    assert manifest_sha == MODEL_FEATURE_MANIFEST_SHA256
    assert sha256_canonical(record) == MODEL_FEATURE_MANIFEST_SHA256


def test_checked_in_model_feature_manifest_is_the_frozen_record():
    record = json.loads(MODEL_FEATURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_sha = record.pop("manifest_sha256")
    assert manifest_sha == MODEL_FEATURE_MANIFEST_SHA256
    assert sha256_canonical(record) == MODEL_FEATURE_MANIFEST_SHA256


def test_model_pipeline_is_fixed_standard_scaler_plus_l2_logistic():
    assert MODEL_PIPELINE_SPEC["steps"] == (
        "StandardScaler",
        "LogisticRegression",
    )
    assert dict(MODEL_PIPELINE_SPEC["standard_scaler"]) == {
        "with_mean": True,
        "with_std": True,
    }
    logistic = dict(MODEL_PIPELINE_SPEC["logistic_regression"])
    assert logistic == {
        "regularization": "L2",
        "C": 1,
        "solver": "lbfgs",
        "max_iter": 2000,
        "fit_intercept": True,
        "random_state": 0,
        "class_weight": None,
        "tolerance_decimal": "0.0001",
    }
    assert MODEL_PIPELINE_SPEC["hyperparameter_tuning_after_labels"] is False
    with pytest.raises(Int3ExperimentError, match="preregistered"):
        SufficiencyModel(max_iterations=100)
    with pytest.raises(Int3ExperimentError, match="preregistered"):
        SufficiencyModel(l2_inverse_regularization_strength=2.0)
    with pytest.raises(Int3ExperimentError, match="preregistered"):
        SufficiencyModel(class_weight="balanced")  # type: ignore[arg-type]


def test_dataset_separates_36_diagnostics_from_14_model_features(methodology):
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    scores = load_reference_score_surface(
        STAGE_A_RUN / "retrieval_observations.jsonl"
    )
    dataset = build_unlabeled_sufficiency_dataset(
        plan=methodology.subset_plan,
        cases=methodology.cases,
        relevance=relevance,
        score_surface=scores,
    )
    assert dataset.feature_names == MODEL_FEATURE_NAMES
    assert dataset.model_feature_names == MODEL_FEATURE_NAMES
    assert dataset.diagnostic_feature_names == FEATURE_NAMES
    assert all(len(row) == 14 for row in dataset.model_feature_matrix())
    assert all(len(row) == 36 for row in dataset.diagnostic_feature_matrix())
    assert dataset.feature_matrix() == dataset.model_feature_matrix()


def test_leave_one_query_out_remains_the_only_grouped_evaluation(methodology):
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    scores = load_reference_score_surface(
        STAGE_A_RUN / "retrieval_observations.jsonl"
    )
    dataset = build_unlabeled_sufficiency_dataset(
        plan=methodology.subset_plan,
        cases=methodology.cases,
        relevance=relevance,
        score_surface=scores,
    )
    folds = leave_one_query_out_folds(dataset)
    assert len(folds) == 6
    assert_no_query_leakage(dataset, folds)
    assert sum(len(fold.test_indices) for fold in folds) == 62


def test_prior_artifact_is_byte_bound_to_immutable_source_commit(methodology):
    assert PRIOR_RESULTS_SOURCE_COMMIT == (
        "3946aa50c477881b1b085e35b60c9a411b6c8d64"
    )
    assert PRIOR_RESULTS_SOURCE_PATH.endswith("stage_b_observations.jsonl")
    assert sha256(PRIOR_PATH.read_bytes()).hexdigest() == PRIOR_RESULTS_FILE_SHA256
    assert len(methodology.prior.hashes) == 15


def test_prior_reuse_is_exact_hash_only(methodology):
    matched = methodology.prior.hashes[0]
    changed = ("0" if matched[0] != "0" else "1") + matched[1:]
    assert methodology.prior.for_hash(matched) is not None
    assert methodology.prior.for_hash(changed) is None


def test_live_execution_plan_has_62_unique_inputs_15_reuses_and_47_calls(
    methodology,
):
    plan = methodology.live_plan
    assert plan.nominal_observation_count == 62
    assert plan.unique_semantic_input_count == 62
    assert plan.prior_exact_result_unique_input_count == 15
    assert plan.prior_exact_result_observation_count == 15
    assert plan.new_unique_input_count == 47
    assert plan.predicted_new_semantic_api_calls == 47
    assert sum(
        1 for item in plan.unique_inputs if item.planned_new_semantic_api_call
    ) == 47
    assert all(len(item.member_observation_ids) == 1 for item in plan.unique_inputs)


def test_prior_results_and_provenance_are_populated_only_for_exact_matches(
    methodology,
):
    record = live_execution_plan_record(methodology.live_plan)
    reused = [
        item for item in record["observations"] if item["prior_exact_match"]
    ]
    new = [
        item for item in record["observations"] if not item["prior_exact_match"]
    ]
    assert len(reused) == 15
    assert len(new) == 47
    assert all(item["execution_status"] == PRIOR_EXACT_RESULT for item in reused)
    assert all(item["observed_semantic_result"] is not None for item in reused)
    assert all(item["prior_run_id"] for item in reused)
    assert all(item["prior_observation_id"] for item in reused)
    assert all(
        item["prior_result_provenance"]["source_commit"]
        == PRIOR_RESULTS_SOURCE_COMMIT
        for item in reused
    )
    assert all(
        item["execution_status"] == NEW_LIVE_EXECUTION_REQUIRED for item in new
    )
    assert all(item["observed_semantic_result"] is None for item in new)
    assert all(item["prior_result_provenance"] is None for item in new)
    assert all(item["decision_stable"] is None for item in record["observations"])


def test_no_duplicate_new_live_calls_for_identical_hashes(methodology):
    plan = methodology.live_plan
    for unique in plan.unique_inputs:
        members = [
            item
            for item in plan.observations
            if item.semantic_input_sha256 == unique.semantic_input_sha256
        ]
        planned = [item for item in members if item.planned_new_semantic_api_call]
        if unique.execution_status == NEW_LIVE_EXECUTION_REQUIRED:
            assert len(planned) == 1
            assert planned[0].observation_id == unique.canonical_plan_observation_id
        else:
            assert planned == []


def test_live_execution_plan_canonical_sha_and_base_are_frozen(methodology, tmp_path):
    output = tmp_path / "subset_live_execution_plan.json"
    write_live_execution_plan(methodology.live_plan, output)
    record = json.loads(output.read_text(encoding="utf-8"))
    canonical = record.pop("canonical_sha256")
    assert record["base_commit"] == LIVE_EXECUTION_PLAN_BASE_COMMIT
    assert record["model_feature_manifest_sha256"] == (
        MODEL_FEATURE_MANIFEST_SHA256
    )
    assert sha256_canonical(record) == canonical


def test_checked_in_live_execution_plan_is_the_frozen_record():
    record = json.loads(LIVE_EXECUTION_PLAN_PATH.read_text(encoding="utf-8"))
    canonical = record.pop("canonical_sha256")
    assert canonical == FROZEN_LIVE_EXECUTION_PLAN_SHA256
    assert sha256_canonical(record) == FROZEN_LIVE_EXECUTION_PLAN_SHA256


def test_methodology_freeze_makes_zero_network_calls(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("methodology freeze must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    values = _methodology()
    write_model_feature_manifest(tmp_path / "model_feature_manifest.json")
    write_live_execution_plan(
        values.live_plan,
        tmp_path / "subset_live_execution_plan.json",
    )
