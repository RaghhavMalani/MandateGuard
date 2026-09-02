"""Product-default behaviour, product/evaluator parity, and state configuration.

Every run in this module uses the real ``CommerceLabService`` with the product
default evidence policy. Nothing here injects ``top_k`` or any other
trust-sensitive override, so a scenario that reaches ``REVIEW`` does so because
its registered evidence is genuinely insufficient.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import socket
from typing import Iterator
import urllib.request as urllib_request

import pytest

from mandateguard.intelligence.retrieval import DEFAULT_ALPHA, DEFAULT_TOP_K
from mandateguard.product.evidence_policy import (
    PRODUCT_EVIDENCE_POLICY,
    TRUST_SENSITIVE_FIELDS,
)
from mandateguard.product.service import (
    CommerceLabService,
    DEMO_PRESETS,
    ObservedCreateResource,
    OperationalCounters,
    RESOLVE_EVALUATION_SCENARIOS,
)
from mandateguard.recovery import (
    MAX_ACQUISITION_ROUNDS,
    MAX_NEW_EVIDENCE_ITEMS,
    RecoveryEventType,
)


T0 = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
PRESETS = {item["id"]: item for item in DEMO_PRESETS}
EXPECTED_INITIAL_CONTROLLERS = {
    "safe": "ALLOW",
    "block": "BLOCK",
    "review": "REVIEW",
    "recoverable": "REVIEW",
}


@pytest.fixture
def service(tmp_path: Path) -> Iterator[CommerceLabService]:
    instance = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib_request, "urlopen", forbidden)


def _run(service: CommerceLabService, preset_id: str, index: int) -> dict:
    """Run one preset with product defaults and no evidence-policy argument."""

    return service.run_sync(
        user_intent=PRESETS[preset_id]["intent"],
        preset_id=preset_id,
        request_id=f"product_default_{preset_id}_{index:04d}",
    )


def test_failed_openai_resource_attempt_is_observed_not_assumed_zero() -> None:
    class FailingResource:
        def create(self, **_kwargs: object) -> object:
            raise RuntimeError("injected resource failure")

    counters = OperationalCounters()
    observed = ObservedCreateResource(
        FailingResource(), counters.record_openai_call
    )
    with pytest.raises(RuntimeError, match="injected resource failure"):
        observed.create(model="offline-path-regression")
    assert counters.openai_calls == 1


def test_product_defaults_produce_the_four_expected_initial_controllers(
    service: CommerceLabService, no_network: None
) -> None:
    for index, preset in enumerate(DEMO_PRESETS, start=1):
        snapshot = _run(service, preset["id"], index)
        result = snapshot["result"]

        assert snapshot["state"] == "COMPLETE", snapshot["error"]
        assert result["decision"] == EXPECTED_INITIAL_CONTROLLERS[preset["id"]]
        assert result["trust_configuration"]["top_k"] == DEFAULT_TOP_K
        assert result["trust_configuration"]["evidence_policy_overridden"] is False
        assert result["observed_counters"]["openai_calls"] == 0
        assert result["observed_counters"]["razorpay_http_calls"] == 0
        assert result["execution"]["external_network_calls"] == 0


def test_recoverable_review_recovers_to_allow_at_product_defaults(
    service: CommerceLabService, no_network: None
) -> None:
    initial = _run(service, "recoverable", 1)
    before = initial["result"]

    assert before["decision"] == "REVIEW"
    assert before["buyer"]["merchant"] == "merchant-lumen"
    assert before["buyer"]["sku"] == "aurora-focus-lamp"
    # The gap is real: the registered listing documents neither the intended
    # use nor the billing model, so both semantic constraints abstain.
    assert [item["status"] for item in before["authorization"]["semantic"]["checks"]] == [
        "ABSTAIN",
        "ABSTAIN",
    ]
    assert before["recovery"]["status"] == "AVAILABLE"
    assert before["execution"]["status"] == "NOT_CALLED"
    assert before["execution"]["razorpay_calls"] == 0
    assert before["observed_counters"]["offline_adapter_calls"] == 0
    assert before["observed_counters"]["trusted_evidence_provider_calls"] == 0
    initial_evidence_sha256 = before["recovery"]["current_evidence_sha256"]
    initial_authorization_sha256 = next(
        item["details"]["authorization_result_sha256"]
        for item in initial["audit"]
        if item["event"] == RecoveryEventType.INITIAL_REVIEW.value
    )

    recovered = service.recover(initial["run_id"])
    after = recovered["result"]

    assert after["decision"] == "ALLOW"
    assert after["authorization"]["final_controller"] == "ALLOW"
    assert after["recovery"]["transition"] == "REVIEW -> ALLOW"
    assert after["recovery"]["current_evidence_sha256"] != initial_evidence_sha256
    reauthorization = next(
        item
        for item in recovered["audit"]
        if item["event"] == RecoveryEventType.REAUTHORIZATION.value
    )
    assert (
        reauthorization["details"]["authorization_result_sha256"]
        != initial_authorization_sha256
    )

    # A capability exists only after the fresh ALLOW, and execution ran once.
    assert after["execution"]["status"] == "ORDER_CREATED"
    assert after["execution"]["capability"]["signature_verified"] is True
    assert after["execution"]["capability"]["single_use"] is True
    assert after["recovery"]["payment_provider_calls_before_final_allow"] == 0
    assert after["observed_counters"]["offline_adapter_calls"] == 1
    assert after["observed_counters"]["razorpay_http_calls"] == 0
    assert after["observed_counters"]["openai_calls"] == 0
    assert after["observed_counters"]["trusted_evidence_provider_calls"] == 1
    assert after["observed_counters"]["acquisition_rounds"] == 1
    assert after["observed_counters"]["new_evidence_items"] == 1
    assert after["observed_counters"]["planner_direct_allow_count"] == 0

    replayed = service.replay(initial["run_id"])
    assert replayed["result"]["execution"]["replay"] == {
        "status": "REJECTED_BEFORE_NETWORK",
        "reason": "NONCE_ALREADY_USED",
        "razorpay_additional_calls": 0,
        "external_additional_calls": 0,
    }
    assert (
        replayed["result"]["observed_counters"]["offline_adapter_calls"] == 1
    ), "a rejected replay must not reach the execution adapter again"


def test_evaluation_scenarios_share_the_product_trust_configuration(
    service: CommerceLabService, no_network: None
) -> None:
    """Product and evaluation runs must agree on every trust-sensitive field."""

    product = _run(service, "recoverable", 2)["result"]["trust_configuration"]
    for index, scenario in enumerate(RESOLVE_EVALUATION_SCENARIOS, start=1):
        snapshot = service.run_sync(
            user_intent=scenario["intent"],
            request_id=f"parity_case_{index:04d}",
        )
        result = snapshot["result"]
        assert snapshot["state"] == "COMPLETE", snapshot["error"]
        assert result["decision"] == "REVIEW"
        assert result["buyer"]["merchant"] == scenario["merchant_id"]
        assert result["buyer"]["sku"] == scenario["sku"]
        evaluation = result["trust_configuration"]
        for field in TRUST_SENSITIVE_FIELDS:
            assert evaluation[field] == product[field], field
        assert evaluation == service.trust_configuration()


def test_trust_configuration_reports_the_shared_server_owned_policy(
    service: CommerceLabService,
) -> None:
    configuration = service.trust_configuration()

    assert set(configuration) == set(TRUST_SENSITIVE_FIELDS)
    assert configuration["policy_id"] == PRODUCT_EVIDENCE_POLICY.policy_id
    assert configuration["top_k"] == DEFAULT_TOP_K
    assert configuration["alpha"] == DEFAULT_ALPHA
    assert configuration["retrieval_mode"] == "hybrid"
    assert configuration["max_acquisition_rounds"] == MAX_ACQUISITION_ROUNDS
    assert configuration["max_new_evidence_items"] == MAX_NEW_EVIDENCE_ITEMS
    assert configuration["registry_sha256"] == service.recovery_registry.registry_sha256
    assert configuration["evidence_policy_overridden"] is False
    assert service.trust_configuration(top_k=2)["evidence_policy_overridden"] is True


def test_evaluation_scenario_resolves_review_to_block_without_execution(
    service: CommerceLabService, no_network: None
) -> None:
    scenario = next(
        item
        for item in RESOLVE_EVALUATION_SCENARIOS
        if item["case_id"] == "RR-BLOCK-SIGNAL-EDGE"
    )
    initial = service.run_sync(
        user_intent=scenario["intent"], request_id="signal_edge_0001"
    )
    assert initial["result"]["decision"] == "REVIEW"

    recovered = service.recover(initial["run_id"])
    result = recovered["result"]

    assert result["decision"] == "BLOCK"
    assert result["recovery"]["transition"] == "REVIEW -> BLOCK"
    assert result["authorization"]["semantic"]["verdict"] == "VIOLATION"
    assert result["execution"]["status"] == "NOT_CALLED"
    assert result["observed_counters"]["offline_adapter_calls"] == 0
    assert result["observed_counters"]["razorpay_http_calls"] == 0


def test_configured_state_dir_persists_cache_ledger_and_audit_across_reopen(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "configured-state"
    first = CommerceLabService(state_dir=state_dir, clock=lambda: T0)
    try:
        assert first.state_persistence == "CONFIGURED_DIRECTORY"
        initial = _run(first, "recoverable", 1)
        recovered = first.recover(initial["run_id"])
        review_id = first.get_run(
            initial["run_id"]
        ).private_context.recovery_state.review_id
        events = first.recovery_audit_store.read(review_id)
        linked = next(event for event in events if event["event"] == "EXECUTION_LINKED")
        decision_nonce = linked["decision_nonce"]
        execution_request_sha256 = linked["execution_request_sha256"]
        assert recovered["result"]["execution"]["status"] == "ORDER_CREATED"
    finally:
        first.close()

    reopened = CommerceLabService(state_dir=state_dir, clock=lambda: T0)
    try:
        assert reopened.recovery_audit_store.read(review_id) == events
        ledger_record = reopened.execution_ledger.get(decision_nonce)
        assert ledger_record is not None
        assert ledger_record.execution_request_sha256 == execution_request_sha256
        repeated = _run(reopened, "recoverable", 2)
        assert repeated["result"]["authorization"]["semantic"]["cache"][
            "status"
        ] == "HIT"
        assert reopened.health()["state_persistence"] == "CONFIGURED_DIRECTORY"
    finally:
        reopened.close()


def test_state_dir_environment_variable_configures_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "env-state"
    monkeypatch.setenv("MANDATEGUARD_STATE_DIR", str(configured))
    service = CommerceLabService(clock=lambda: T0)
    try:
        assert service.state_dir == configured
        assert service.state_persistence == "CONFIGURED_DIRECTORY"
        assert (
            service.public_config()["resolve"]["state_persistence"]
            == "CONFIGURED_DIRECTORY"
        )
    finally:
        service.close()


def test_absent_state_dir_falls_back_to_declared_temporary_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MANDATEGUARD_STATE_DIR", raising=False)
    service = CommerceLabService(clock=lambda: T0)
    try:
        assert service.state_persistence == "EPHEMERAL_TEMPORARY_DIRECTORY"
        assert (
            service.health()["state_persistence"] == "EPHEMERAL_TEMPORARY_DIRECTORY"
        )
    finally:
        service.close()


def test_recovery_audit_links_review_to_capability_and_execution(
    service: CommerceLabService, no_network: None
) -> None:
    """A reviewer can join REVIEW to execution from durable storage alone."""

    initial = _run(service, "recoverable", 3)
    recovered = service.recover(initial["run_id"])
    review_id = service.get_run(
        initial["run_id"]
    ).private_context.recovery_state.review_id
    events = service.recovery_audit_store.read(review_id)

    assert {event["mandate_payload_sha256"] for event in events}
    assert len({event["mandate_payload_sha256"] for event in events}) == 1
    assert len({event["transaction_body_sha256"] for event in events}) == 1
    linked = next(
        event
        for event in events
        if event["event"] == RecoveryEventType.EXECUTION_LINKED.value
    )
    capability = recovered["result"]["execution"]
    assert linked["decision_nonce"]
    assert linked["execution_request_sha256"]
    assert linked["execution_receipt_id"] == capability["order"]["order_id"]
    assert linked["outcome_codes"] == ["EXECUTION_RECORDED"]
    # The linkage joins to the execution ledger without in-memory run state.
    ledger_record = service.execution_ledger.get(linked["decision_nonce"])
    assert ledger_record is not None
    assert ledger_record.execution_request_sha256 == linked[
        "execution_request_sha256"
    ]
