from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import socket
from typing import Callable

import pytest

from mandateguard.engineering.int2.stage_b_cases import (
    EXPECTED_STAGE_B_QUERY_IDS,
    StageBCaseManifestError,
    canonical_stage_b_manifest_sha256,
    deterministic_action,
    load_stage_b_case_manifest,
    manifest_preview_record,
)
from mandateguard.engineering.int2.stage_b_plan import (
    build_stage_b_execution_plan,
)
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.models.decision import DecisionAction


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
STAGE_B_CASES = INT2_FIXTURES / "stage_b_cases.json"
MANIFEST_SHA256 = "b2d4857750b98a1f3629f63c9d294f353fb734b67ab2bcbec8e8f3a057fc6454"


def _store() -> TrustedCommerceStore:
    return TrustedCommerceStore.from_files(
        catalog_path=COMMERCE_FIXTURES / "merchant_catalog.json",
        merchant_terms_path=COMMERCE_FIXTURES / "merchant_terms.json",
    )


def _load_manifest(path: Path = STAGE_B_CASES):
    return load_stage_b_case_manifest(
        path,
        query_corpus_path=INT2_FIXTURES / "retrieval_queries.json",
        store=_store(),
    )


@pytest.fixture(scope="module")
def manifest():
    return _load_manifest()


def _mutated_manifest(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> Path:
    decoded = json.loads(STAGE_B_CASES.read_text(encoding="utf-8"))
    mutation(decoded)
    decoded["manifest_sha256"] = canonical_stage_b_manifest_sha256(decoded)
    path = tmp_path / "mutated_stage_b_cases.json"
    path.write_text(
        json.dumps(decoded, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_manifest_freezes_exact_query_set_and_natural_expectation_distribution(
    manifest,
):
    assert len(manifest.cases) == 6
    assert tuple(case.query_id for case in manifest.cases) == EXPECTED_STAGE_B_QUERY_IDS
    assert Counter(case.engineering_expectation.value for case in manifest.cases) == {
        "PASS": 3,
        "VIOLATION": 2,
        "ABSTAIN": 1,
    }
    assert {
        case.engineering_expectation.value: case.expected_final_action
        for case in manifest.cases
    } == {"PASS": "ALLOW", "VIOLATION": "BLOCK", "ABSTAIN": "REVIEW"}


def test_manifest_canonical_hash_is_stable_and_commits_all_non_hash_fields(manifest):
    decoded = json.loads(STAGE_B_CASES.read_text(encoding="utf-8"))
    assert manifest.manifest_sha256 == MANIFEST_SHA256
    assert canonical_stage_b_manifest_sha256(decoded) == MANIFEST_SHA256
    assert _load_manifest().manifest_sha256 == MANIFEST_SHA256


def test_every_replay_scenario_is_repeatably_deterministic_tier_ab_allow(manifest):
    first = tuple(
        deterministic_action(case.downstream_case) for case in manifest.cases
    )
    second = tuple(
        deterministic_action(case.downstream_case) for case in _load_manifest().cases
    )
    assert first == second == (DecisionAction.ALLOW,) * 6


def test_eligible_evidence_and_expectation_provenance_are_source_bound(manifest):
    store = _store()
    known = {entry.evidence_id: entry for entry in store.evidence_entries}
    for case in manifest.cases:
        transaction = case.scenario.transaction.payload
        merchant_id = transaction.merchant_id
        sku = transaction.lines[0].sku
        assert case.eligible_evidence_ids
        for evidence_id in case.eligible_evidence_ids:
            evidence = known[evidence_id]
            assert evidence.merchant_id == merchant_id
            if evidence.sku is not None:
                assert store.get_product(
                    merchant_id=merchant_id, sku=evidence.sku
                ).merchant_id == merchant_id
        product_source_ids = {
            item.evidence_id
            for item in store.evidence_for_product(merchant_id=merchant_id, sku=sku)
        }
        assert set(case.provenance.merchant_evidence_ids) == product_source_ids
        assert case.provenance.catalog_product_id == f"{merchant_id}/{sku}"
        assert case.provenance.source_paths == (
            "fixtures/agentic_commerce/merchant_catalog.json",
            "fixtures/agentic_commerce/merchant_terms.json",
        )
        assert case.expectation_reason


def test_manifest_contains_no_stage_a_result_or_ranking_fields():
    decoded = json.loads(STAGE_B_CASES.read_text(encoding="utf-8"))
    serialized = json.dumps(decoded, sort_keys=True)
    for forbidden in (
        "recall_at_k",
        "precision_at_k",
        "reciprocal_rank",
        "mrr",
        "retrieved_evidence_ids",
        "retrieval_strategy",
        "top_k",
        "alpha",
    ):
        assert f'"{forbidden}"' not in serialized


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        (lambda root: root["cases"].pop(), "six"),
        (
            lambda root: root["cases"][-1].__setitem__(
                "query_id", root["cases"][0]["query_id"]
            ),
            "query|merchant|SKU",
        ),
        (
            lambda root: root["cases"][0].__setitem__("query_id", "INT2-Q-UNKNOWN"),
            "query|merchant|SKU",
        ),
        (
            lambda root: root["cases"][0].__setitem__(
                "engineering_expectation", "UNKNOWN"
            ),
            "unsupported",
        ),
        (
            lambda root: root["cases"][0]["replay_scenario"].pop("nonce_state"),
            "unexpected or missing fields",
        ),
        (
            lambda root: root["cases"][0]["replay_scenario"]["mandate"][
                "payload"
            ]["constraints"]["hard"].__setitem__("max_total_minor", 0),
            "deterministic Tier A/B ALLOW",
        ),
        (
            lambda root: root["cases"][0]["eligible_evidence_ids"].append(
                "unknown-evidence-v1"
            ),
            "unknown eligible evidence",
        ),
        (
            lambda root: root["cases"][0]["eligible_evidence_ids"].__setitem__(
                0, "academy-terms-v1"
            ),
            "merchant|retrieval corpus",
        ),
        (
            lambda root: root["cases"][0].__setitem__("unexpected", True),
            "unexpected or missing fields",
        ),
        (
            lambda root: root["cases"][0]["replay_scenario"]["mandate"][
                "metadata"
            ].__setitem__("unexpected", True),
            "unexpected or missing fields",
        ),
        (
            lambda root: root["cases"][0].__setitem__("eligible_evidence_ids", []),
            "string array",
        ),
    ],
)
def test_strict_validator_rejects_invalid_or_source_drifting_cases(
    tmp_path, mutation, error_match
):
    path = _mutated_manifest(tmp_path, mutation)
    with pytest.raises(StageBCaseManifestError, match=error_match):
        _load_manifest(path)


def test_validator_explicitly_rejects_stage_a_metrics_in_case_data(tmp_path):
    def add_stage_a_result(root):
        root["cases"][0]["mean_recall_at_k"] = 1

    path = _mutated_manifest(tmp_path, add_stage_a_result)
    with pytest.raises(StageBCaseManifestError, match="forbidden Stage-A"):
        _load_manifest(path)


def test_preview_is_offline_and_reports_all_required_case_fields(manifest, monkeypatch):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden in Stage-B preview/tests")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    records = manifest_preview_record(manifest)
    assert tuple(item["query_id"] for item in records) == EXPECTED_STAGE_B_QUERY_IDS
    assert all(item["deterministic_action"] == "ALLOW" for item in records)
    assert all(item["eligible_evidence_count"] > 0 for item in records)
    assert {item["engineering_expectation"] for item in records} == {
        "PASS",
        "VIOLATION",
        "ABSTAIN",
    }


def test_all_36_observations_plan_offline_and_deduplicate_only_by_exact_hash(
    manifest, monkeypatch
):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden in Stage-B planning/tests")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    plan = build_stage_b_execution_plan(
        selection_path=STAGE_A_RUN / "stage_b_selection.json",
        stage_a_observations_path=STAGE_A_RUN / "retrieval_observations.jsonl",
        cases=manifest,
        model_id="gpt-5.6-terra",
    )
    assert plan.nominal_observation_count == len(plan.observations) == 36
    assert plan.evidence_insufficient_no_call_count == 6
    assert plan.unique_semantic_input_count == plan.predicted_semantic_api_calls == 15
    assert plan.duplicate_reused_observation_count == 15

    empty = [item for item in plan.observations if not item.retrieved_evidence_ids]
    assert len(empty) == 6
    assert all(item.semantic_input_sha256 is None for item in empty)
    assert all(item.semantic_status == "NOT_EVALUATED" for item in empty)
    assert all(item.planned_semantic_call is False for item in empty)
    assert all(item.selected_trusted_evidence_ids == () for item in empty)

    evidence_bearing = [item for item in plan.observations if item.retrieved_evidence_ids]
    assert len(evidence_bearing) == 30
    assert all(len(item.semantic_input_sha256 or "") == 64 for item in evidence_bearing)
    assert all(item.selected_trusted_evidence_ids for item in evidence_bearing)

    grouped: dict[str, list[str]] = defaultdict(list)
    for item in evidence_bearing:
        grouped[item.semantic_input_sha256].append(item.observation_id)
    assert set(grouped) == {
        item.semantic_input_sha256 for item in plan.equivalence_classes
    }
    for equivalence_class in plan.equivalence_classes:
        members = tuple(grouped[equivalence_class.semantic_input_sha256])
        assert equivalence_class.member_observation_ids == members
        assert equivalence_class.canonical_observation_id == members[0]
        planned = [
            item
            for item in evidence_bearing
            if item.semantic_input_sha256 == equivalence_class.semantic_input_sha256
            and item.planned_semantic_call
        ]
        assert [item.observation_id for item in planned] == [members[0]]

    by_id = {item.observation_id: item for item in plan.observations}
    for query_id in EXPECTED_STAGE_B_QUERY_IDS:
        assert (
            by_id[f"C:{query_id}"].semantic_input_sha256
            == by_id[f"D:{query_id}"].semantic_input_sha256
        )
