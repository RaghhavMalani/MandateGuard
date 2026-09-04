"""Classifier inference, the mismatch signal, and proposal anomaly analytics."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mandateguard.discovery.anomaly import (
    ANALYTICS_VERSION,
    FEATURE_QUESTIONS,
    FEATURE_WEIGHTS,
    ProposalContext,
    assess,
    band_for,
    build_price_profiles,
    feature_names,
    feature_vector,
)
from mandateguard.discovery.classifier import (
    CategoryClassifier,
    load_classifier,
    write_classifier,
)
from mandateguard.discovery.intent import parse_intent
from mandateguard.discovery.mismatch import (
    BENIGN_CONFUSIONS,
    SEVERITIES,
    evaluate_mismatch,
)
from mandateguard.discovery.transactability import assess_listing, summarize

from tests.discovery_factories import build_catalog, build_product


def _classifier(tmp_path: Path) -> CategoryClassifier:
    """A two-class model that reads 'lamp' as Lighting and 'kurta' as Clothing."""

    terms = ["kurta", "lamp"]
    path = tmp_path / "classifier.mgdx"
    write_classifier(
        path,
        model_id="test-classifier-v1",
        classes=("Clothing", "Home Decor & Festive Needs"),
        terms=terms,
        idf=[1.0, 1.0],
        coefficients=[[3.0, -3.0], [-3.0, 3.0]],
        intercepts=[0.0, 0.0],
        catalog_sha256="0" * 64,
        metrics={"test": {"macro_f1": 0.9}},
        trainer={"features": "title + description"},
    )
    return load_classifier(path)


# --------------------------------------------------------------------------
# Classifier
# --------------------------------------------------------------------------


def test_the_frozen_classifier_predicts_from_title_and_description(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path)
    assert classifier.predict("A desk lamp for reading").label == "Home Decor & Festive Needs"
    assert classifier.predict("A printed cotton kurta").label == "Clothing"


def test_a_listing_with_no_known_term_reports_no_signal_rather_than_a_guess(
    tmp_path: Path,
) -> None:
    prediction = _classifier(tmp_path).predict("zzzqqxx wibble")
    assert prediction.matched_terms == 0
    assert prediction.confidence_band == "NO_SIGNAL"


def test_the_classifier_carries_its_metrics_and_its_lack_of_authority(
    tmp_path: Path,
) -> None:
    classifier = _classifier(tmp_path)
    assert classifier.metrics["test"]["macro_f1"] == 0.9
    payload = classifier.predict("a lamp").to_mapping()
    assert payload["authorization_authority"] == "NONE"


def test_top_k_labels_are_ordered_by_decision_value(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path)
    assert classifier.top_k_labels("a desk lamp", 2) == (
        "Home Decor & Festive Needs",
        "Clothing",
    )


# --------------------------------------------------------------------------
# Mismatch signal
# --------------------------------------------------------------------------


def test_a_listing_that_agrees_with_its_own_text_raises_no_signal(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path)
    product = build_product(
        category_path=("Home Decor & Festive Needs", "Lighting"),
        title="StudyGlow Desk Lamp",
        description="A compact lamp.",
    )
    signal = evaluate_mismatch(product, classifier)
    assert signal.agrees is True
    assert signal.severity == "NONE"


def test_a_laundered_category_raises_a_high_severity_signal(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path)
    product = build_product(
        category_path=("Clothing", "Kurtas"),
        title="StudyGlow Desk Lamp",
        description="A compact lamp for a reading desk.",
    )
    signal = evaluate_mismatch(product, classifier)
    assert signal.agrees is False
    assert signal.severity in {"MEDIUM", "HIGH"}
    assert signal.raises_investigation_priority is True


def test_a_mismatch_can_never_authorize_however_severe(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path)
    product = build_product(category_path=("Clothing",), title="StudyGlow Desk Lamp")
    signal = evaluate_mismatch(product, classifier)
    assert signal.may_authorize is False
    payload = signal.to_mapping()
    assert payload["authorization_authority"] == "NONE"
    assert "AUTHORIZE_PAYMENT" in payload["forbidden_effects"]
    assert "SURFACE_REVIEW" in payload["permitted_effects"]


def test_a_taxonomy_the_model_was_not_trained_on_is_a_gap_not_an_accusation(
    tmp_path: Path,
) -> None:
    classifier = _classifier(tmp_path)
    product = build_product(category_path=("Registered Merchant Catalog", "merchant-x"))
    signal = evaluate_mismatch(product, classifier)
    assert signal.severity == "LOW"
    assert "taxonomy gap" in signal.rationale


def test_categories_a_marketplace_routinely_conflates_are_capped_at_low() -> None:
    assert frozenset({"Clothing", "Footwear"}) in BENIGN_CONFUSIONS
    assert SEVERITIES == ("NONE", "LOW", "MEDIUM", "HIGH")


# --------------------------------------------------------------------------
# Proposal anomaly analytics
# --------------------------------------------------------------------------


def _context(**overrides) -> ProposalContext:
    catalog = build_catalog()
    profiles = build_price_profiles(catalog)
    product = overrides.pop("product", build_product())
    base = {
        "product": product,
        "intent": parse_intent("Buy a desk lamp under Rs 2000. One-time payment only."),
        "price_profile": profiles.get(product.top_category),
        "trusted_evidence_count": 2,
        "authorized_price_minor": product.price_minor,
        "presented_price_minor": product.price_minor,
        "expected_merchant": product.merchant_or_seller,
        "evidence_age_days": 5.0,
        "consent_active": True,
        "replay_seen": False,
    }
    base.update(overrides)
    return ProposalContext(**base)


def test_every_registered_feature_is_computed_and_carries_its_question() -> None:
    assessment = assess(_context())
    assert {item.feature_id for item in assessment.features} == set(FEATURE_QUESTIONS)
    for item in assessment.features:
        assert item.question == FEATURE_QUESTIONS[item.feature_id]
        assert item.finding


def test_an_ordinary_proposal_scores_low_and_a_defective_one_scores_higher() -> None:
    ordinary = assess(_context())
    mutated = assess(_context(presented_price_minor=999_999))
    assert mutated.score > ordinary.score
    assert any(
        item.feature_id == "price_changed_after_authorization" and item.triggered
        for item in mutated.features
    )


def test_missing_trusted_evidence_is_the_heaviest_single_signal() -> None:
    assert FEATURE_WEIGHTS["missing_trusted_evidence"] == max(FEATURE_WEIGHTS.values())
    assessment = assess(_context(trusted_evidence_count=0, evidence_age_days=None))
    finding = next(
        item for item in assessment.features if item.feature_id == "missing_trusted_evidence"
    )
    assert finding.triggered
    assert "not an authorization" in finding.finding


def test_a_recurring_listing_under_a_one_time_mandate_is_flagged_not_decided() -> None:
    product = build_product(
        description="This plan renews monthly until cancelled as a subscription."
    )
    assessment = assess(_context(product=product))
    finding = next(
        item for item in assessment.features if item.feature_id == "recurrence_cues"
    )
    assert finding.triggered
    assert "needs authoritative merchant terms" in finding.finding
    # It raised priority; it did not decide.
    assert assessment.to_mapping()["effect"] == "INVESTIGATION_PRIORITY_ONLY"


def test_revoked_consent_and_replay_both_register_as_signals() -> None:
    assessment = assess(_context(consent_active=False, replay_seen=True))
    triggered = {item.feature_id for item in assessment.triggered}
    assert {"consent_state", "replay_attempt"} <= triggered


def test_a_category_with_too_few_priced_listings_declines_to_judge_the_price() -> None:
    """Three listings is not a distribution."""

    assessment = assess(_context())
    finding = next(
        item for item in assessment.features if item.feature_id == "price_vs_category"
    )
    assert finding.triggered is False
    assert "Too few priced listings" in finding.finding


def test_price_profiles_use_median_and_fences_not_a_mean() -> None:
    products = tuple(
        build_product(source_product_id=f"p{index}", price_minor=10_000)
        for index in range(20)
    ) + (build_product(source_product_id="outlier", price_minor=50_000_000),)
    profiles = build_price_profiles(build_catalog(products))
    profile = profiles[products[0].top_category]
    assert profile.median_minor == 10_000
    assert profile.deviation(10_000) == 0.0
    assert profile.deviation(50_000_000) > 0.0


def test_the_score_bands_are_ordered_and_zero_maps_to_none() -> None:
    assert band_for(0.0) == "NONE"
    assert band_for(0.05) == "LOW"
    assert band_for(0.2) == "ELEVATED"
    assert band_for(0.9) == "HIGH"


def test_the_feature_vector_is_stable_and_named() -> None:
    assessment = assess(_context())
    vector = feature_vector(assessment)
    assert len(vector) == len(FEATURE_QUESTIONS)
    assert feature_names() == sorted(FEATURE_QUESTIONS)
    assert feature_vector(assess(_context())) == vector


def test_the_analytics_version_travels_with_the_assessment() -> None:
    assert assess(_context()).to_mapping()["analytics_version"] == ANALYTICS_VERSION


# --------------------------------------------------------------------------
# Transactability
# --------------------------------------------------------------------------


def test_the_diagnostic_reports_the_six_checks_in_buyer_order() -> None:
    report = assess_listing(build_product(), category_understood=True)
    assert [check.label for check in report.checks] == [
        "DISCOVERABLE",
        "PRICE AVAILABLE",
        "CATEGORY UNDERSTOOD",
        "MERCHANT IDENTITY",
        "SKU TRUST EVIDENCE",
        "RECURRENCE TERMS",
    ]


def test_a_listing_with_no_published_price_reports_no_not_unresolved() -> None:
    """"We could not verify it" and "it does not exist" are different answers."""

    report = assess_listing(
        build_product(price_minor=None), category_understood=True
    )
    price = next(check for check in report.checks if check.label == "PRICE AVAILABLE")
    assert price.status == "NO"


def test_a_marketplace_is_not_reported_as_a_seller_of_record() -> None:
    report = assess_listing(build_product(), category_understood=True)
    merchant = next(check for check in report.checks if check.label == "MERCHANT IDENTITY")
    assert merchant.status == "UNRESOLVED"
    assert "not a seller of record" in merchant.detail


def test_summarize_separates_ready_listings_from_review_required_ones() -> None:
    ready = assess_listing(
        build_product(),
        category_understood=True,
        trusted_evidence_count=1,
        merchant_of_record="merchant-scholarly",
        recurrence_evidenced=True,
    )
    incomplete = assess_listing(build_product(), category_understood=True)
    summary = summarize([ready, incomplete])
    assert summary == {
        "listings": 2,
        "evidence_ready": 1,
        "review_required": 1,
        "mean_resolved_checks": 4.5,
        "checks_per_listing": 6,
    }


def test_summarizing_nothing_does_not_invent_a_denominator() -> None:
    assert summarize([])["listings"] == 0
