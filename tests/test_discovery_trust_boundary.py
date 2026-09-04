"""The discovery layer must never be able to authorize anything.

These are the tests that matter most in this change. Everything else in the
discovery package is a feature; this is the property that makes the feature safe
to have. If one of these fails, the correct response is to delete the capability
that broke it, not to relax the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mandateguard.discovery import trust
from mandateguard.discovery.anomaly import AnomalyAssessment, band_for
from mandateguard.discovery.classifier import CategoryPrediction
from mandateguard.discovery.transactability import assess_listing
from mandateguard.discovery.trust import (
    ADVISORY_CAPABILITIES,
    AdvisorySignal,
    BOUNDARY_STATEMENT,
    FORBIDDEN_CAPABILITIES,
    TrustBoundaryViolation,
    assert_advisory_only,
    boundary_declaration,
)

from tests.discovery_factories import build_product


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_ROOT = REPOSITORY_ROOT / "src" / "mandateguard" / "discovery"

#: Modules whose import into the discovery layer would mean the discovery layer
#: can reach money. `intelligence.store` is deliberately absent from this list:
#: the discovery *search* layer never imports it, and the one product-level
#: adapter that does is asserted separately below.
FORBIDDEN_IMPORTS = (
    "mandateguard.execution",
    "mandateguard.policy",
    "mandateguard.semantic",
    "mandateguard.recovery",
    "mandateguard.replay",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_discovery_module_imports_the_money_path() -> None:
    offenders: list[str] = []
    for path in sorted(DISCOVERY_ROOT.rglob("*.py")):
        for module in _imported_modules(path):
            if any(module.startswith(forbidden) for forbidden in FORBIDDEN_IMPORTS):
                offenders.append(f"{path.relative_to(REPOSITORY_ROOT)} imports {module}")
    assert offenders == [], (
        "the discovery layer must not import the authorization or execution "
        f"path: {offenders}"
    )


def test_advisory_signal_refuses_to_authorize() -> None:
    signal = AdvisorySignal(
        signal_id="product_category_prediction",
        value="Clothing",
        produced_by="discovery-category-linear-v1",
    )
    assert signal.authorization_authority == "NONE"
    with pytest.raises(TrustBoundaryViolation) as error:
        signal.authorize()
    assert BOUNDARY_STATEMENT in str(error.value)


@pytest.mark.parametrize("capability", FORBIDDEN_CAPABILITIES)
def test_forbidden_capabilities_are_refused(capability: str) -> None:
    with pytest.raises(TrustBoundaryViolation):
        assert_advisory_only(capability)


@pytest.mark.parametrize("capability", ADVISORY_CAPABILITIES)
def test_advisory_capabilities_are_permitted(capability: str) -> None:
    assert_advisory_only(capability)


def test_an_unregistered_capability_is_refused_rather_than_assumed_safe() -> None:
    with pytest.raises(TrustBoundaryViolation):
        assert_advisory_only("MOVE_MONEY_JUST_THIS_ONCE")


def test_boundary_declaration_states_the_catalog_is_not_evidence() -> None:
    declaration = boundary_declaration()
    assert declaration["discovery_catalog_is_trusted_evidence"] is False
    assert declaration["authoritative_component"] == (
        "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER"
    )
    assert "AUTHORIZE_PAYMENT" in declaration["ml_may_not"]
    assert "OVERRIDE_DETERMINISTIC_BLOCK" in declaration["ml_may_not"]
    assert "SATISFY_MISSING_TRUSTED_EVIDENCE" in declaration["ml_may_not"]
    assert "OVERRIDE_REVOCATION" in declaration["ml_may_not"]
    assert "OVERRIDE_REQUEST_BINDING" in declaration["ml_may_not"]


def test_every_advisory_output_carries_no_authorization_authority() -> None:
    prediction = CategoryPrediction(
        label="Clothing",
        margin=0.9,
        ranked=(("Clothing", 1.0),),
        matched_terms=5,
        model_id="discovery-category-linear-v1",
    )
    assessment = AnomalyAssessment(score=0.9, band=band_for(0.9))
    report = assess_listing(build_product(), category_understood=True)
    for payload in (
        prediction.to_mapping(),
        assessment.to_mapping(),
        report.to_mapping(),
    ):
        assert payload["authorization_authority"] == "NONE"
    for signal in (prediction.as_signal(), assessment.as_signal(), report.as_signal()):
        with pytest.raises(TrustBoundaryViolation):
            signal.authorize()


def test_a_perfect_transactability_score_is_still_not_an_authorization() -> None:
    """Every check resolved is the *most* this diagnostic can ever say."""

    report = assess_listing(
        build_product(),
        category_understood=True,
        trusted_evidence_count=3,
        merchant_of_record="merchant-scholarly",
        recurrence_evidenced=True,
    )
    assert report.resolved == report.total
    assert report.status == "EVIDENCE READY"
    payload = report.to_mapping()
    assert payload["authorization_authority"] == "NONE"
    assert "cannot authorize" in payload["authority_notice"]
    # The diagnostic reports readiness; the controller still decides.
    assert "controller decides separately" in report.next_action


def test_a_listing_without_merchant_evidence_cannot_reach_evidence_ready() -> None:
    report = assess_listing(
        build_product(),
        category_understood=True,
        trusted_evidence_count=0,
        merchant_of_record=None,
        recurrence_evidenced=False,
    )
    assert report.status == "REVIEW REQUIRED"
    labels = {check.label for check in report.blocking}
    assert {"MERCHANT IDENTITY", "SKU TRUST EVIDENCE", "RECURRENCE TERMS"} <= labels


def test_the_boundary_statement_is_the_one_in_the_brief() -> None:
    assert BOUNDARY_STATEMENT == (
        "ML understands the commerce universe. "
        "MandateGuard's deterministic gate controls money."
    )


def test_discovery_only_stages_end_at_review_required() -> None:
    assert trust.DISCOVERY_ONLY_STAGES == (
        "DISCOVERED",
        "MATCHED",
        "EVIDENCE_INCOMPLETE",
        "REVIEW_REQUIRED",
    )
