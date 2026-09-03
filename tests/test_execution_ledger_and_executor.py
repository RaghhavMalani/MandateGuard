from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from mandateguard.execution import (
    ExecutionError,
    ExecutionFailureReason,
    ExecutionLedgerStatus,
    ExecutionReceipt,
    ExecutionRefusal,
    ExecutionRefusalReason,
    HMACSHA256Verifier,
    InMemoryMandateStateRegistry,
    RazorpayTestOrdersAdapter,
    SQLiteExecutionLedger,
    build_razorpay_order_request,
    execute_razorpay_order,
)
from mandateguard.execution.razorpay import (
    HTTPResponse,
    RazorpayAmbiguousTransportError,
    RazorpayProviderRejection,
)
from tests.execution_factories import (
    CONFIG,
    DECISION_NONCE,
    SIGNING_KEY_ID,
    SYNTHETIC_SIGNING_KEY,
    RecordingClient,
    make_signed_allow,
)
from tests.factories import SERVER_TIME


def _verifier() -> HMACSHA256Verifier:
    return HMACSHA256Verifier({SIGNING_KEY_ID: SYNTHETIC_SIGNING_KEY})


def _execute(capability, result, mandate, transaction, ledger, client):
    mandate_state_registry = InMemoryMandateStateRegistry()
    mandate_state_registry.register_active(
        mandate.payload.mandate_id,
        capability.payload.mandate_version,
        updated_at=SERVER_TIME,
    )
    return execute_razorpay_order(
        authorization=capability,
        authorization_result=result,
        mandate=mandate,
        transaction=transaction,
        now=SERVER_TIME,
        config=CONFIG,
        verifier=_verifier(),
        ledger=ledger,
        client=client,
        mandate_state_registry=mandate_state_registry,
    )


def test_nonce_reservation_is_atomic_and_single_use(tmp_path) -> None:
    ledger = SQLiteExecutionLedger(tmp_path / "ledger.sqlite3")

    assert ledger.reserve(DECISION_NONCE, "a" * 64) is True
    assert ledger.reserve(DECISION_NONCE, "a" * 64) is False
    assert ledger.reserve(DECISION_NONCE, "b" * 64) is False
    assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.RESERVED


def test_nonce_usage_survives_process_style_reopen(tmp_path) -> None:
    path = tmp_path / "persistent-ledger.sqlite3"
    first = SQLiteExecutionLedger(path)
    assert first.reserve(DECISION_NONCE, "a" * 64) is True
    first.close()

    reopened = SQLiteExecutionLedger(path)

    assert reopened.reserve(DECISION_NONCE, "a" * 64) is False
    assert reopened.get(DECISION_NONCE).status is ExecutionLedgerStatus.RESERVED


def test_provider_success_transitions_to_succeeded(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    ledger = SQLiteExecutionLedger(tmp_path / "success.sqlite3")
    client = RecordingClient()

    outcome = _execute(
        capability, result, mandate, transaction, ledger, client
    )

    assert isinstance(outcome, ExecutionReceipt)
    assert outcome.execution_request_sha256 == capability.payload.execution_request_sha256
    assert outcome.razorpay_order_id == "order_synthetic_result"
    assert outcome.status == "created"
    record = ledger.get(DECISION_NONCE)
    assert record.status is ExecutionLedgerStatus.SUCCEEDED
    assert record.razorpay_order_id == outcome.razorpay_order_id
    assert len(client.calls) == 1
    assert client.calls[0] == build_razorpay_order_request(
        transaction, capability.payload.decision_nonce
    )


def test_provider_definite_rejection_transitions_to_rejected(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    ledger = SQLiteExecutionLedger(tmp_path / "rejected.sqlite3")
    client = RecordingClient(
        exception=RazorpayProviderRejection("synthetic provider rejection")
    )

    with pytest.raises(ExecutionError) as caught:
        _execute(capability, result, mandate, transaction, ledger, client)

    assert caught.value.reason is ExecutionFailureReason.PROVIDER_REJECTED
    assert caught.value.ledger_status is ExecutionLedgerStatus.REJECTED
    assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.REJECTED
    assert len(client.calls) == 1


def test_ambiguous_timeout_transitions_to_uncertain_and_never_retries(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    ledger = SQLiteExecutionLedger(tmp_path / "uncertain.sqlite3")
    client = RecordingClient(
        exception=RazorpayAmbiguousTransportError("synthetic timeout")
    )

    with pytest.raises(ExecutionError) as caught:
        _execute(capability, result, mandate, transaction, ledger, client)

    assert caught.value.reason is ExecutionFailureReason.OUTCOME_UNCERTAIN
    assert caught.value.ledger_status is ExecutionLedgerStatus.UNCERTAIN
    assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.UNCERTAIN
    assert len(client.calls) == 1

    second = _execute(capability, result, mandate, transaction, ledger, client)

    assert second == ExecutionRefusal(ExecutionRefusalReason.NONCE_ALREADY_USED)
    assert len(client.calls) == 1


@dataclass
class MalformedSuccessTransport:
    calls: list[bytes] = field(default_factory=list)

    def post_json(self, *, path, headers, body, timeout_seconds):
        self.calls.append(body)
        request = json.loads(body)
        response = {
            "entity": "order",
            "id": "order_malformed_success",
            "amount": request["amount"] + 1,
            "currency": request["currency"],
            "receipt": request["receipt"],
            "status": "created",
        }
        return HTTPResponse(status_code=200, body=json.dumps(response).encode())


def test_malformed_success_is_uncertain_not_successful(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    ledger = SQLiteExecutionLedger(tmp_path / "malformed.sqlite3")
    transport = MalformedSuccessTransport()
    client = RazorpayTestOrdersAdapter(
        key_id="rzp_test_synthetic",
        key_secret="synthetic-non-secret",
        transport=transport,
    )

    with pytest.raises(ExecutionError) as caught:
        _execute(capability, result, mandate, transaction, ledger, client)

    assert caught.value.reason is ExecutionFailureReason.OUTCOME_UNCERTAIN
    assert ledger.get(DECISION_NONCE).status is ExecutionLedgerStatus.UNCERTAIN
    assert len(transport.calls) == 1
