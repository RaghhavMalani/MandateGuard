"""Judge-runnable execution attacks over the real controller and gate."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from mandateguard.product.service import CommerceLabService
from mandateguard.product.discovery_service import REGISTERED_SOURCE


@pytest.fixture
def service(tmp_path: Path):
    instance = CommerceLabService(state_dir=tmp_path / "state")
    try:
        yield instance
    finally:
        instance.close()


def _scenario(service: CommerceLabService, scenario_id: str) -> dict:
    run, _deduplicated, _session_id, _scenario = service.playground_scenario(
        scenario_id=scenario_id,
        request_id="execution_lab_" + uuid4().hex,
    )
    assert run.completion.wait(30)
    return service.playground_run_snapshot(run)


def test_recurring_billing_violation_is_a_real_controller_block(
    service: CommerceLabService,
) -> None:
    snapshot = _scenario(service, "recurring-billing")
    result = snapshot["result"]

    assert result["decision"] == "BLOCK"
    assert result["authorization"]["final_controller"] == "BLOCK"
    assert result["buyer"]["mandate"] == (
        "Buy this as a one-time purchase. No subscriptions."
    )
    recurrence_rows = [
        row
        for group in ("tier_a", "tier_b")
        for row in result["authorization"]["deterministic"][group]
        if "recurrence" in row["label"].casefold()
    ]
    assert any(row["status"] != "PASS" for row in recurrence_rows)
    assert result["execution"]["status"] == "NOT_CALLED"
    assert result["execution"]["capability"] is None
    assert result["execution"]["razorpay_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0
    assert any(
        "recurrence" in label.casefold()
        for label in snapshot["explanation"]["failed_constraint_labels"]
    )


@pytest.mark.parametrize(
    ("scenario_id", "mutation", "reason", "changed_field", "authorized", "attempted"),
    (
        (
            "price-mutation",
            "PRICE",
            "TRANSACTION_HASH_MISMATCH",
            "price_minor",
            349_900,
            799_900,
        ),
        (
            "sku-mutation",
            "SKU",
            "TRANSACTION_HASH_MISMATCH",
            "sku",
            "headphones-042",
            "headphones-091",
        ),
        (
            "merchant-mutation",
            "MERCHANT",
            "MERCHANT_MISMATCH",
            "merchant_id",
            None,
            "sandbox-mutation-merchant-b",
        ),
    ),
)
def test_post_authorization_mutations_are_rejected_before_provider_io(
    service: CommerceLabService,
    scenario_id: str,
    mutation: str,
    reason: str,
    changed_field: str,
    authorized: object,
    attempted: object,
) -> None:
    initial = _scenario(service, scenario_id)
    result = initial["result"]
    assert result["decision"] == "ALLOW"
    assert result["authorization"]["final_controller"] == "ALLOW"
    assert result["execution"]["status"] == "AUTHORIZED"
    assert result["execution"]["capability"]["signature_verified"] is True
    assert result["execution"]["razorpay_calls"] == 0

    refused = service.attempt_mutated_execution(initial["run_id"], mutation)
    execution = refused["result"]["execution"]
    lab = execution["lab"]
    checks = lab["checks"]

    assert execution["status"] == "REJECTED_BEFORE_NETWORK"
    assert execution["reason"] == reason
    assert execution["razorpay_calls"] == 0
    assert execution["external_network_calls"] == 0
    assert lab["status"] == "REJECTED_BEFORE_NETWORK"
    assert lab["reason"] == reason
    assert lab["attempted"][changed_field] == attempted
    if authorized is not None:
        assert lab["authorized"][changed_field] == authorized
    else:
        assert lab["authorized"][changed_field] != attempted
    assert lab["provider_additional_calls"] == 0
    assert lab["external_additional_calls"] == 0
    assert checks == {
        "signed": True,
        "expired": False,
        "mandate_active": True,
        "transaction_matches": False,
        "provider_reached": False,
    }


def test_compact_judge_strip_points_only_to_real_registered_scenarios(
    service: CommerceLabService,
) -> None:
    config = service.playground_config()
    assert [item["label"] for item in config["judge_test_strip"]] == [
        "SAFE PURCHASE",
        "BUDGET VIOLATION",
        "RECURRING BILLING",
        "PRICE MUTATION",
        "SKU SWAP",
        "REVOKED AFTER ALLOW",
        "REPLAY ATTEMPT",
    ]
    scenario_ids = {item["scenario_id"] for item in config["scenarios"]}
    assert all(
        item["scenario_id"] in scenario_ids for item in config["judge_test_strip"]
    )


def test_ninety_second_golden_demo_runs_without_reloading_the_service(
    service: CommerceLabService,
) -> None:
    started = monotonic()
    session_id = service.open_judge_session()["session_id"]

    safe_run, _deduplicated, _session_id, _scenario_item = service.playground_scenario(
        scenario_id="safe-purchase",
        request_id="golden_safe_" + uuid4().hex,
        session_id=session_id,
    )
    assert safe_run.completion.wait(30)
    safe = service.playground_run_snapshot(safe_run)
    assert safe["result"]["decision"] == "ALLOW"
    assert safe["result"]["execution"]["status"] == "ORDER_CREATED"
    replayed = service.replay(safe["run_id"])
    assert replayed["result"]["execution"]["replay"]["reason"] == "NONCE_ALREADY_USED"
    assert replayed["result"]["execution"]["replay"]["razorpay_additional_calls"] == 0

    price_run, _deduplicated, _session_id, _scenario_item = service.playground_scenario(
        scenario_id="price-mutation",
        request_id="golden_price_" + uuid4().hex,
        session_id=session_id,
    )
    assert price_run.completion.wait(30)
    price = service.playground_run_snapshot(price_run)
    assert price["result"]["execution"]["status"] == "AUTHORIZED"
    mutated = service.attempt_mutated_execution(price["run_id"], "PRICE")
    assert mutated["result"]["execution"]["reason"] == "TRANSACTION_HASH_MISMATCH"
    assert mutated["result"]["execution"]["razorpay_calls"] == 0

    recurring_run, _deduplicated, _session_id, _scenario_item = service.playground_scenario(
        scenario_id="recurring-billing",
        request_id="golden_recurring_" + uuid4().hex,
        session_id=session_id,
    )
    assert recurring_run.completion.wait(30)
    recurring = service.playground_run_snapshot(recurring_run)
    assert recurring["result"]["decision"] == "BLOCK"
    assert recurring["result"]["execution"]["razorpay_calls"] == 0

    marketplace = service.discovery_search(
        intent="Buy a desk lamp under INR 2,000.", top_k=12
    )
    historical = next(
        item for item in marketplace["candidates"] if item["source"] != REGISTERED_SOURCE
    )
    selected = service.discovery_select(
        intent="Buy a desk lamp under INR 2,000.",
        catalog_product_id=historical["catalog_product_id"],
    )["selection"]
    assert selected["transactable"] is False
    assert selected["stage"] == "REVIEW_REQUIRED"

    assert monotonic() - started < 90
