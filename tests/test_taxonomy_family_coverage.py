from __future__ import annotations

import pytest

from mandateguard.models.finding import (
    REGISTERED_FAMILIES,
    TIER_A_FAMILIES,
    TIER_B_FAMILIES,
    Finding,
    TaxonomyFamily,
    TierACheckResult,
    TierACheckStatus,
)
from mandateguard.policy import SUPPORTED_TIER_A_FAMILIES, SUPPORTED_TIER_B_FAMILIES
from mandateguard.policy.tier_a import _fail as tier_a_failure
from mandateguard.policy.tier_b import _finding as tier_b_finding


def test_registered_deterministic_families_match_frozen_taxonomy() -> None:
    assert {family.value for family in TIER_A_FAMILIES} == {
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
        "A7",
        "A8",
    }
    assert {family.value for family in TIER_B_FAMILIES} == {
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B6",
        "B7",
        "B8",
        "B9",
        "B10",
    }


def test_every_policy_emission_family_is_registered() -> None:
    assert SUPPORTED_TIER_A_FAMILIES == TIER_A_FAMILIES
    assert SUPPORTED_TIER_B_FAMILIES == TIER_B_FAMILIES
    assert SUPPORTED_TIER_A_FAMILIES | SUPPORTED_TIER_B_FAMILIES <= REGISTERED_FAMILIES


def test_policy_helpers_reject_cross_tier_or_tier_c_findings() -> None:
    with pytest.raises(ValueError):
        tier_a_failure(TaxonomyFamily.B1, "wrong tier")
    with pytest.raises(ValueError):
        tier_b_finding(TaxonomyFamily.C_DEV_PURPOSE, "Tier C is out of scope")


def test_finding_rejects_unregistered_family_string() -> None:
    with pytest.raises(ValueError):
        Finding(family="A99", message="not registered")  # type: ignore[arg-type]


def test_tier_a_result_rejects_non_tier_a_family() -> None:
    with pytest.raises(ValueError):
        TierACheckResult(
            family=TaxonomyFamily.B1,
            status=TierACheckStatus.PASS,
        )
    with pytest.raises(ValueError):
        TierACheckResult(
            family="A1",  # type: ignore[arg-type]
            status=TierACheckStatus.PASS,
        )
