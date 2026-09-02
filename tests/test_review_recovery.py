from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mandateguard.core.hashing import sha256_canonical
from mandateguard.models.decision import DecisionAction
from mandateguard.product.service import CommerceLabService, DEMO_PRESETS
from mandateguard.recovery import (
    MAX_ACQUISITION_ROUNDS,
    MAX_NEW_EVIDENCE_ITEMS,
    AcquisitionItemStatus,
    EvidenceKind,
    EvidenceScope,
    RecoveryEventType,
    TrustedEvidenceClaim,
    TrustedEvidenceManifest,
    TrustedEvidenceRecord,
    TrustedEvidenceSource,
    TrustedEvidenceSourceRegistry,
    create_review_recovery,
    recover_review_once,
)
from mandateguard.semantic.evidence import (
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    SemanticEvidenceProviderRegistry,
)

from tests.test_product_commerce_lab import running_server


PRESETS = {item["id"]: item for item in DEMO_PRESETS}


@pytest.fixture
def service(tmp_path: Path) -> Iterator[CommerceLabService]:
    instance = CommerceLabService(state_dir=tmp_path / "state")
    try:
        yield instance
    finally:
        instance.close()


def _run(
    service: CommerceLabService,
    preset_id: str,
    *,
    top_k: int | None = None,
) -> dict:
    kwargs = {"top_k": 2 if top_k is None else top_k}
    return service.run_sync(
        user_intent=PRESETS[preset_id]["intent"],
        preset_id=preset_id,
        **kwargs,
    )


def test_recoverable_review_exposes_server_selected_gap_and_zero_calls(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "recoverable")
    result = initial["result"]

    assert result["decision"] == "REVIEW"
    assert result["execution"]["razorpay_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0
    assert result["recovery"]["status"] == "AVAILABLE"
    assert result["recovery"]["gap"]["reason"] == (
        "Recurring terms could not be verified."
    )
    assert result["recovery"]["trusted_source"]["label"] == "Merchant SKU Terms"
    assert result["recovery"]["action"] == {
        "enabled": True,
        "label": "ACQUIRE TRUSTED EVIDENCE",
        "accepts_source_input": False,
        "accepts_url": False,
        "accepts_evidence_text": False,
    }
    assert result["transactability"]["status"] == "REVIEW"
    assert result["transactability"]["evidence_readiness"] == "INCOMPLETE"


def test_fresh_authorization_resolves_review_to_allow_and_changes_evidence_hash(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "recoverable")
    initial_hash = initial["result"]["recovery"]["current_evidence_sha256"]
    recovered = service.recover(initial["run_id"])
    result = recovered["result"]

    assert result["decision"] == "ALLOW"
    assert result["authorization"]["final_controller"] == "ALLOW"
    assert result["recovery"]["transition"] == "REVIEW -> ALLOW"
    assert result["recovery"]["resolved_after"] == "1 trusted evidence acquisition"
    assert result["recovery"]["new_evidence_items"] == 1
    assert result["recovery"]["current_evidence_sha256"] != initial_hash
    assert result["recovery"]["payment_provider_calls_before_final_allow"] == 0
    assert result["execution"]["status"] == "ORDER_CREATED"
    assert result["execution"]["razorpay_calls"] == 1
    assert result["execution"]["external_network_calls"] == 0
    assert [item["evidence_id"] for item in result["evidence"]["cards"]] == [
        "studyglow-sku-terms-v2"
    ]

    events = [item["event"] for item in recovered["audit"]]
    for expected in (
        RecoveryEventType.INITIAL_REVIEW,
        RecoveryEventType.GAP_IDENTIFIED,
        RecoveryEventType.ROUND_RESERVED,
        RecoveryEventType.SOURCE_SELECTED,
        RecoveryEventType.ACQUISITION_RESULT,
        RecoveryEventType.REAUTHORIZATION,
        RecoveryEventType.REVIEW_RESOLVED,
    ):
        assert expected.value in events
    recovery_events = service.get_run(initial["run_id"]).private_context.recovery_state.audit_events
    assert [item.sequence for item in recovery_events] == list(
        range(1, len(recovery_events) + 1)
    )
    assert all(
        item.previous_event_sha256 == previous.event_sha256
        for previous, item in zip(
            recovery_events[:-1], recovery_events[1:], strict=True
        )
    )


def test_recovered_allow_capability_replay_is_rejected_before_network(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "recoverable")
    service.recover(initial["run_id"])
    replayed = service.replay(initial["run_id"])

    assert replayed["result"]["execution"]["replay"] == {
        "status": "REJECTED_BEFORE_NETWORK",
        "reason": "NONCE_ALREADY_USED",
        "razorpay_additional_calls": 0,
        "external_additional_calls": 0,
    }


def test_new_trusted_evidence_can_resolve_review_to_block_without_execution(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "block", top_k=2)
    assert initial["result"]["decision"] == "REVIEW"

    recovered = service.recover(initial["run_id"])
    result = recovered["result"]
    assert result["decision"] == "BLOCK"
    assert result["recovery"]["transition"] == "REVIEW -> BLOCK"
    assert result["authorization"]["semantic"]["verdict"] == "VIOLATION"
    assert result["execution"]["razorpay_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0


def test_semantic_verifier_can_still_abstain_after_acquisition(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "review", top_k=2)
    recovered = service.recover(initial["run_id"])
    result = recovered["result"]

    assert result["decision"] == "REVIEW"
    assert result["recovery"]["status"] == "NO_RECOVERABLE_GAP"
    assert result["recovery"]["new_evidence_items"] == 1
    assert result["authorization"]["semantic"]["verdict"] == "ABSTAIN"
    assert result["execution"]["razorpay_calls"] == 0


def test_recovery_http_boundary_accepts_no_buyer_source_selection(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "recoverable")
    with running_server(service) as base_url:
        request = Request(
            f"{base_url}/api/runs/{initial['run_id']}/recover",
            data=json.dumps({"url": "https://buyer.invalid/terms"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=10)
        assert caught.value.code == 400
        error = json.loads(caught.value.read().decode("utf-8"))
        assert error["error"]["code"] == "INVALID_REQUEST"
    assert service.get_run(initial["run_id"]).snapshot()["result"]["decision"] == "REVIEW"


@dataclass
class _StaticProvider:
    bundle: SemanticEvidenceBundle

    def fetch_semantic_evidence(self, *, merchant_id: str) -> SemanticEvidenceBundle:
        return self.bundle


def _entry(
    evidence_id: str = "candidate-v1",
    *,
    merchant_id: str = "merchant-test",
    sku: str = "sku-test",
    text: str = "Authoritative product terms establish individual study use.",
) -> SemanticEvidenceEntry:
    return SemanticEvidenceEntry(
        evidence_id=evidence_id,
        merchant_id=merchant_id,
        sku=sku,
        source_kind="product_terms",
        text=text,
    )


def _registry(
    *,
    source: TrustedEvidenceSource,
    provider_entry: SemanticEvidenceEntry,
) -> TrustedEvidenceSourceRegistry:
    provider = _StaticProvider(
        SemanticEvidenceBundle(
            merchant_id=provider_entry.merchant_id,
            entries=(provider_entry,),
        )
    )
    return TrustedEvidenceSourceRegistry(
        sources=(source,),
        providers=SemanticEvidenceProviderRegistry(
            {provider_entry.merchant_id: provider}
        ),
    )


def _source(
    entry: SemanticEvidenceEntry,
    *,
    merchant_id: str | None = None,
    sku: str | None = None,
    expected_hash: str | None = None,
) -> TrustedEvidenceSource:
    actual_merchant = merchant_id or entry.merchant_id
    actual_sku = sku or entry.sku or "sku-test"
    effective_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TrustedEvidenceSource(
        source_id="registered-source-v1",
        display_name="Registered source",
        manifest=TrustedEvidenceManifest(
            manifest_id="registered-manifest-v1",
            source_id="registered-source-v1",
            merchant_id=actual_merchant,
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku=actual_sku,
            evidence_kinds=(EvidenceKind.PURPOSE,),
            manifest_version="1",
            effective_at=effective_at,
            expires_at=None,
            records=(
                TrustedEvidenceRecord(
                    evidence_id=entry.evidence_id,
                    expected_entry_sha256=expected_hash or sha256_canonical(entry),
                    effective_at=effective_at,
                ),
            ),
        ),
    )


def test_registry_rejects_disappeared_no_record_wrong_binding_duplicate_and_tamper() -> None:
    entry = _entry()
    valid = _registry(source=_source(entry), provider_entry=entry)

    disappeared = valid.acquire(
        source_ids=("source-that-disappeared",),
        merchant_id=entry.merchant_id,
        skus=(entry.sku or "",),
        existing_entries=(),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert disappeared.items[0].status is AcquisitionItemStatus.NO_RECORD
    assert disappeared.provider_calls == 0

    other = _entry("other-v1")
    no_record = _registry(source=_source(entry), provider_entry=other).acquire(
        source_ids=("registered-source-v1",),
        merchant_id=entry.merchant_id,
        skus=(entry.sku or "",),
        existing_entries=(),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert no_record.items[0].status is AcquisitionItemStatus.SOURCE_INCOMPLETE

    wrong_sku_entry = _entry(sku="wrong-sku")
    wrong_sku = _registry(
        source=_source(wrong_sku_entry, sku="sku-test"),
        provider_entry=wrong_sku_entry,
    ).acquire(
        source_ids=("registered-source-v1",),
        merchant_id="merchant-test",
        skus=("sku-test",),
        existing_entries=(),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert wrong_sku.items[0].status is AcquisitionItemStatus.WRONG_BINDING

    wrong_merchant = valid.acquire(
        source_ids=("registered-source-v1",),
        merchant_id="merchant-other",
        skus=("sku-test",),
        existing_entries=(),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert wrong_merchant.items[0].status is AcquisitionItemStatus.WRONG_BINDING
    assert wrong_merchant.provider_calls == 0

    duplicate = valid.acquire(
        source_ids=("registered-source-v1",),
        merchant_id=entry.merchant_id,
        skus=(entry.sku or "",),
        existing_entries=(entry,),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert duplicate.items[0].status is AcquisitionItemStatus.ACQUIRED
    assert duplicate.complete is True

    tampered = _registry(
        source=_source(entry, expected_hash="0" * 64),
        provider_entry=entry,
    ).acquire(
        source_ids=("registered-source-v1",),
        merchant_id=entry.merchant_id,
        skus=(entry.sku or "",),
        existing_entries=(),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert tampered.items[0].status is AcquisitionItemStatus.TAMPERED


def test_registry_enforces_item_budget_and_has_no_url_acquisition_surface() -> None:
    entry = _entry()
    registry = _registry(source=_source(entry), provider_entry=entry)
    assert MAX_ACQUISITION_ROUNDS == 2
    assert MAX_NEW_EVIDENCE_ITEMS == 4

    arbitrary_url = registry.acquire(
        source_ids=("https://buyer.invalid/terms",),
        merchant_id=entry.merchant_id,
        skus=(entry.sku or "",),
        existing_entries=(),
        item_limit=1,
        acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert arbitrary_url.provider_calls == 0
    assert arbitrary_url.items[0].status is AcquisitionItemStatus.NO_RECORD
    with pytest.raises(ValueError, match="fixed evidence budget"):
        registry.acquire(
            source_ids=("registered-source-v1",),
            merchant_id=entry.merchant_id,
            skus=(entry.sku or "",),
            existing_entries=(),
            item_limit=MAX_NEW_EVIDENCE_ITEMS + 1,
            acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


def test_round_budget_terminates_repeated_no_record_acquisition(
    service: CommerceLabService,
) -> None:
    initial = _run(service, "recoverable")
    context = service.get_run(initial["run_id"]).private_context
    source = context.recovery_state.gap_analysis.gaps[0].candidate_evidence_ids[0]
    missing_registry = TrustedEvidenceSourceRegistry(
        sources=(),
        providers=SemanticEvidenceProviderRegistry({}),
    )
    state = create_review_recovery(
        scenario=context.recovery_state.scenario,
        authorization=context.recovery_state.initial_authorization,
        semantic_evidence=None,
        registry=service.recovery_registry,
        created_at=context.evaluated_at,
    )
    # Preserve the frozen identified candidate while simulating disappearance.
    assert state.gap_analysis.gaps[0].candidate_evidence_ids[0] == source
    state = recover_review_once(
        state=state,
        registry=missing_registry,
        semantic_verifier=context.semantic_verifier,
        recorded_at=context.evaluated_at,
    )
    state = replace(state, gap_analysis=context.recovery_state.gap_analysis)
    state = recover_review_once(
        state=state,
        registry=missing_registry,
        semantic_verifier=context.semantic_verifier,
        recorded_at=context.evaluated_at,
    )
    assert state.rounds_used == MAX_ACQUISITION_ROUNDS
    assert state.final_action is DecisionAction.REVIEW
    with pytest.raises(RuntimeError, match="round budget exhausted"):
        recover_review_once(
            state=state,
            registry=missing_registry,
            semantic_verifier=context.semantic_verifier,
            recorded_at=context.evaluated_at,
        )


def test_non_benchmark_evaluation_plan_was_frozen_and_outcomes_are_bounded() -> None:
    root = Path(__file__).resolve().parents[1]
    plan = json.loads(
        (
            root
            / "fixtures"
            / "engineering"
            / "review_recovery"
            / "evaluation_plan.json"
        ).read_text(encoding="utf-8")
    )
    summary = json.loads(
        (
            root
            / "artifacts"
            / "engineering"
            / "review_recovery"
            / "resolve-nonbenchmark-v1"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )

    assert plan["status"] == "FROZEN_BEFORE_OUTCOMES"
    assert plan["limits"] == {
        "max_acquisition_rounds": MAX_ACQUISITION_ROUNDS,
        "max_new_evidence_items": MAX_NEW_EVIDENCE_ITEMS,
    }
    assert summary["classification"] == (
        "NON_BENCHMARK_SYNTHETIC_ENGINEERING_EVALUATION"
    )
    assert summary["metrics"] == {
        "initial_review_count": 3,
        "resolved_after_bounded_acquisition": 2,
        "resolved_to_allow": 1,
        "resolved_to_block": 1,
        "still_review": 1,
        "mean_additional_trusted_evidence_items": {
            "numerator": 3,
            "denominator": 3,
            "decimal": "1.000",
        },
        "max_acquisition_rounds": 1,
        "payment_provider_calls_before_final_allow": 0,
        "planner_direct_unsafe_allow_count": 0,
        "synthetic_transaction_value_released_from_review_minor": 129900,
    }
    assert summary["architecture_verification"]["planner_can_emit_allow_or_block"] is False
    assert summary["external_calls"] == {
        "openai_calls": 0,
        "razorpay_calls": 0,
        "network_calls": 0,
        "offline_execution_double_calls_after_final_allow": 1,
    }
