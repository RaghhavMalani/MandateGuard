"""The synthetic universe must be reproducible, and it must not grade itself.

Two failures would make the authorization-scale benchmark worthless, and both are
silent:

*   **A drifted generator.** If the cases this code builds are no longer the
    cases the freeze described, the benchmark measures something nobody
    committed to. The frozen descriptor-stream digests are the tripwire.
*   **A self-graded corpus.** If the expected safe action for a case came from
    the controller, the agreement rate is a tautology. The labels must be
    derivable with the controller absent, and these tests derive them that way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.engineering.authscale.universe import (
    EXPECTED_SAFE_ACTIONS,
    FAMILIES,
    FIXED_CLOCK,
    GATE_FAMILIES,
    SEED,
    SKUS_PER_MERCHANT,
    WORLD_VERSION,
    SyntheticMerchantUniverse,
    build_case,
    case_descriptor,
    descriptor_stream_sha256,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FREEZE = json.loads(
    (REPOSITORY_ROOT / "data" / "eval" / "authorization-scale" / "WORLD_FREEZE.json")
    .read_text(encoding="utf-8")
)
TAXONOMY = json.loads(
    (REPOSITORY_ROOT / "data" / "eval" / "authorization-scale" / "CASE_TAXONOMY.json")
    .read_text(encoding="utf-8")
)


# --------------------------------------------------------------------------
# The generator agrees with the freeze
# --------------------------------------------------------------------------


def test_the_world_constants_match_the_freeze() -> None:
    assert WORLD_VERSION == FREEZE["world_generation_version"]
    assert SEED == FREEZE["seed"]
    assert SKUS_PER_MERCHANT == FREEZE["skus_per_merchant"]
    assert FIXED_CLOCK.strftime("%Y-%m-%dT%H:%M:%SZ") == FREEZE["fixed_clock"]


@pytest.mark.parametrize("rung", FREEZE["scale_ladder"], ids=lambda r: str(r["case_count"]))
def test_the_descriptor_stream_reproduces_the_frozen_digest(rung: dict) -> None:
    """The tripwire. A changed generator fails here rather than downstream."""

    assert descriptor_stream_sha256(rung["case_count"]) == rung[
        "case_descriptor_stream_sha256"
    ]


@pytest.mark.parametrize("rung", FREEZE["scale_ladder"], ids=lambda r: str(r["case_count"]))
def test_the_family_composition_reproduces_the_freeze(rung: dict) -> None:
    counts: dict[str, int] = {}
    for index in range(rung["case_count"]):
        family = str(case_descriptor(index)["family"])
        counts[family] = counts.get(family, 0) + 1
    assert counts == rung["family_composition"]


def test_the_generator_is_deterministic_across_calls() -> None:
    assert [case_descriptor(i) for i in range(50)] == [
        case_descriptor(i) for i in range(50)
    ]
    assert descriptor_stream_sha256(500) == descriptor_stream_sha256(500)


def test_every_taxonomy_family_is_generated_and_no_others() -> None:
    taxonomy_families = {entry["family"] for entry in TAXONOMY["families"]}
    assert set(FAMILIES) == taxonomy_families
    assert set(EXPECTED_SAFE_ACTIONS) == taxonomy_families


def test_expected_safe_actions_match_the_frozen_taxonomy() -> None:
    for entry in TAXONOMY["families"]:
        assert list(EXPECTED_SAFE_ACTIONS[entry["family"]]) == entry[
            "expected_safe_actions"
        ]


def test_merchant_and_sku_identity_follow_the_frozen_layout() -> None:
    for index in (0, 49, 50, 999):
        descriptor = case_descriptor(index)
        assert descriptor["merchant_id"] == (
            f"synthetic-merchant-{index // SKUS_PER_MERCHANT:05d}"
        )
        assert descriptor["sku"] == f"synthetic-sku-{index:05d}"


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3", None])
def test_a_malformed_index_is_refused(bad: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        case_descriptor(bad)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The corpus does not grade itself
# --------------------------------------------------------------------------


def test_the_universe_module_never_imports_the_controller() -> None:
    """Construction must not be able to consult the thing it is testing."""

    source = (
        REPOSITORY_ROOT / "src" / "mandateguard" / "engineering" / "authscale"
        / "universe.py"
    ).read_text(encoding="utf-8")
    for banned in (
        "authorize_transaction",
        "evaluate_tier_a",
        "evaluate_tier_b",
        "issue_execution_authorization",
        "validate_and_reserve_execution",
        "execute_razorpay_order",
    ):
        assert banned not in source, f"universe.py must not reference {banned}"


def test_the_freeze_declares_the_label_source() -> None:
    assert "controller output is prohibited" in TAXONOMY["label_source"]
    assert FREEZE["outcomes_included"] is False
    assert FREEZE["status"] == "FROZEN_BEFORE_EXECUTION"


def test_labels_are_available_without_running_anything() -> None:
    """Every label is a pure function of the descriptor."""

    for index in range(200):
        case = build_case(index)
        descriptor = case_descriptor(index)
        assert list(case.expected_safe_actions) == descriptor["expected_safe_actions"]
        assert case.expected_safe_actions == EXPECTED_SAFE_ACTIONS[case.family]


def test_the_source_catalog_is_never_treated_as_merchant_evidence() -> None:
    assert FREEZE["source_catalog_is_trusted_evidence"] is False
    source = (
        REPOSITORY_ROOT / "src" / "mandateguard" / "engineering" / "authscale"
        / "universe.py"
    ).read_text(encoding="utf-8")
    for banned in ("discovery_catalog", "load_catalog", "DiscoveryProduct", "flipkart"):
        assert banned not in source


# --------------------------------------------------------------------------
# Construction is coherent
# --------------------------------------------------------------------------


def test_every_family_materializes() -> None:
    seen = {build_case(index).family for index in range(200)}
    assert seen == set(FAMILIES)


def test_a_case_binds_its_mandate_to_its_own_merchant_and_sku() -> None:
    for index in range(120):
        case = build_case(index)
        hard = case.mandate.payload.constraints.hard
        assert hard.merchant_allowlist == (case.merchant_id,)
        assert hard.sku_allowlist == (case.sku,)


def test_evidence_absent_families_carry_no_catalog_snapshot() -> None:
    for index in range(400):
        case = build_case(index)
        if case.family in {
            "MISSING_EVIDENCE",
            "AUTHORITY_CONFLICT",
            "STALE_EVIDENCE",
            "SUPERSEDED_EVIDENCE",
        }:
            assert case.catalog_snapshot is None
            assert case.evidence_resolution != "CURRENT_RECORD_RESOLVED"


def test_the_four_evidence_absent_families_are_four_distinct_constructions() -> None:
    """They converge on one controller answer; they are not one world."""

    resolutions = {}
    for index in range(400):
        case = build_case(index)
        if case.catalog_snapshot is None:
            resolutions[case.family] = case.evidence_resolution
    assert len(set(resolutions.values())) == 4, resolutions


def test_gate_families_carry_a_post_issuance_mutation() -> None:
    for index in range(400):
        case = build_case(index)
        if case.family in GATE_FAMILIES:
            assert case.gate_mutation is not None
            assert case.is_gate_family is True
        else:
            assert case.gate_mutation is None


def test_budget_violation_is_exactly_one_minor_unit_over() -> None:
    for index in range(400):
        case = build_case(index)
        if case.family == "BUDGET_VIOLATION":
            ceiling = case.mandate.payload.constraints.hard.max_total_minor
            assert case.transaction.payload.declared_order_total_minor == ceiling + 1


def test_a_universe_yields_exactly_its_case_count() -> None:
    universe = SyntheticMerchantUniverse(case_count=120)
    assert len(list(universe.cases())) == 120
    assert universe.merchant_count == 3


@pytest.mark.parametrize("bad", [0, -5, True, "10", None])
def test_a_malformed_case_count_is_refused(bad: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        SyntheticMerchantUniverse(case_count=bad)  # type: ignore[arg-type]
