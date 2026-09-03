from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
import sqlite3
from threading import Event, Thread
from time import sleep

import pytest

from mandateguard.core.hashing import mandate_payload_sha256
from mandateguard.execution import (
    ExecutionLedgerStatus,
    ExecutionReceipt,
    ExecutionRefusal,
    ExecutionRefusalReason,
    HMACSHA256Signer,
    HMACSHA256Verifier,
    InMemoryMandateStateRegistry,
    MandateState,
    MandateStateBusyError,
    MandateStateCorruptionError,
    MandateStateTransitionError,
    MandateStatus,
    SQLiteExecutionLedger,
    SQLiteMandateStateRegistry,
    SignedExecutionAuthorization,
    build_razorpay_order_request,
    execute_razorpay_order,
    execution_request_sha256,
    issue_execution_authorization,
)
from mandateguard.product.service import (
    CommerceLabService,
    DEMO_PRESETS,
    REVOCATION_DEMO_PRESET,
)
from mandateguard.execution.models import RazorpayOrderRequest, RazorpayOrderResult
from tests.execution_factories import (
    CAPABILITY_EXPIRES_AT,
    CONFIG,
    DECISION_NONCE,
    SIGNING_KEY_ID,
    SYNTHETIC_SIGNING_KEY,
    RecordingClient,
    make_authorization,
)
from tests.factories import SERVER_TIME


def _signer() -> HMACSHA256Signer:
    return HMACSHA256Signer(
        key_id=SIGNING_KEY_ID, key=SYNTHETIC_SIGNING_KEY
    )


def _verifier() -> HMACSHA256Verifier:
    return HMACSHA256Verifier({SIGNING_KEY_ID: SYNTHETIC_SIGNING_KEY})


def _issue(
    registry,
    *,
    version: int = 1,
    decision_nonce: str = DECISION_NONCE,
):
    authorization, scenario = make_authorization()
    registry.register_active(
        scenario.mandate.payload.mandate_id,
        version,
        updated_at=SERVER_TIME,
    )
    capability = issue_execution_authorization(
        authorization_result=authorization,
        authorization_scenario=scenario,
        semantic_evidence=None,
        semantic_verifier=None,
        issued_at=SERVER_TIME,
        expires_at=CAPABILITY_EXPIRES_AT,
        decision_nonce=decision_nonce,
        config=CONFIG,
        signer=_signer(),
        mandate_state_registry=registry,
        mandate_version=version,
    )
    assert isinstance(capability, SignedExecutionAuthorization)
    return capability, authorization, scenario


def _execute(capability, authorization, scenario, registry, ledger, client, *, now=SERVER_TIME):
    return execute_razorpay_order(
        authorization=capability,
        authorization_result=authorization,
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        now=now,
        config=CONFIG,
        verifier=_verifier(),
        ledger=ledger,
        client=client,
        mandate_state_registry=registry,
    )


def test_a_active_mandate_and_valid_capability_execute(tmp_path) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry)
    ledger = SQLiteExecutionLedger(tmp_path / "active-ledger.sqlite3")
    client = RecordingClient()

    result = _execute(
        capability, authorization, scenario, registry, ledger, client
    )

    assert isinstance(result, ExecutionReceipt)
    assert len(client.calls) == 1


def test_b_h_revoked_valid_capability_is_refused_and_audited_before_provider(
    tmp_path,
) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry)
    registry.revoke(
        capability.payload.mandate_id,
        capability.payload.mandate_version,
        revoked_at=SERVER_TIME + timedelta(seconds=1),
    )
    ledger = SQLiteExecutionLedger(tmp_path / "revoked-ledger.sqlite3")
    client = RecordingClient()

    assert _verifier().verify(capability).value == "VALID"
    assert capability.payload.mandate_payload_sha256 == mandate_payload_sha256(
        scenario.mandate
    )
    result = _execute(
        capability, authorization, scenario, registry, ledger, client
    )

    assert result == ExecutionRefusal(ExecutionRefusalReason.MANDATE_REVOKED)
    assert client.calls == []
    assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.REJECTED
    events = registry.audit_events(capability.payload.mandate_id)
    assert [event["event"] for event in events] == [
        "MANDATE_REGISTERED_ACTIVE",
        "MANDATE_REVOKED",
        "EXECUTION_REFUSED_MANDATE_STATE",
    ]
    assert events[-1]["decision_nonce"] == DECISION_NONCE
    assert events[-1]["execution_request_sha256"] == (
        capability.payload.execution_request_sha256
    )
    assert "signature" not in str(events).lower()


def test_c_version_7_capability_is_refused_after_supersession_to_8(tmp_path) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry, version=7)
    registry.supersede(
        capability.payload.mandate_id,
        7,
        superseded_by_version=8,
        updated_at=SERVER_TIME + timedelta(seconds=1),
    )
    client = RecordingClient()

    result = _execute(
        capability,
        authorization,
        scenario,
        registry,
        SQLiteExecutionLedger(tmp_path / "superseded-ledger.sqlite3"),
        client,
    )

    assert result == ExecutionRefusal(ExecutionRefusalReason.MANDATE_SUPERSEDED)
    assert client.calls == []
    assert registry.get_current(capability.payload.mandate_id).version == 8


def test_d_missing_current_mandate_state_is_refused(tmp_path) -> None:
    issuing_registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(issuing_registry)
    client = RecordingClient()

    result = _execute(
        capability,
        authorization,
        scenario,
        InMemoryMandateStateRegistry(),
        SQLiteExecutionLedger(tmp_path / "missing-ledger.sqlite3"),
        client,
    )

    assert result == ExecutionRefusal(ExecutionRefusalReason.MANDATE_STATE_MISSING)
    assert client.calls == []


def test_e_validly_signed_wrong_mandate_id_is_refused(tmp_path) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry)
    wrong_id = "00000000-0000-4000-8000-000000000002"
    wrong_capability = _signer().sign(
        replace(capability.payload, mandate_id=wrong_id)
    )
    client = RecordingClient()

    result = _execute(
        wrong_capability,
        authorization,
        scenario,
        registry,
        SQLiteExecutionLedger(tmp_path / "wrong-id-ledger.sqlite3"),
        client,
    )

    assert result == ExecutionRefusal(ExecutionRefusalReason.MANDATE_ID_MISMATCH)
    assert client.calls == []


def test_f_validly_signed_wrong_version_is_refused(tmp_path) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry)
    wrong_capability = _signer().sign(
        replace(capability.payload, mandate_version=99)
    )
    client = RecordingClient()

    result = _execute(
        wrong_capability,
        authorization,
        scenario,
        registry,
        SQLiteExecutionLedger(tmp_path / "wrong-version-ledger.sqlite3"),
        client,
    )

    assert result == ExecutionRefusal(
        ExecutionRefusalReason.MANDATE_VERSION_MISMATCH
    )
    assert client.calls == []


def test_g_m_revocation_refusal_consumes_nonce_and_replay_stays_refused(
    tmp_path,
) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry)
    registry.revoke(
        capability.payload.mandate_id,
        1,
        revoked_at=SERVER_TIME + timedelta(seconds=1),
    )
    ledger = SQLiteExecutionLedger(tmp_path / "consumed-ledger.sqlite3")
    client = RecordingClient()

    first = _execute(
        capability, authorization, scenario, registry, ledger, client
    )
    second = _execute(
        capability, authorization, scenario, registry, ledger, client
    )

    assert first == ExecutionRefusal(ExecutionRefusalReason.MANDATE_REVOKED)
    assert second == ExecutionRefusal(ExecutionRefusalReason.NONCE_ALREADY_USED)
    assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.REJECTED
    assert client.calls == []


def test_i_expiry_precedes_revocation_deterministically(tmp_path) -> None:
    registry = InMemoryMandateStateRegistry()
    capability, authorization, scenario = _issue(registry)
    registry.revoke(
        capability.payload.mandate_id,
        1,
        revoked_at=SERVER_TIME + timedelta(seconds=1),
    )
    ledger = SQLiteExecutionLedger(tmp_path / "expired-revoked-ledger.sqlite3")
    client = RecordingClient()

    result = _execute(
        capability,
        authorization,
        scenario,
        registry,
        ledger,
        client,
        now=CAPABILITY_EXPIRES_AT,
    )

    assert result == ExecutionRefusal(ExecutionRefusalReason.CAPABILITY_EXPIRED)
    assert ledger.get(DECISION_NONCE) is None
    assert client.calls == []


def test_j_recovered_allow_cannot_execute_after_revocation(tmp_path) -> None:
    presets = {item["id"]: item for item in DEMO_PRESETS}
    with CommerceLabService(state_dir=tmp_path / "recovery-state") as service:
        initial = service.run_sync(
            user_intent=presets["recoverable"]["intent"],
            preset_id="recoverable",
            defer_execution=True,
        )
        recovered = service.recover(initial["run_id"])
        assert recovered["result"]["decision"] == "ALLOW"
        assert recovered["result"]["execution"]["status"] == "AUTHORIZED"
        assert recovered["result"]["execution"]["razorpay_calls"] == 0

        service.revoke_mandate(initial["run_id"])
        refused = service.attempt_execution(initial["run_id"])

    assert refused["result"]["execution"]["status"] == "REJECTED_BEFORE_NETWORK"
    assert refused["result"]["execution"]["reason"] == "MANDATE_REVOKED"
    assert refused["result"]["execution"]["razorpay_calls"] == 0


def test_k_registry_survives_reopen_and_audit_chain_validates(tmp_path) -> None:
    path = tmp_path / "state" / "mandate-state.sqlite3"
    path.parent.mkdir()
    mandate_id = "00000000-0000-4000-8000-000000000003"
    first = SQLiteMandateStateRegistry(path)
    first.register_active(mandate_id, 1, updated_at=SERVER_TIME)
    first.revoke(
        mandate_id, 1, revoked_at=SERVER_TIME + timedelta(seconds=1)
    )
    first.close()

    reopened = SQLiteMandateStateRegistry(path)
    try:
        state = reopened.get_current(mandate_id)
        events = reopened.audit_events(mandate_id)
    finally:
        reopened.close()

    assert state.status is MandateStatus.REVOKED
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[1]["previous_event_sha256"] == events[0]["event_sha256"]


def test_l_revocation_neither_reruns_models_nor_exposes_registry_to_product_payload(
    tmp_path,
) -> None:
    with CommerceLabService(state_dir=tmp_path / "model-boundary") as service:
        initial = service.run_sync(
            user_intent=REVOCATION_DEMO_PRESET["intent"],
            preset_id=REVOCATION_DEMO_PRESET["id"],
        )
        counters_before = dict(initial["result"]["observed_counters"])
        revoked = service.revoke_mandate(initial["run_id"])
        refused = service.attempt_execution(initial["run_id"])

    assert revoked["result"]["observed_counters"] == counters_before
    assert refused["result"]["observed_counters"] == counters_before
    assert "mandate_state_registry" not in str(refused)
    assert counters_before["openai_calls"] == 0


def test_n_revoked_version_cannot_reactivate_and_fresh_version_needs_fresh_capability(
    tmp_path,
) -> None:
    registry = InMemoryMandateStateRegistry()
    old_capability, authorization, scenario = _issue(registry, version=1)
    registry.revoke(
        old_capability.payload.mandate_id,
        1,
        revoked_at=SERVER_TIME + timedelta(seconds=1),
    )
    with pytest.raises(MandateStateTransitionError, match="cannot be reactivated"):
        registry.register_active(
            old_capability.payload.mandate_id,
            1,
            updated_at=SERVER_TIME + timedelta(seconds=2),
        )
    registry.register_active(
        old_capability.payload.mandate_id,
        2,
        updated_at=SERVER_TIME + timedelta(seconds=2),
    )
    fresh = issue_execution_authorization(
        authorization_result=authorization,
        authorization_scenario=scenario,
        semantic_evidence=None,
        semantic_verifier=None,
        issued_at=SERVER_TIME + timedelta(seconds=2),
        expires_at=CAPABILITY_EXPIRES_AT,
        decision_nonce="fresh_decision_nonce_123456",
        config=CONFIG,
        signer=_signer(),
        mandate_state_registry=registry,
        mandate_version=2,
    )
    assert isinstance(fresh, SignedExecutionAuthorization)
    client = RecordingClient()

    result = _execute(
        fresh,
        authorization,
        scenario,
        registry,
        SQLiteExecutionLedger(tmp_path / "fresh-version-ledger.sqlite3"),
        client,
        now=SERVER_TIME + timedelta(seconds=2),
    )

    assert isinstance(result, ExecutionReceipt)
    assert fresh.payload.mandate_version == 2
    assert fresh.payload.decision_nonce != old_capability.payload.decision_nonce
    assert len(client.calls) == 1


def test_o_revoked_after_allow_demo_stays_at_zero_provider_calls(tmp_path) -> None:
    with CommerceLabService(state_dir=tmp_path / "revocation-demo") as service:
        initial = service.run_sync(
            user_intent=REVOCATION_DEMO_PRESET["intent"],
            preset_id=REVOCATION_DEMO_PRESET["id"],
        )
        execution = initial["result"]["execution"]
        assert execution["status"] == "AUTHORIZED"
        assert execution["consent"]["status"] == "ACTIVE"
        assert execution["capability"]["signature_verified"] is True
        assert execution["razorpay_calls"] == 0

        revoked = service.revoke_mandate(initial["run_id"])
        assert revoked["result"]["execution"]["consent"]["status"] == "REVOKED"
        refused = service.attempt_execution(initial["run_id"])

    execution = refused["result"]["execution"]
    assert execution["status"] == "REJECTED_BEFORE_NETWORK"
    assert execution["reason"] == "MANDATE_REVOKED"
    assert execution["razorpay_calls"] == 0
    assert execution["external_network_calls"] == 0
    assert "Current consent no longer permits execution" in execution["consent"][
        "teaching"
    ]


@dataclass
class _BlockingClient:
    started: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    calls: list[RazorpayOrderRequest] = field(default_factory=list)

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        self.calls.append(request)
        self.started.set()
        assert self.release.wait(5)
        return RazorpayOrderResult(
            razorpay_order_id="order_serialized",
            amount=request.amount,
            currency=request.currency,
            receipt=request.receipt,
            status="created",
        )


def test_sqlite_guard_orders_concurrent_revocation_after_inflight_provider_call(
    tmp_path,
) -> None:
    path = tmp_path / "ordered-mandates.sqlite3"
    execution_registry = SQLiteMandateStateRegistry(path)
    revocation_registry = SQLiteMandateStateRegistry(path)
    capability, authorization, scenario = _issue(execution_registry)
    ledger = SQLiteExecutionLedger(tmp_path / "ordered-ledger.sqlite3")
    client = _BlockingClient()
    execution_results: list[object] = []
    revocation_finished = Event()

    execute_thread = Thread(
        target=lambda: execution_results.append(
            _execute(
                capability,
                authorization,
                scenario,
                execution_registry,
                ledger,
                client,
            )
        )
    )
    execute_thread.start()
    assert client.started.wait(5)

    def revoke() -> None:
        revocation_registry.revoke(
            capability.payload.mandate_id,
            1,
            revoked_at=SERVER_TIME + timedelta(seconds=1),
        )
        revocation_finished.set()

    revoke_thread = Thread(target=revoke)
    revoke_thread.start()
    sleep(0.05)
    assert not revocation_finished.is_set()
    client.release.set()
    execute_thread.join(timeout=5)
    revoke_thread.join(timeout=5)
    try:
        assert isinstance(execution_results[0], ExecutionReceipt)
        assert revocation_finished.is_set()
        assert revocation_registry.get_current(
            capability.payload.mandate_id
        ).status is MandateStatus.REVOKED
    finally:
        execution_registry.close()
        revocation_registry.close()


# ---------------------------------------------------------------------------
# Post-review hardening: per-run consent identity, controlled lock contention,
# trusted-storage corruption, and clock-domain consistency.
# ---------------------------------------------------------------------------


def _consent(snapshot) -> dict:
    return snapshot["result"]["execution"]["consent"]


def test_p_two_runs_of_one_intent_get_independent_revocable_mandates(
    tmp_path,
) -> None:
    """H-1: identical intent, one state directory, zero shared consent state."""

    state_dir = tmp_path / "cross-run"
    intent = REVOCATION_DEMO_PRESET["intent"]
    preset = REVOCATION_DEMO_PRESET["id"]
    service = CommerceLabService(state_dir=state_dir)
    try:
        run_a = service.run_sync(
            user_intent=intent, preset_id=preset, request_id="visitor_a_request"
        )
        run_b = service.run_sync(
            user_intent=intent, preset_id=preset, request_id="visitor_b_request"
        )
        consent_a, consent_b = _consent(run_a), _consent(run_b)

        # Distinct server-issued consent identities for the same sentence.
        assert consent_a["mandate_id"] != consent_b["mandate_id"]
        assert consent_a["status"] == consent_b["status"] == "ACTIVE"

        # Revoking A must not touch B at all.
        service.revoke_mandate(run_a["run_id"])
        assert _consent(service.get_run(run_a["run_id"]).snapshot())["status"] == (
            "REVOKED"
        )
        assert service.mandate_state_registry.get_current(
            consent_b["mandate_id"]
        ).status is MandateStatus.ACTIVE

        refused = service.attempt_execution(run_a["run_id"])["result"]["execution"]
        assert refused["status"] == "REJECTED_BEFORE_NETWORK"
        assert refused["reason"] == "MANDATE_REVOKED"
        assert refused["razorpay_calls"] == 0
        assert refused["external_network_calls"] == 0

        allowed = service.attempt_execution(run_b["run_id"])["result"]["execution"]
        assert allowed["status"] == "ORDER_CREATED"
        assert allowed["razorpay_calls"] == 1
        assert allowed["external_network_calls"] == 0

        # A third visitor is unaffected by either outcome.
        run_c = service.run_sync(
            user_intent=intent, preset_id=preset, request_id="visitor_c_request"
        )
        assert run_c["state"] == "COMPLETE"
        consent_c = _consent(run_c)
        assert consent_c["status"] == "ACTIVE"
        assert consent_c["mandate_version"] == 1
        assert consent_c["mandate_id"] not in {
            consent_a["mandate_id"],
            consent_b["mandate_id"],
        }
        identities = (
            consent_a["mandate_id"],
            consent_b["mandate_id"],
            consent_c["mandate_id"],
        )
    finally:
        service.close()

    reopened = CommerceLabService(state_dir=state_dir)
    try:
        statuses = [
            reopened.mandate_state_registry.get_current(mandate_id).status
            for mandate_id in identities
        ]
        assert statuses == [
            MandateStatus.REVOKED,
            MandateStatus.ACTIVE,
            MandateStatus.ACTIVE,
        ]
        for mandate_id in identities:
            assert reopened.mandate_state_registry.audit_events(mandate_id)
    finally:
        reopened.close()


def test_q_revocation_does_not_poison_later_runs_of_the_same_intent(tmp_path) -> None:
    """The offline semantic clock can no longer move mandate state backwards."""

    intent = REVOCATION_DEMO_PRESET["intent"]
    preset = REVOCATION_DEMO_PRESET["id"]
    with CommerceLabService(state_dir=tmp_path / "clock") as service:
        first = service.run_sync(
            user_intent=intent, preset_id=preset, request_id="clock_first_request"
        )
        service.revoke_mandate(first["run_id"])
        for index in range(3):
            later = service.run_sync(
                user_intent=intent,
                preset_id=preset,
                request_id=f"clock_later_request_{index}",
            )
            assert later["state"] == "COMPLETE", later["error"]
            assert _consent(later)["status"] == "ACTIVE"


def test_r_deferred_capability_survives_a_frozen_offline_semantic_clock(
    tmp_path,
) -> None:
    """Capability lifetime is measured on the trusted clock, not the demo clock."""

    moment = [SERVER_TIME]
    with CommerceLabService(
        state_dir=tmp_path / "lifetime", clock=lambda: moment[0]
    ) as service:
        # The offline demo freezes its semantic clock at construction. Simulate
        # a service that has been up far longer than a capability lifetime, then
        # run. Issuance must be stamped on the trusted clock, not on the frozen
        # construction-time clock, or the demo would answer CAPABILITY_EXPIRED
        # instead of MANDATE_REVOKED for every visitor after the first minutes.
        moment[0] = SERVER_TIME + timedelta(hours=6)
        run = service.run_sync(
            user_intent=REVOCATION_DEMO_PRESET["intent"],
            preset_id=REVOCATION_DEMO_PRESET["id"],
            request_id="lifetime_probe_request",
        )
        assert run["result"]["execution"]["status"] == "AUTHORIZED"
        assert run["result"]["execution"]["capability"]["expiry_valid"] is True
        service.revoke_mandate(run["run_id"])
        refused = service.attempt_execution(run["run_id"])["result"]["execution"]

    assert refused["reason"] == "MANDATE_REVOKED"
    assert refused["razorpay_calls"] == 0


def test_s_revocation_queues_behind_a_guarded_provider_call_and_then_commits(
    tmp_path,
) -> None:
    """M-5 case 1: provider returns inside the wait budget, revocation commits."""

    path = tmp_path / "queued-mandates.sqlite3"
    execution_registry = SQLiteMandateStateRegistry(path)
    revocation_registry = SQLiteMandateStateRegistry(path, lock_wait_seconds=10.0)
    capability, authorization, scenario = _issue(execution_registry)
    ledger = SQLiteExecutionLedger(tmp_path / "queued-ledger.sqlite3")
    client = _BlockingClient()
    outcomes: list[object] = []
    revocation: list[object] = []

    execute_thread = Thread(
        target=lambda: outcomes.append(
            _execute(
                capability, authorization, scenario, execution_registry, ledger, client
            )
        )
    )
    execute_thread.start()
    assert client.started.wait(5)

    def revoke() -> None:
        try:
            revocation.append(
                revocation_registry.revoke(
                    capability.payload.mandate_id,
                    1,
                    revoked_at=SERVER_TIME + timedelta(seconds=1),
                )
            )
        except BaseException as error:  # pragma: no cover - failure detail
            revocation.append(error)

    revoke_thread = Thread(target=revoke)
    revoke_thread.start()
    sleep(0.05)
    assert not revocation, "revocation must not interleave into the guarded section"
    client.release.set()
    execute_thread.join(timeout=10)
    revoke_thread.join(timeout=10)

    try:
        assert isinstance(outcomes[0], ExecutionReceipt)
        assert len(client.calls) == 1
        assert isinstance(revocation[0], MandateState)
        assert revocation[0].status is MandateStatus.REVOKED
    finally:
        execution_registry.close()
        revocation_registry.close()
        ledger.close()


def test_t_revocation_past_the_wait_budget_is_controlled_not_opaque(tmp_path) -> None:
    """M-5 case 2: budget exhausted -> typed BUSY, no cancellation, no retry."""

    path = tmp_path / "busy-mandates.sqlite3"
    execution_registry = SQLiteMandateStateRegistry(path)
    # A deliberately tiny budget stands in for "provider outlasted the budget".
    revocation_registry = SQLiteMandateStateRegistry(path, lock_wait_seconds=0.25)
    capability, authorization, scenario = _issue(execution_registry)
    ledger = SQLiteExecutionLedger(tmp_path / "busy-ledger.sqlite3")
    client = _BlockingClient()
    outcomes: list[object] = []

    execute_thread = Thread(
        target=lambda: outcomes.append(
            _execute(
                capability, authorization, scenario, execution_registry, ledger, client
            )
        )
    )
    execute_thread.start()
    assert client.started.wait(5)

    with pytest.raises(MandateStateBusyError):
        revocation_registry.revoke(
            capability.payload.mandate_id,
            1,
            revoked_at=SERVER_TIME + timedelta(seconds=1),
        )
    # The guarded provider call is untouched: not cancelled, not duplicated.
    assert len(client.calls) == 1
    client.release.set()
    execute_thread.join(timeout=10)

    try:
        assert isinstance(outcomes[0], ExecutionReceipt)
        assert len(client.calls) == 1
        # Nothing was committed by the refused revocation.
        assert revocation_registry.get_current(
            capability.payload.mandate_id
        ).status is MandateStatus.ACTIVE
        # And the registry is still usable once the guard has been released.
        assert revocation_registry.revoke(
            capability.payload.mandate_id,
            1,
            revoked_at=SERVER_TIME + timedelta(seconds=2),
        ).status is MandateStatus.REVOKED
    finally:
        execution_registry.close()
        revocation_registry.close()
        ledger.close()


def test_u_execution_blocked_on_the_guard_fails_closed_without_provider_io(
    tmp_path,
) -> None:
    """Lock failure on the execution side must never fail open."""

    path = tmp_path / "blocked-mandates.sqlite3"
    registry = SQLiteMandateStateRegistry(path, lock_wait_seconds=0.25)
    capability, authorization, scenario = _issue(registry)
    ledger = SQLiteExecutionLedger(tmp_path / "blocked-ledger.sqlite3")
    client = RecordingClient()
    blocker = sqlite3.connect(str(path), timeout=0.25, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(MandateStateBusyError):
            _execute(capability, authorization, scenario, registry, ledger, client)
        assert client.calls == []
        assert ledger.get(DECISION_NONCE) is None
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        registry.close()
        ledger.close()


def _corrupt(path, *statements) -> None:
    raw = sqlite3.connect(str(path))
    try:
        for statement in statements:
            raw.execute(statement)
        raw.commit()
    finally:
        raw.close()


@pytest.mark.parametrize(
    "label,statements",
    [
        (
            "pointer references a missing state row",
            ("UPDATE mandate_current SET version = 8",),
        ),
        (
            "two ACTIVE versions for one mandate",
            (
                "INSERT INTO mandate_states VALUES ("
                "(SELECT mandate_id FROM mandate_current), 2, 'ACTIVE',"
                " '2026-01-01T00:00:00.000000+00:00', NULL, NULL)",
            ),
        ),
        (
            "an ACTIVE version newer than the pointer",
            (
                "INSERT INTO mandate_states VALUES ("
                "(SELECT mandate_id FROM mandate_current), 5, 'SUPERSEDED',"
                " '2026-01-01T00:00:00.000000+00:00', NULL, 9)",
            ),
        ),
    ],
)
def test_v_registry_corruption_fails_closed_before_any_provider_call(
    tmp_path, label, statements
) -> None:
    """M-4: impossible trusted state is rejected, never silently reinterpreted."""

    path = tmp_path / f"corrupt-{abs(hash(label))}.sqlite3"
    registry = SQLiteMandateStateRegistry(path)
    capability, authorization, scenario = _issue(registry)
    ledger = SQLiteExecutionLedger(tmp_path / f"corrupt-ledger-{abs(hash(label))}.sqlite3")
    registry.close()
    _corrupt(path, *statements)

    registry = SQLiteMandateStateRegistry(path)
    client = RecordingClient()
    try:
        with pytest.raises(MandateStateCorruptionError):
            registry.get_current(capability.payload.mandate_id)

        result = _execute(
            capability, authorization, scenario, registry, ledger, client
        )
        assert result == ExecutionRefusal(
            ExecutionRefusalReason.MANDATE_STATE_CORRUPT
        )
        assert client.calls == []
        # The capability is consumed, so repairing storage cannot revive it.
        assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.REJECTED
        events = registry.audit_events(capability.payload.mandate_id)
        assert events[-1]["event"] == "EXECUTION_REFUSED_MANDATE_STATE"
        assert events[-1]["reason"] == "MANDATE_STATE_CORRUPT"
        assert events[-1]["current_status"] is None
    finally:
        registry.close()
        ledger.close()


def test_w_resurrecting_revoked_state_cannot_revive_a_consumed_capability(
    tmp_path,
) -> None:
    """Re-run of the hostile resurrection probe: the burnt nonce is the backstop."""

    path = tmp_path / "resurrect-mandates.sqlite3"
    ledger = SQLiteExecutionLedger(tmp_path / "resurrect-ledger.sqlite3")
    registry = SQLiteMandateStateRegistry(path)
    capability, authorization, scenario = _issue(registry)
    registry.revoke(
        capability.payload.mandate_id, 1, revoked_at=SERVER_TIME + timedelta(seconds=1)
    )
    client = RecordingClient()
    first = _execute(capability, authorization, scenario, registry, ledger, client)
    assert first == ExecutionRefusal(ExecutionRefusalReason.MANDATE_REVOKED)
    assert client.calls == []
    registry.close()

    # Force trusted storage back to ACTIVE outside every API transition.
    _corrupt(path, "UPDATE mandate_states SET status='ACTIVE', revoked_at=NULL")
    revived = SQLiteMandateStateRegistry(path)
    try:
        assert revived.get_current(
            capability.payload.mandate_id
        ).status is MandateStatus.ACTIVE
        replay = _execute(
            capability, authorization, scenario, revived, ledger, client
        )
        assert replay == ExecutionRefusal(ExecutionRefusalReason.NONCE_ALREADY_USED)
        assert client.calls == []
        assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.REJECTED
    finally:
        revived.close()
        ledger.close()


def test_x_audit_records_missing_state_without_fabricating_an_active_status(
    tmp_path,
) -> None:
    """Provenance: a version that never existed is recorded as observed-missing."""

    for registry in (
        InMemoryMandateStateRegistry(),
        SQLiteMandateStateRegistry(tmp_path / "observed.sqlite3"),
    ):
        mandate_id = "0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d"
        digest = "b" * 64
        registry.record_execution_refusal(
            mandate_id=mandate_id,
            version=7,
            occurred_at=SERVER_TIME,
            reason="MANDATE_STATE_MISSING",
            decision_nonce="observed_nonce_0001",
            execution_request_sha256=digest,
            authorization_result_sha256=digest,
        )
        event = registry.audit_events(mandate_id)[-1]
        assert event["mandate_version"] == 7
        assert event["previous_status"] is None
        assert event["current_status"] is None
        assert event["reason"] == "MANDATE_STATE_MISSING"
        # No state row was invented to carry the identity into the log.
        assert registry.get_version(mandate_id, 7) is None
        assert registry.get_current(mandate_id) is None
        if hasattr(registry, "close"):
            registry.close()
