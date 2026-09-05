"""End-to-end safety and experience checks for the judge Playground.

These tests deliberately cross the product boundary. The universe-only tests
prove what was generated; this module proves that a selected sandbox listing
still goes through the ordinary controller, capability and execution gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from mandateguard.product.playground import PlaygroundError
from mandateguard.product.discovery_service import REGISTERED_SOURCE
from mandateguard.product.service import CommerceLabService
from mandateguard.sandbox.onboarding import (
    MerchantDeclaration,
    NeutralDiscoveryAttributes,
    onboard,
)
from mandateguard.sandbox.templates import WORLD_VERSION


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HEALTH_REPORT = (
    REPOSITORY_ROOT
    / "data"
    / "eval"
    / "judge-playground-v3"
    / "JUDGE_QUERY_REPORT.json"
)


@pytest.fixture
def service(tmp_path: Path):
    instance = CommerceLabService(state_dir=tmp_path / "state")
    try:
        yield instance
    finally:
        instance.close()


def _scenario(service: CommerceLabService, scenario_id: str) -> tuple[dict, str]:
    run, _deduplicated, session_id, _scenario = service.playground_scenario(
        scenario_id=scenario_id,
        request_id="judge_scenario_" + uuid4().hex,
    )
    assert run.completion.wait(30)
    return service.playground_run_snapshot(run), session_id


def _authorize_top(
    service: CommerceLabService, intent: str, *, session_id: str | None = None
) -> tuple[dict, dict, str]:
    search = service.playground_search(intent=intent, top_k=8, session_id=session_id)
    assert search["candidates"]
    candidate = search["candidates"][0]
    run, _deduplicated, resolved_session = service.playground_authorize(
        intent=intent,
        catalog_product_id=candidate["catalog_product_id"],
        request_id="judge_custom_" + uuid4().hex,
        session_id=search["session"]["session_id"],
        max_total_minor=(
            candidate["price_minor"]
            if search["mandate"]["max_total_minor"] is None
            else None
        ),
    )
    assert run.completion.wait(30)
    return service.playground_run_snapshot(run), candidate, resolved_session


@pytest.mark.parametrize(
    ("intent", "category_id"),
    (
        ("headphones under 5000", "audio-headphones"),
        ("wireless headphones for gym under 4k", "audio-headphones"),
        ("desk lamp under 1500", "lighting-desk-lamps"),
        ("something to help me study at night", "lighting-desk-lamps"),
        ("running shoes below 6000", "footwear-running"),
        ("camera for beginners", "cameras"),
        ("backpack for college under 3000", "bags-backpacks"),
        ("smartwatch under 10000", "wearables-smartwatches"),
        ("mechanical keyboard below 7000", "computing-keyboards"),
        ("office chair under 15000", "furniture-office-chairs"),
        ("laptop stand", "computing-laptop-accessories"),
        ("laptop under 60000", "computing-laptops"),
        ("power bank under 2000", "mobile-power"),
        ("yoga mat under 2000", "fitness-equipment"),
        ("resistance band set under 1500", "fitness-equipment"),
    ),
)
def test_likely_judge_inputs_find_a_plausible_top_category(
    service: CommerceLabService, intent: str, category_id: str
) -> None:
    result = service.playground_search(intent=intent, top_k=8)
    assert len(result["candidates"]) >= 5
    assert result["candidates"][0]["category_id"] == category_id
    assert result["authority"] == "RETRIEVAL_IS_ADVISORY_AND_DECIDES_NOTHING"


def test_safe_custom_purchase_uses_the_real_controller_and_offline_gate(
    service: CommerceLabService,
) -> None:
    snapshot, _candidate, _session_id = _authorize_top(
        service, "Buy wireless headphones under INR 5,000. No subscriptions."
    )
    result = snapshot["result"]
    assert snapshot["world"] == "SANDBOX"
    assert result["decision"] == "ALLOW"
    assert result["authorization"]["final_controller"] == "ALLOW"
    assert snapshot["explanation"]["controller"] == (
        "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER"
    )
    assert result["execution"]["status"] == "ORDER_CREATED"
    assert result["execution"]["razorpay_calls"] == 1
    assert result["execution"]["external_network_calls"] == 0
    assert result["execution"]["capability"]["nonce_consumed"] is True
    assert snapshot["explanation"]["payment_reached"] is False
    assert snapshot["explanation"]["offline_adapter_reached"] is True


def test_unknown_product_request_returns_explained_near_misses_not_an_error(
    service: CommerceLabService,
) -> None:
    result = service.playground_search(
        intent="Buy a telescope under INR 20,000.", top_k=8
    )
    assert result["candidates"] == []
    assert result["no_match_message"] == (
        "MandateGuard's sandbox does not currently contain this product category."
    )
    assert result["no_match"]["headline"] == "NO DIRECT SANDBOX MATCH"
    assert len(result["near_misses"]) == 4
    assert all(item["excluded_by"] for item in result["near_misses"])
    assert any(
        item["excluded_by"] == "NO_RELEVANCE_MATCH"
        for item in result["near_misses"]
    )


@pytest.mark.parametrize(
    ("scenario_id", "decision"),
    (
        ("budget-violation", "BLOCK"),
        ("prohibited-content", "BLOCK"),
        ("evidence-conflict", "REVIEW"),
        ("billing-undeclared", "REVIEW"),
    ),
)
def test_block_and_review_are_controller_outcomes_with_zero_provider_calls(
    service: CommerceLabService, scenario_id: str, decision: str
) -> None:
    snapshot, _session_id = _scenario(service, scenario_id)
    result = snapshot["result"]
    assert result["decision"] == decision
    assert result["authorization"]["final_controller"] == decision
    assert result["execution"]["status"] == "NOT_CALLED"
    assert result["execution"]["razorpay_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0


def test_revocation_after_allow_is_refused_before_network(
    service: CommerceLabService,
) -> None:
    snapshot, session_id = _scenario(service, "revoked-after-allow")
    assert snapshot["result"]["decision"] == "ALLOW"
    assert snapshot["result"]["execution"]["status"] == "AUTHORIZED"
    run = service.get_run(snapshot["run_id"])
    assert run is not None
    service.authorize_run_access(run, session_id)

    service.revoke_mandate(snapshot["run_id"])
    refused = service.attempt_execution(snapshot["run_id"])
    execution = refused["result"]["execution"]
    assert execution["status"] == "REJECTED_BEFORE_NETWORK"
    assert execution["reason"] == "MANDATE_REVOKED"
    assert execution["razorpay_calls"] == 0
    assert execution["external_network_calls"] == 0


def test_capability_replay_is_rejected_without_a_second_adapter_call(
    service: CommerceLabService,
) -> None:
    snapshot, _session_id = _scenario(service, "replay")
    assert snapshot["result"]["execution"]["status"] == "ORDER_CREATED"
    replayed = service.replay(snapshot["run_id"])
    replay = replayed["result"]["execution"]["replay"]
    assert replay["status"] == "REJECTED_BEFORE_NETWORK"
    assert replay["reason"] == "NONCE_ALREADY_USED"
    assert replay["razorpay_additional_calls"] == 0
    assert replay["external_additional_calls"] == 0


def test_recoverable_review_acquires_trusted_evidence_then_reauthorizes(
    service: CommerceLabService,
) -> None:
    initial, session_id = _scenario(service, "recoverable-review")
    assert initial["result"]["decision"] == "REVIEW"
    assert initial["result"]["execution"]["razorpay_calls"] == 0
    assert initial["result"]["execution"]["external_network_calls"] == 0
    run = service.get_run(initial["run_id"])
    assert run is not None
    service.authorize_run_access(run, session_id)

    recovered = service.recover(initial["run_id"])
    result = recovered["result"]
    assert result["decision"] == "ALLOW"
    assert result["authorization"]["final_controller"] == "ALLOW"
    assert result["recovery"]["transition"] == "REVIEW -> ALLOW"
    assert result["recovery"]["new_evidence_items"] == 1
    assert result["recovery"]["payment_provider_calls_before_final_allow"] == 0
    assert result["execution"]["status"] == "ORDER_CREATED"
    assert result["execution"]["razorpay_calls"] == 1
    assert result["execution"]["external_network_calls"] == 0


def test_browser_price_sku_and_merchant_fields_have_no_authority(
    service: CommerceLabService,
) -> None:
    intent = "Buy a desk lamp under INR 2,000. No subscriptions."
    search = service.playground_search(intent=intent, top_k=8)
    selected = search["candidates"][0]
    browser_copy = dict(selected)
    browser_copy.update(
        price_minor=799_900,
        merchant_id="sandbox-attacker",
        sku="mutated-sku",
    )

    # The request boundary accepts only the opaque catalog identifier. The
    # authoritative fields are resolved again from the server-owned world.
    run, _deduplicated, _session_id = service.playground_authorize(
        intent=intent,
        catalog_product_id=selected["catalog_product_id"],
        request_id="judge_tamper_" + uuid4().hex,
        session_id=search["session"]["session_id"],
    )
    assert run.completion.wait(30)
    buyer = run.snapshot()["result"]["buyer"]
    assert buyer["price_minor"] == selected["price_minor"]
    assert buyer["price_minor"] != browser_copy["price_minor"]
    assert buyer["merchant"] == selected["merchant_id"]
    assert buyer["merchant"] != browser_copy["merchant_id"]
    assert buyer["sku"] == selected["sku"]
    assert buyer["sku"] != browser_copy["sku"]

    with pytest.raises(PlaygroundError, match="PRODUCT_NOT_FOUND"):
        service.playground_preview(
            intent=intent,
            catalog_product_id=selected["catalog_product_id"][:-1] + "0",
            session_id=search["session"]["session_id"],
        )


def test_runs_and_onboarded_merchants_are_isolated_by_live_session(
    service: CommerceLabService,
) -> None:
    first = service.open_judge_session()["session_id"]
    second = service.open_judge_session()["session_id"]
    snapshot, _candidate, _resolved = _authorize_top(
        service,
        "Buy a power bank under INR 2,000. No subscriptions.",
        session_id=first,
    )
    run = service.get_run(snapshot["run_id"])
    assert run is not None
    service.authorize_run_access(run, first)
    with pytest.raises(PermissionError):
        service.authorize_run_access(run, second)
    with pytest.raises(PermissionError):
        service.authorize_run_access(run, None)

    marketplace = service.discovery_search(
        intent="Buy a desk lamp under INR 2,000.", top_k=12
    )
    listing = next(
        item for item in marketplace["candidates"] if item["source"] != REGISTERED_SOURCE
    )
    onboarded = service.playground_onboard(
        intent="Buy a desk lamp under INR 2,000.",
        catalog_product_id=listing["catalog_product_id"],
        declaration={
            "merchant_display_name": "Session Lamp Merchant",
            "price_minor": 149_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["individual study", "general use"],
        },
        session_id=first,
    )
    product_id = onboarded["product"]["catalog_product_id"]
    service.playground_preview(
        intent="Buy a desk lamp under INR 2,000.",
        catalog_product_id=product_id,
        session_id=first,
    )
    with pytest.raises(PlaygroundError, match="PRODUCT_NOT_FOUND"):
        service.playground_preview(
            intent="Buy a desk lamp under INR 2,000.",
            catalog_product_id=product_id,
            session_id=second,
        )


def test_simulated_onboarding_creates_new_evidence_and_leaves_source_untrusted(
    service: CommerceLabService,
) -> None:
    intent = "Buy a desk lamp under INR 2,000."
    before = service.discovery_search(intent=intent, top_k=12)
    listing = next(
        item for item in before["candidates"] if item["source"] != REGISTERED_SOURCE
    )
    source_id = listing["catalog_product_id"]
    before_selection = service.discovery_select(
        intent=intent, catalog_product_id=source_id
    )["selection"]
    assert before_selection["transactable"] is False

    session_id = service.open_judge_session()["session_id"]
    created = service.playground_onboard(
        intent=intent,
        catalog_product_id=source_id,
        declaration={
            "merchant_display_name": "Declared Light Works",
            "price_minor": 149_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["individual study", "general use"],
        },
        session_id=session_id,
    )
    assert created["world"] == "SANDBOX_ONBOARDED"
    assert created["source_listing"]["still_untrusted"] is True
    assert created["marketplace_listing_after_onboarding"]["transactable"] is False
    assert created["product"]["catalog_product_id"] != source_id
    assert created["merchant"]["merchant_id"].startswith("sandbox-onboarded-")
    assert all(
        "SYNTHETIC SANDBOX RECORD" in item["text"]
        for item in created["trusted_evidence"]
    )

    run, _deduplicated, _ = service.playground_authorize(
        intent=intent,
        catalog_product_id=created["product"]["catalog_product_id"],
        request_id="judge_onboard_" + uuid4().hex,
        session_id=session_id,
    )
    assert run.completion.wait(30)
    snapshot = service.playground_run_snapshot(run)
    assert snapshot["world"] == "SANDBOX_ONBOARDED"
    # Everything the simulated merchant published clears. The one thing it
    # cannot publish is a server-owned product family - its category words came
    # off the crawled page - so A2 reports that identity as unavailable and the
    # run reaches REVIEW instead of assuming the families agree.
    assert snapshot["result"]["decision"] == "REVIEW"
    assert snapshot["result"]["execution"]["external_network_calls"] == 0
    unresolved = [
        row
        for row in snapshot["result"]["authorization"]["deterministic"]["tier_a"]
        if row["status"] != "PASS"
    ]
    assert [row["family"] for row in unresolved] == ["A2"]
    assert unresolved[0]["reason"] == (
        "server-owned product-family identity unavailable for selected SKU"
    )

    after_selection = service.discovery_select(
        intent=intent, catalog_product_id=source_id
    )["selection"]
    assert after_selection == before_selection


def test_changed_onboarding_declaration_gets_a_distinct_exact_identity() -> None:
    attributes = NeutralDiscoveryAttributes(
        listing_id="flipkart.example",
        title="Historical desk lamp",
        category_label="Desk lamps",
        brand_hint=None,
    )
    base = {
        "merchant_display_name": "Declared Merchant",
        "price_minor": 149_900,
        "billing_model": "ONE_TIME",
        "content_classification": "NO_RESTRICTED_CONTENT",
        "purposes": ["general use"],
    }
    first = onboard(
        attributes=attributes,
        declaration=MerchantDeclaration.from_mapping(base),
        session_id="js_" + "1" * 32,
    )
    second = onboard(
        attributes=attributes,
        declaration=MerchantDeclaration.from_mapping(
            {**base, "price_minor": 179_900}
        ),
        session_id="js_" + "1" * 32,
    )
    assert first.merchant_id != second.merchant_id
    assert first.sku != second.sku
    assert first.product.catalog_product_id != second.product.catalog_product_id


def test_frozen_judge_health_report_matches_the_current_world() -> None:
    report = json.loads(HEALTH_REPORT.read_text(encoding="utf-8"))
    assert report["world_version"] == WORLD_VERSION
    assert report["controller"] == "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER"
    assert report["queries"] >= 100
    assert report["overall"]["candidate_found_rate"] > 0.95
    assert report["ordinary"]["rates"]["ALLOW"] >= 0.60
    assert report["ordinary"]["rates"]["REVIEW"] <= 0.30
    assert report["insistent_selection"]["counts"]["BLOCK"] > 0
    assert report["insistent_selection"]["counts"]["ALLOW"] == 0
    assert report["overall"]["counts"]["ERROR"] == 0
