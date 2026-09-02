"""Structural tests for the frozen 20-case Resolve recovery preregistration.

Every test here is deterministic and offline. None of them authorizes a
transaction, acquires evidence, calls a model, or calls a payment provider, so
running the suite cannot produce evaluation outcomes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import pytest

from mandateguard.engineering.resolve_eval import (
    PREREGISTERED_OBSERVED_METRIC_NAMES,
    build_registry,
    load_worlds,
)
from mandateguard.engineering.resolve_eval.metrics import (
    OBSERVED_COUNTER_NAMES,
    MetricSchemaError,
    validate_preregistered_observed_metrics,
)
from mandateguard.engineering.resolve_eval.preregistration import (
    COMMIT_PATH,
    COMMIT_SCHEMA,
    FIXTURE_ROOT,
    FORBIDDEN_CASE_OVERRIDE_KEYS,
    OUTPUT_ROOT,
    PLAN_PATH,
    SAFETY_INVARIANT_IDS,
    PreregistrationError,
    load_frozen_preregistration,
    require_execution_preconditions,
    structural_report,
)
from mandateguard.engineering.resolve_eval.worlds import WorldFixtureError, load_world
from mandateguard.product.evidence_policy import (
    PRODUCT_EVIDENCE_POLICY,
    TRUST_SENSITIVE_FIELDS,
)
from mandateguard.recovery import (
    CLAIM_VALUE_UNESTABLISHED,
    EvidenceKind,
    EvidenceScope,
    METRIC_SCHEMA_VERSION,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMPOSITION = {
    "PURPOSE": 3,
    "EXCLUSION": 3,
    "RECURRENCE": 3,
    "CONFLICT_FRESHNESS": 4,
    "BINDING_COMPLETENESS": 4,
    "UNRESOLVED_FAILURE": 3,
}


@pytest.fixture(scope="module")
def frozen():
    return load_frozen_preregistration(REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def worlds():
    return {world.case_id: world for world in load_worlds(REPOSITORY_ROOT)}


def _sources(world, *, scope: EvidenceScope):
    return tuple(
        source
        for source in world.sources
        if source.manifest.scope_type is scope
        and source.manifest.merchant_id == world.merchant_id
    )


def _claims(record) -> dict[str, str]:
    return {
        claim.claim_id: claim.claim_value
        for claim in record.claims
        if claim.claim_value != CLAIM_VALUE_UNESTABLISHED
    }


def test_plan_is_frozen_with_exactly_twenty_independent_cases(frozen) -> None:
    assert frozen.plan["status"] == "FROZEN"
    assert frozen.plan["outcomes_executed"] is False
    assert len(frozen.plan["cases"]) == 20
    assert len(set(frozen.case_ids)) == 20
    assert dict(frozen.plan["composition"]) == EXPECTED_COMPOSITION

    # Independence is a property of the fixtures, not a claim in prose: no two
    # cases may share a merchant, a SKU, an evidence record, or a source.
    merchants = [case["merchant_id"] for case in frozen.plan["cases"]]
    skus = [case["sku"] for case in frozen.plan["cases"]]
    assert len(set(merchants)) == 20
    assert len(set(skus)) == 20


def test_every_world_binds_its_own_fixtures_and_record_hashes(frozen, worlds) -> None:
    assert set(worlds) == set(frozen.case_ids)
    seen_evidence: set[str] = set()
    seen_sources: set[str] = set()
    for world in worlds.values():
        assert world.initial_evidence.bundle.entries
        for source in world.sources:
            assert source.source_id not in seen_sources
            seen_sources.add(source.source_id)
            for record in source.manifest.records:
                # load_world already refuses a record whose declared hash does
                # not re-derive from the evidence fixture; this asserts every
                # record is genuinely manifested.
                assert len(record.expected_entry_sha256) == 64
        for merchant_id, path in world.evidence_fixtures.items():
            assert path.is_file()
            assert merchant_id not in seen_evidence or merchant_id == world.merchant_id
            seen_evidence.add(merchant_id)


def test_shared_registry_matches_the_freeze_record(frozen, worlds) -> None:
    registry = build_registry(tuple(worlds[case_id] for case_id in frozen.case_ids))
    assert registry.registry_sha256 == frozen.freeze["registry_sha256"]
    assert registry.registry_sha256 == frozen.registry_sha256


def test_freeze_record_commits_the_plan_and_every_fixture(frozen) -> None:
    assert frozen.freeze["plan_canonical_sha256"] == frozen.plan_canonical_sha256
    assert frozen.freeze["plan_raw_file_sha256"] == frozen.plan_raw_file_sha256
    assert frozen.freeze["metric_schema_version"] == METRIC_SCHEMA_VERSION
    fixtures = frozen.freeze["fixture_sha256"]
    assert len(fixtures) == 41
    assert all(
        (REPOSITORY_ROOT / relative).is_file() for relative in fixtures
    )
    assert frozen.freeze["commit_binding"]["mechanism"] == "TWO_STEP_IMMUTABLE_FREEZE"


def test_trust_sensitive_policy_equals_the_product_policy(frozen) -> None:
    policy = frozen.plan["evidence_policy"]
    assert policy["policy_id"] == PRODUCT_EVIDENCE_POLICY.policy_id
    assert policy["top_k"] == PRODUCT_EVIDENCE_POLICY.top_k
    assert policy["alpha"] == f"{PRODUCT_EVIDENCE_POLICY.alpha}"
    assert policy["retrieval_mode"] == PRODUCT_EVIDENCE_POLICY.retrieval_mode.value
    assert (
        policy["max_acquisition_rounds"]
        == PRODUCT_EVIDENCE_POLICY.max_acquisition_rounds
    )
    assert (
        policy["max_new_evidence_items"]
        == PRODUCT_EVIDENCE_POLICY.max_new_evidence_items
    )
    assert tuple(policy["trust_sensitive_fields"]) == TRUST_SENSITIVE_FIELDS
    for case in frozen.plan["cases"]:
        assert case["evidence_policy"] == "product_default_evidence_policy"
        assert not FORBIDDEN_CASE_OVERRIDE_KEYS.intersection(case)
        assert (
            case["max_permitted_acquisition_rounds"]
            == PRODUCT_EVIDENCE_POLICY.max_acquisition_rounds
        )
        assert (
            case["max_permitted_evidence_items"]
            == PRODUCT_EVIDENCE_POLICY.max_new_evidence_items
        )


@pytest.mark.parametrize("override", ("top_k", "alpha", "semantic_mode", "conflict_semantics"))
def test_a_case_carrying_a_trust_sensitive_override_is_refused(
    tmp_path: Path, override: str
) -> None:
    root = _copy_fixture_tree(tmp_path)
    plan_path = root / PLAN_PATH
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["cases"][0][override] = "evaluation-only"
    plan_path.write_bytes((json.dumps(plan, indent=2) + "\n").encode("utf-8"))
    with pytest.raises(PreregistrationError, match="trust-sensitive override"):
        load_frozen_preregistration(root)


def test_metric_schema_is_versioned_and_covers_every_runtime_counter(frozen) -> None:
    schema = frozen.plan["metric_schema"]
    assert schema["version"] == METRIC_SCHEMA_VERSION
    assert tuple(schema["runtime_observed_counters"]) == OBSERVED_COUNTER_NAMES
    assert (
        tuple(schema["preregistered_observed_metrics"])
        == PREREGISTERED_OBSERVED_METRIC_NAMES
    )
    assert set(OBSERVED_COUNTER_NAMES) <= set(PREREGISTERED_OBSERVED_METRIC_NAMES)
    assert tuple(schema["definitions"]) == PREREGISTERED_OBSERVED_METRIC_NAMES


@pytest.mark.parametrize(
    "planned",
    (
        PREREGISTERED_OBSERVED_METRIC_NAMES[:-1],
        PREREGISTERED_OBSERVED_METRIC_NAMES + ("provider_calls_after_allow",),
        tuple(reversed(PREREGISTERED_OBSERVED_METRIC_NAMES)),
    ),
)
def test_drifted_observed_metric_names_are_refused(planned: tuple[str, ...]) -> None:
    with pytest.raises(MetricSchemaError):
        validate_preregistered_observed_metrics(planned, context="test plan")


def test_every_case_preregisters_an_outcome_and_a_safety_posture(frozen) -> None:
    for case in frozen.plan["cases"]:
        assert case["expected_initial_action"] == "REVIEW"
        allowed = case["allowed_final_actions"]
        forbidden = case["forbidden_final_actions"]
        assert allowed and not set(allowed) & set(forbidden)
        assert case["expected_final_action"] in allowed
        assert len(case["expected_safety_posture"]) >= 24
        assert isinstance(case["amount_minor"], int) and case["amount_minor"] > 0
        assert case["currency"] == "INR"
        assert case["world_fixture"].endswith(f"{case['case_id']}.json")
        assert case["initial_evidence_fixture_refs"]
        assert case["recovery_source_manifest_refs"]


def test_allowed_sets_never_permit_allow_on_an_unresolved_case(frozen) -> None:
    unresolved = {
        "RR20-03-PURPOSE-UNRESOLVED",
        "RR20-06-EXCLUSION-AMBIGUOUS",
        "RR20-09-RECURRENCE-UNDOCUMENTED",
        "RR20-11-AUTHORITY-CONFLICT",
        "RR20-12-SCOPE-PRECEDENCE-CONFLICT",
        "RR20-15-WRONG-SKU-BINDING",
        "RR20-17-BUDGET-INSUFFICIENT",
        "RR20-18-NO-REGISTERED-SOURCE",
        "RR20-19-COMPLETE-BUT-INSUFFICIENT",
        "RR20-20-PROVIDER-FAILURE",
    }
    for case in frozen.plan["cases"]:
        if case["case_id"] in unresolved:
            assert "ALLOW" not in case["allowed_final_actions"]
            assert "ALLOW" in case["forbidden_final_actions"]


def test_global_safety_invariants_are_preregistered(frozen) -> None:
    invariants = frozen.plan["safety_invariants"]
    assert tuple(item["id"] for item in invariants) == SAFETY_INVARIANT_IDS
    assert len(SAFETY_INVARIANT_IDS) == 12
    assert all(item["statement"].strip() for item in invariants)


def test_synthetic_value_metric_is_named_without_revenue_language(frozen) -> None:
    metric = frozen.plan["synthetic_value_metric"]
    assert metric["name"] == (
        "synthetic transaction value moved from REVIEW to executable ALLOW"
    )
    assert metric["amounts_frozen_before_outcomes"] is True
    prohibited = {name.casefold() for name in metric["prohibited_names"]}
    assert {"revenue recovered", "gmv recovered", "conversion lift"} <= prohibited
    # The banned phrases may name nothing: not the metric itself, and not any
    # case record. They appear in the plan only in the list that bans them and
    # in the sentence saying this is not a merchant revenue study.
    named = json.dumps(
        {
            "metric": {
                key: value
                for key, value in metric.items()
                if key != "prohibited_names"
            },
            "cases": frozen.plan["cases"],
        }
    ).casefold()
    for banned in prohibited:
        assert banned not in named


def test_completeness_attack_case_exceeds_the_evidence_item_budget(worlds) -> None:
    world = worlds["RR20-17-BUDGET-INSUFFICIENT"]
    sku_sources = _sources(world, scope=EvidenceScope.SKU_SPECIFIC)
    assert len(sku_sources) == 1
    records = sku_sources[0].manifest.records
    assert len(records) == 5
    initial = set(world.initial_evidence_ids)
    new_records = [
        record for record in records if record.evidence_id not in initial
    ]
    assert len(new_records) == 5
    assert len(new_records) > PRODUCT_EVIDENCE_POLICY.max_new_evidence_items
    # Four records read favourably and the last carries the blocking fact.
    assert _claims(records[-1]) == {"billing.model": "RECURRING"}
    assert all("billing.model" not in _claims(record) for record in records[:-1])


def test_authority_conflict_case_has_two_simultaneous_opposite_records(worlds) -> None:
    world = worlds["RR20-11-AUTHORITY-CONFLICT"]
    records = _sources(world, scope=EvidenceScope.SKU_SPECIFIC)[0].manifest.records
    asserted = [record for record in records if "billing.model" in _claims(record)]
    assert len(asserted) == 2
    left, right = asserted
    assert left.expires_at is None and right.expires_at is None
    assert left.supersedes_evidence_id is None and right.supersedes_evidence_id is None
    assert left.effective_at == right.effective_at
    assert _claims(left)["billing.model"] != _claims(right)["billing.model"]


def test_scope_precedence_case_contradicts_across_authority_scopes(worlds) -> None:
    world = worlds["RR20-12-SCOPE-PRECEDENCE-CONFLICT"]
    global_source = _sources(world, scope=EvidenceScope.MERCHANT_GLOBAL)[0]
    sku_source = _sources(world, scope=EvidenceScope.SKU_SPECIFIC)[0]
    assert EvidenceKind.EXCLUSION in global_source.evidence_kinds
    assert EvidenceKind.EXCLUSION in sku_source.evidence_kinds
    global_claims = _claims(global_source.manifest.records[0])
    sku_claims = [_claims(record) for record in sku_source.manifest.records]
    shared = [
        claims
        for claims in sku_claims
        if set(claims) & set(global_claims)
        and any(claims[key] != global_claims[key] for key in set(claims) & set(global_claims))
    ]
    assert shared, "the SKU scope must contradict the merchant-global scope"


def test_freshness_cases_carry_supersession_and_expiry_metadata(worlds) -> None:
    superseded = worlds["RR20-10-SUPERSEDED-RECORD"]
    records = _sources(superseded, scope=EvidenceScope.SKU_SPECIFIC)[0].manifest.records
    assert any(record.supersedes_evidence_id is not None for record in records)

    expired = worlds["RR20-13-EXPIRED-REPLACED"]
    sku_sources = _sources(expired, scope=EvidenceScope.SKU_SPECIFIC)
    assert any(
        source.manifest.supersedes_manifest_id is not None for source in sku_sources
    )
    assert any(
        record.expires_at is not None
        for source in sku_sources
        for record in source.manifest.records
    )


def test_binding_cases_register_real_foreign_and_wrong_sku_evidence(worlds) -> None:
    cross = worlds["RR20-14-CROSS-MERCHANT-SKU"]
    foreign = [
        source for source in cross.sources if source.merchant_id != cross.merchant_id
    ]
    assert foreign, "a second merchant must register the identical SKU"
    assert any(source.sku == cross.sku for source in foreign)
    probe_ids = {probe.source_id for probe in cross.binding_probes}
    assert probe_ids and probe_ids <= {source.source_id for source in foreign}

    wrong_sku = worlds["RR20-15-WRONG-SKU-BINDING"]
    manifested = {
        record.evidence_id
        for source in _sources(wrong_sku, scope=EvidenceScope.SKU_SPECIFIC)
        for record in source.manifest.records
    }
    bundle = {
        entry.evidence_id: entry.sku
        for entry in load_semantic_bundle(wrong_sku).entries
    }
    misbound = [
        evidence_id
        for evidence_id in manifested
        if bundle[evidence_id] != wrong_sku.sku
    ]
    assert misbound, "a manifested record must be served under a different SKU"


def load_semantic_bundle(world):
    from mandateguard.semantic.evidence import load_semantic_evidence_fixture

    return load_semantic_evidence_fixture(world.evidence_fixtures[world.merchant_id])


def test_uncovered_gap_case_registers_no_exclusion_source(worlds) -> None:
    world = worlds["RR20-18-NO-REGISTERED-SOURCE"]
    assert any(
        constraint.constraint_family.value == "EXCLUSION"
        for constraint in world.mandate.payload.constraints.semantic
    )
    assert all(
        EvidenceKind.EXCLUSION not in source.evidence_kinds for source in world.sources
    )


def test_provider_failure_case_declares_a_fault_over_a_present_fixture(worlds) -> None:
    world = worlds["RR20-20-PROVIDER-FAILURE"]
    assert world.provider_fault is not None
    assert world.provider_fault.mode == "ALWAYS_UNAVAILABLE"
    assert world.provider_fault.merchant_id == world.merchant_id
    assert world.evidence_fixtures[world.merchant_id].is_file()
    assert all(other.provider_fault is None for other in worlds.values() if other is not world)


def test_structural_report_declares_no_outcomes() -> None:
    report = structural_report(REPOSITORY_ROOT)
    assert report["outcomes_executed"] is False
    assert report["case_count"] == 20
    assert report["metric_schema_version"] == METRIC_SCHEMA_VERSION
    assert not (REPOSITORY_ROOT / OUTPUT_ROOT).exists()


def _copy_fixture_tree(tmp_path: Path) -> Path:
    destination = tmp_path / FIXTURE_ROOT
    shutil.copytree(REPOSITORY_ROOT / FIXTURE_ROOT, destination)
    return tmp_path


def test_a_tampered_world_fixture_is_refused(tmp_path: Path) -> None:
    root = _copy_fixture_tree(tmp_path)
    world_path = root / FIXTURE_ROOT / "worlds" / "RR20-01-PURPOSE-SUPPORTED.json"
    world = json.loads(world_path.read_text(encoding="utf-8"))
    world["amount_minor"] += 1
    world_path.write_bytes((json.dumps(world, indent=2) + "\n").encode("utf-8"))
    with pytest.raises(PreregistrationError):
        load_frozen_preregistration(root)


def test_a_tampered_evidence_record_breaks_its_manifest_hash(tmp_path: Path) -> None:
    root = _copy_fixture_tree(tmp_path)
    evidence_path = (
        root / FIXTURE_ROOT / "evidence" / "merchant-halcyon-atelier.json"
    )
    bundle = json.loads(evidence_path.read_text(encoding="utf-8"))
    bundle["entries"][-1]["text"] = bundle["entries"][-1]["text"] + " Amended."
    evidence_path.write_bytes((json.dumps(bundle, indent=2) + "\n").encode("utf-8"))
    with pytest.raises(WorldFixtureError, match="hash does not"):
        load_world(
            root / FIXTURE_ROOT / "worlds" / "RR20-01-PURPOSE-SUPPORTED.json",
            repository_root=root,
        )


def test_execution_is_refused_without_a_complete_preregistration(tmp_path: Path) -> None:
    with pytest.raises(PreregistrationError, match="missing"):
        require_execution_preconditions(
            tmp_path, now=datetime(2026, 9, 3, tzinfo=timezone.utc)
        )


def test_execution_is_refused_outside_the_frozen_validity_window(
    tmp_path: Path,
) -> None:
    root = _copy_fixture_tree(tmp_path)
    with pytest.raises(PreregistrationError, match="validity window"):
        require_execution_preconditions(
            root, now=datetime(2026, 8, 1, tzinfo=timezone.utc)
        )


def test_commit_binding_names_the_frozen_plan_and_creates_no_outcomes(frozen) -> None:
    binding = json.loads((REPOSITORY_ROOT / COMMIT_PATH).read_text(encoding="utf-8"))
    assert binding["schema"] == COMMIT_SCHEMA
    assert binding["mechanism"] == "TWO_STEP_IMMUTABLE_FREEZE"
    assert binding["outcomes_executed"] is False
    sha = binding["preregistration_commit_sha"]
    assert len(sha) == 40 and all(character in "0123456789abcdef" for character in sha)
    assert binding["plan_canonical_sha256"] == frozen.plan_canonical_sha256
    assert binding["plan_raw_file_sha256"] == frozen.plan_raw_file_sha256
    assert binding["freeze_raw_file_sha256"] == frozen.freeze_raw_file_sha256
    assert binding["bound_paths"]
    for relative in binding["bound_paths"]:
        assert (REPOSITORY_ROOT / relative).exists()
    # The binding record is added after the plan is frozen, so binding it to
    # itself would make the freeze unverifiable.
    assert str(COMMIT_PATH).replace("\\", "/") not in binding["bound_paths"]


def test_runner_and_validator_scripts_are_preregistered(frozen) -> None:
    runner = frozen.plan["runner"]
    assert (REPOSITORY_ROOT / runner["path"]).is_file()
    assert (REPOSITORY_ROOT / runner["validator_path"]).is_file()
    assert runner["on_failure"] == "STOP"
    assert len(runner["gate"]) >= 15
    assert runner["output_root"] == str(OUTPUT_ROOT).replace("\\", "/")
