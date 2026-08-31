from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from mandateguard.engineering.int2.fixtures import load_relevance_manifest
from mandateguard.engineering.int2.stage_b_cases import (
    EXPECTED_STAGE_B_QUERY_IDS,
    load_stage_b_case_manifest,
)
from mandateguard.engineering.int3.artifacts import (
    FUTURE_SUBSET_RESULTS_FILENAME,
    FUTURE_SUFFICIENCY_DATASET_FILENAME,
    build_unlabeled_sufficiency_dataset,
    write_subset_plan_jsonl,
)
from mandateguard.engineering.int3.features import (
    FEATURE_NAMES,
    FORBIDDEN_TARGET_FIELDS,
    SubsetFeatureInput,
    assert_no_target_leakage,
)
from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    subset_counts_by_query,
)
from mandateguard.engineering.int3.splits import (
    assert_no_query_leakage,
    leave_one_query_out_folds,
)
from mandateguard.engineering.int3.subsets import (
    build_subset_plan,
    enumerate_nonempty_subsets,
    load_full_evidence_references,
    load_reference_score_surface,
    subset_mask,
    subset_observation_id,
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
EXPECTED_COUNTS = {
    "INT2-Q-STUDYGLOW": 15,
    "INT2-Q-NOTEBOOK": 15,
    "INT2-Q-STUDY-CLUB": 15,
    "INT2-Q-MARKET-EDGE": 7,
    "INT2-Q-TAX-GUIDE": 7,
    "INT2-Q-FLEXI": 3,
}


def _store() -> TrustedCommerceStore:
    return TrustedCommerceStore.from_files(
        catalog_path=COMMERCE_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=COMMERCE_FIXTURES / "merchant_terms.json",
    )


def _inputs(*, created_at: datetime | None = None) -> SimpleNamespace:
    cases = load_stage_b_case_manifest(
        INT2_FIXTURES / "stage_b_cases.json",
        query_corpus_path=INT2_FIXTURES / "retrieval_queries.json",
        store=_store(),
    )
    relevance = load_relevance_manifest(INT2_FIXTURES / "relevance_manifest.json")
    references = load_full_evidence_references(
        STAGE_B_RUN / "stage_b_observations.jsonl",
        cases=cases,
    )
    score_surface = load_reference_score_surface(
        STAGE_A_RUN / "retrieval_observations.jsonl"
    )
    plan = build_subset_plan(
        cases=cases,
        references=references,
        created_at=created_at or datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    dataset = build_unlabeled_sufficiency_dataset(
        plan=plan,
        cases=cases,
        relevance=relevance,
        score_surface=score_surface,
    )
    return SimpleNamespace(
        cases=cases,
        relevance=relevance,
        references=references,
        score_surface=score_surface,
        plan=plan,
        dataset=dataset,
    )


@pytest.fixture(scope="module")
def int3_inputs():
    return _inputs()


def test_enumerates_exactly_every_nonempty_subset(int3_inputs):
    plan = int3_inputs.plan
    assert plan.observation_count == 62
    assert subset_counts_by_query(plan) == EXPECTED_COUNTS
    assert all(observation.subset_evidence_ids for observation in plan.observations)
    for case in int3_inputs.cases.cases:
        expected = enumerate_nonempty_subsets(case.eligible_evidence_ids)
        observed = tuple(
            item.subset_evidence_ids
            for item in plan.observations_for_query(case.query_id)
        )
        assert observed == expected
        assert len(observed) == 2 ** len(case.eligible_evidence_ids) - 1


def test_plan_has_exact_frozen_query_coverage(int3_inputs):
    assert int3_inputs.plan.query_ids == EXPECTED_STAGE_B_QUERY_IDS
    assert {
        item.query_id for item in int3_inputs.plan.observations
    } == set(EXPECTED_STAGE_B_QUERY_IDS)
    assert {
        item.query_id for item in int3_inputs.references
    } == set(EXPECTED_STAGE_B_QUERY_IDS)


def test_subset_masks_and_observation_ids_are_stable(int3_inputs):
    first = int3_inputs.plan
    second = _inputs(
        created_at=datetime(2030, 1, 1, tzinfo=timezone.utc)
    ).plan
    assert tuple(item.observation_id for item in first.observations) == tuple(
        item.observation_id for item in second.observations
    )
    assert tuple(item.subset_semantic_input_sha256 for item in first.observations) == tuple(
        item.subset_semantic_input_sha256 for item in second.observations
    )
    for observation in first.observations:
        mask = subset_mask(
            eligible_evidence_ids=observation.eligible_evidence_ids,
            subset_evidence_ids=observation.subset_evidence_ids,
        )
        assert observation.subset_mask == mask
        assert observation.observation_id == subset_observation_id(
            query_id=observation.query_id,
            mask=mask,
        )


def test_full_subset_reproduces_each_frozen_production_reference(int3_inputs):
    for reference in int3_inputs.references:
        full = next(
            item
            for item in int3_inputs.plan.observations_for_query(reference.query_id)
            if item.is_full_evidence_subset
        )
        assert full.subset_evidence_ids == reference.full_evidence_ids
        assert full.subset_semantic_input_sha256 == (
            reference.full_reference_semantic_input_sha256
        )
        assert full.full_reference_action == reference.full_reference_action
        assert full.full_reference_semantic_behavior == (
            reference.full_reference_semantic_behavior
        )
        assert full.matches_full_reference_semantic_input is True


def test_plan_contains_only_null_future_subset_results(int3_inputs):
    assert all(item.semantic_status == "PLANNED" for item in int3_inputs.plan.observations)
    assert all(
        item.observed_semantic_behavior is None
        and item.observed_final_action is None
        and item.decision_stable is None
        for item in int3_inputs.plan.observations
    )


def test_plan_build_does_not_mutate_frozen_cases_or_evidence():
    frozen_paths = (
        INT2_FIXTURES / "stage_b_cases.json",
        INT2_FIXTURES / "retrieval_queries.json",
        INT2_FIXTURES / "relevance_manifest.json",
        COMMERCE_FIXTURES / "merchant_catalog.json",
        COMMERCE_FIXTURES / "merchant_terms.json",
        STAGE_B_RUN / "stage_b_observations.jsonl",
    )
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen_paths
    }
    _inputs()
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen_paths
    }
    assert after == before


def test_feature_dataset_is_strict_finite_and_unlabeled(int3_inputs):
    dataset = int3_inputs.dataset
    assert len(dataset.rows) == 62
    assert dataset.feature_names == FEATURE_NAMES
    assert dataset.is_fully_labeled is False
    assert len(dataset.unlabeled_rows) == 62
    assert dataset.labeled_rows == ()
    for row in dataset.rows:
        assert tuple(row.features) == FEATURE_NAMES
        assert all(math.isfinite(value) for value in row.vector)
        assert row.features["evidence_count"] == row.subset_size
        assert row.features["evidence_fraction"] == pytest.approx(
            row.subset_size / row.eligible_size
        )
        assert row.features["max_score"] == row.features["hybrid_max_score"]
        assert row.features["mean_score"] == row.features["hybrid_mean_score"]
        assert row.features["min_score"] == row.features["hybrid_min_score"]
        assert row.features["score_margin"] == row.features["hybrid_score_margin"]
        assert row.features["retrieval_scores_available"] == 1.0


def test_feature_extraction_covers_annotations_scores_sources_and_case_family(
    int3_inputs,
):
    full_rows = [
        row for row in int3_inputs.dataset.rows if row.subset_size == row.eligible_size
    ]
    assert len(full_rows) == 6
    for row in full_rows:
        features = row.features
        assert features["evidence_fraction"] == 1.0
        assert features["required_annotation_fraction"] == 1.0
        assert features["relevant_annotation_fraction"] == 1.0
        assert features["source_kind_count"] >= 1.0
        assert 0.0 < features["source_kind_diversity"] <= 1.0
        assert 0.0 <= features["lexical_min_score"] <= features["lexical_max_score"]
        assert 0.0 <= features["semantic_min_score"] <= features["semantic_max_score"]
        assert 0.0 <= features["hybrid_min_score"] <= features["hybrid_max_score"]
        one_hot = sum(
            features[name]
            for name in (
                "case_family_purpose_and_exclusion",
                "case_family_exclusion_only",
                "case_family_purpose_only",
                "case_family_other",
            )
        )
        assert one_hot == 1.0


def test_feature_boundary_mechanically_excludes_targets_and_reference_results():
    feature_input_fields = {item.name for item in fields(SubsetFeatureInput)}
    assert not (feature_input_fields & FORBIDDEN_TARGET_FIELDS)
    assert not (set(FEATURE_NAMES) & FORBIDDEN_TARGET_FIELDS)
    for forbidden in (
        "decision_stable",
        "subset_semantic_verdict",
        "final_action",
        "engineering_expectation",
        "full_reference_action",
    ):
        with pytest.raises(Int3ExperimentError, match="post-inference|resembles"):
            assert_no_target_leakage(("evidence_count", forbidden))


def test_leave_one_query_out_has_six_isolated_complete_test_sets(int3_inputs):
    folds = leave_one_query_out_folds(int3_inputs.dataset)
    assert len(folds) == 6
    assert_no_query_leakage(int3_inputs.dataset, folds)
    for fold in folds:
        test_rows = fold.test_rows(int3_inputs.dataset)
        train_rows = fold.train_rows(int3_inputs.dataset)
        assert {row.query_id for row in test_rows} == {fold.held_out_query_id}
        assert fold.held_out_query_id not in {row.query_id for row in train_rows}
        assert len(test_rows) == EXPECTED_COUNTS[fold.held_out_query_id]
        assert len(train_rows) + len(test_rows) == 62


def test_subset_plan_artifact_contains_62_plan_rows_and_no_result_artifacts(
    int3_inputs, tmp_path
):
    output = tmp_path / "int3" / "subset_plan.jsonl"
    write_subset_plan_jsonl(
        plan=int3_inputs.plan,
        cases=int3_inputs.cases,
        relevance=int3_inputs.relevance,
        score_surface=int3_inputs.score_surface,
        output_path=output,
    )
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 62
    assert not (output.parent / FUTURE_SUBSET_RESULTS_FILENAME).exists()
    assert not (output.parent / FUTURE_SUFFICIENCY_DATASET_FILENAME).exists()
    for record in records:
        assert record["subset_evidence_ids"]
        assert len(record["subset_semantic_input_sha256"]) == 64
        assert record["future_subset_observed_semantic_behavior"] is None
        assert record["future_subset_observed_final_action"] is None
        assert record["decision_stable"] is None
        assert tuple(record["features"]) == tuple(sorted(FEATURE_NAMES))
        assert record["external_calls"] == {
            "evidence_fetch_calls": 0,
            "razorpay_calls": 0,
            "semantic_provider_calls": 0,
        }


def test_plan_artifact_generation_makes_zero_network_calls(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("INT-3A planning must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    values = _inputs()
    write_subset_plan_jsonl(
        plan=values.plan,
        cases=values.cases,
        relevance=values.relevance,
        score_surface=values.score_surface,
        output_path=tmp_path / "subset_plan.jsonl",
    )
