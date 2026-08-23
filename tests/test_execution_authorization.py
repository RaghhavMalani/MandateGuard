from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from mandateguard.core.hashing import transaction_body_sha256
from mandateguard.execution import (
    ExecutionRefusal,
    ExecutionRefusalReason,
    HMACSHA256Signer,
    HMACSHA256Verifier,
    RazorpayOrderRequest,
    SQLiteExecutionLedger,
    SignedExecutionAuthorization,
    execution_request_sha256,
    issue_execution_authorization,
    receipt_for_decision_nonce,
    validate_and_reserve_execution,
)
from mandateguard.models.decision import DecisionAction
from tests.execution_factories import (
    CAPABILITY_EXPIRES_AT,
    CONFIG,
    DECISION_NONCE,
    SIGNING_KEY_ID,
    SYNTHETIC_SIGNING_KEY,
    make_authorization,
    make_signed_allow,
)
from tests.factories import SERVER_TIME, make_line, make_payload, make_transaction


def _signer() -> HMACSHA256Signer:
    return HMACSHA256Signer(
        key_id=SIGNING_KEY_ID, key=SYNTHETIC_SIGNING_KEY
    )


def _verifier() -> HMACSHA256Verifier:
    return HMACSHA256Verifier({SIGNING_KEY_ID: SYNTHETIC_SIGNING_KEY})


def _gate(capability, result, mandate, transaction, tmp_path, *, now=SERVER_TIME):
    ledger = SQLiteExecutionLedger(tmp_path / "execution.sqlite3")
    outcome = validate_and_reserve_execution(
        authorization=capability,
        authorization_result=result,
        mandate=mandate,
        transaction=transaction,
        now=now,
        config=CONFIG,
        verifier=_verifier(),
        ledger=ledger,
    )
    return outcome, ledger


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        (DecisionAction.BLOCK, ExecutionRefusalReason.AUTHORIZATION_BLOCKED),
        (
            DecisionAction.REVIEW,
            ExecutionRefusalReason.AUTHORIZATION_REVIEW_REQUIRED,
        ),
    ],
)
def test_non_allow_decisions_return_refusal_and_no_capability(action, reason) -> None:
    result, scenario = make_authorization(action)

    outcome = issue_execution_authorization(
        authorization_result=result,
        authorization_scenario=scenario,
        semantic_evidence=None,
        semantic_verifier=None,
        issued_at=SERVER_TIME,
        expires_at=CAPABILITY_EXPIRES_AT,
        decision_nonce=DECISION_NONCE,
        config=CONFIG,
        signer=_signer(),
    )

    assert outcome == ExecutionRefusal(reason)
    assert not isinstance(outcome, SignedExecutionAuthorization)


def test_allow_decision_issues_signed_capability() -> None:
    capability, result, mandate, transaction = make_signed_allow()

    assert capability.payload.action is DecisionAction.ALLOW
    assert capability.payload.transaction_body_sha256 == transaction_body_sha256(
        transaction
    )
    assert capability.payload.merchant_id == mandate.payload.constraints.hard.merchant_allowlist[0]
    assert result.final_action is DecisionAction.ALLOW
    assert len(capability.signature) == 32


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (SERVER_TIME, SERVER_TIME),
        (SERVER_TIME + timedelta(seconds=1), SERVER_TIME),
        (SERVER_TIME, SERVER_TIME + timedelta(minutes=5, microseconds=1)),
    ],
)
def test_invalid_capability_lifetime_returns_typed_refusal(
    issued_at, expires_at
) -> None:
    result, scenario = make_authorization()

    outcome = issue_execution_authorization(
        authorization_result=result,
        authorization_scenario=scenario,
        semantic_evidence=None,
        semantic_verifier=None,
        issued_at=issued_at,
        expires_at=expires_at,
        decision_nonce=DECISION_NONCE,
        config=CONFIG,
        signer=_signer(),
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.INVALID_CAPABILITY_LIFETIME
    )


def test_valid_signature_is_accepted_and_nonce_is_reserved(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()

    outcome, ledger = _gate(
        capability, result, mandate, transaction, tmp_path
    )

    assert not isinstance(outcome, ExecutionRefusal)
    assert outcome.request.amount == transaction.payload.declared_order_total_minor
    assert ledger.get(DECISION_NONCE).status.value == "RESERVED"


def test_modified_signature_is_rejected_before_reservation(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    changed = bytes([capability.signature[0] ^ 1]) + capability.signature[1:]

    outcome, ledger = _gate(
        replace(capability, signature=changed),
        result,
        mandate,
        transaction,
        tmp_path,
    )

    assert outcome == ExecutionRefusal(ExecutionRefusalReason.SIGNATURE_INVALID)
    assert ledger.get(DECISION_NONCE) is None


def test_unknown_key_is_rejected_before_reservation(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()

    outcome, ledger = _gate(
        replace(capability, key_id="unknown-signing-key"),
        result,
        mandate,
        transaction,
        tmp_path,
    )

    assert outcome == ExecutionRefusal(ExecutionRefusalReason.UNKNOWN_SIGNING_KEY)
    assert ledger.get(DECISION_NONCE) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda p: replace(p, transaction_body_sha256="0" * 64),
        lambda p: replace(p, execution_request_sha256="0" * 64),
        lambda p: replace(p, mandate_payload_sha256="0" * 64),
        lambda p: replace(p, authorization_result_sha256="0" * 64),
        lambda p: replace(p, merchant_id="merchant-2"),
        lambda p: replace(p, account_scope="another-test-account"),
        lambda p: replace(p, environment="LIVE"),
        lambda p: replace(p, audience="another-audience"),
        lambda p: replace(p, decision_nonce="changed_nonce_123456"),
        lambda p: replace(p, issued_at=p.issued_at + timedelta(seconds=1)),
        lambda p: replace(p, expires_at=p.expires_at - timedelta(seconds=1)),
        lambda p: replace(
            p,
            semantic_input_sha256="1" * 64,
            semantic_output_sha256="2" * 64,
        ),
    ],
)
def test_every_modified_payload_field_invalidates_signature(change, tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    tampered = replace(capability, payload=change(capability.payload))

    outcome, ledger = _gate(
        tampered, result, mandate, transaction, tmp_path
    )

    assert outcome == ExecutionRefusal(ExecutionRefusalReason.SIGNATURE_INVALID)
    assert ledger.get(DECISION_NONCE) is None


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("environment", "LIVE", ExecutionRefusalReason.ENVIRONMENT_MISMATCH),
        ("audience", "other-orders", ExecutionRefusalReason.AUDIENCE_MISMATCH),
        (
            "account_scope",
            "other-test-account",
            ExecutionRefusalReason.ACCOUNT_SCOPE_MISMATCH,
        ),
        ("merchant_id", "merchant-2", ExecutionRefusalReason.MERCHANT_MISMATCH),
    ],
)
def test_validly_signed_wrong_scope_is_rejected(
    field, value, reason, tmp_path
) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    payload = replace(capability.payload, **{field: value})
    wrong_scope = _signer().sign(payload)

    outcome, ledger = _gate(
        wrong_scope, result, mandate, transaction, tmp_path
    )

    assert outcome == ExecutionRefusal(reason)
    assert ledger.get(DECISION_NONCE) is None


@pytest.mark.parametrize("action", [DecisionAction.BLOCK, DecisionAction.REVIEW])
def test_non_allow_execution_payload_cannot_be_constructed_or_signed(action) -> None:
    capability, _result, _mandate, _transaction = make_signed_allow()

    with pytest.raises(ValueError, match="action must be ALLOW"):
        invalid_payload = replace(capability.payload, action=action)
        _signer().sign(invalid_payload)


@pytest.mark.parametrize(
    ("now", "reason"),
    [
        (
            CAPABILITY_EXPIRES_AT,
            ExecutionRefusalReason.CAPABILITY_EXPIRED,
        ),
        (
            CAPABILITY_EXPIRES_AT + timedelta(microseconds=1),
            ExecutionRefusalReason.CAPABILITY_EXPIRED,
        ),
    ],
)
def test_expiry_and_exact_boundary_are_rejected(now, reason, tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()

    outcome, ledger = _gate(
        capability, result, mandate, transaction, tmp_path, now=now
    )

    assert outcome == ExecutionRefusal(reason)
    assert ledger.get(DECISION_NONCE) is None


def test_materially_future_issued_capability_is_rejected(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    payload = replace(
        capability.payload,
        issued_at=SERVER_TIME + timedelta(seconds=31),
        expires_at=SERVER_TIME + timedelta(minutes=3),
    )
    future_capability = _signer().sign(payload)

    outcome, ledger = _gate(
        future_capability, result, mandate, transaction, tmp_path
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.CAPABILITY_NOT_YET_VALID
    )
    assert ledger.get(DECISION_NONCE) is None


def test_mandate_mutation_is_rejected(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    changed_hard = replace(
        mandate.payload.constraints.hard,
        max_total_minor=mandate.payload.constraints.hard.max_total_minor + 1,
    )
    changed_mandate = replace(
        mandate,
        payload=replace(
            mandate.payload,
            constraints=replace(mandate.payload.constraints, hard=changed_hard),
        ),
    )

    outcome, ledger = _gate(
        capability, result, changed_mandate, transaction, tmp_path
    )

    assert outcome == ExecutionRefusal(ExecutionRefusalReason.MANDATE_HASH_MISMATCH)
    assert ledger.get(DECISION_NONCE) is None


def test_different_authorization_result_is_rejected(tmp_path) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    changed_result = replace(
        result,
        deterministic_decision=replace(
            result.deterministic_decision,
            replay_seed=result.deterministic_decision.replay_seed + 1,
        ),
    )

    outcome, ledger = _gate(
        capability, changed_result, mandate, transaction, tmp_path
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_RESULT_HASH_MISMATCH
    )
    assert ledger.get(DECISION_NONCE) is None


def _mutated_transactions():
    original_line = make_line()
    cases = {
        "declared-order-total": make_payload(declared_order_total_minor=10_001),
        "order-currency": make_payload(order_currency="USD"),
        "quantity": make_payload(lines=(replace(original_line, quantity=2),)),
        "line-total": make_payload(
            lines=(replace(original_line, line_total_minor=10_001),)
        ),
        "effective-unit-price": make_payload(
            lines=(replace(original_line, effective_unit_price_minor=10_001),)
        ),
        "sku": make_payload(lines=(replace(original_line, sku="sku-2"),)),
        "merchant": make_payload(merchant_id="merchant-2"),
    }
    return [(name, make_transaction(payload=payload)) for name, payload in cases.items()]


@pytest.mark.parametrize(
    ("_name", "changed_transaction"), _mutated_transactions()
)
def test_transaction_mutations_refuse_before_reservation(
    _name, changed_transaction, tmp_path
) -> None:
    capability, result, mandate, transaction = make_signed_allow()

    outcome, ledger = _gate(
        capability, result, mandate, changed_transaction, tmp_path
    )

    expected = (
        ExecutionRefusalReason.MERCHANT_MISMATCH
        if changed_transaction.payload.merchant_id != transaction.payload.merchant_id
        else ExecutionRefusalReason.TRANSACTION_HASH_MISMATCH
    )
    assert outcome == ExecutionRefusal(expected)
    assert ledger.get(DECISION_NONCE) is None


@pytest.mark.parametrize(
    "mutated_request",
    [
        RazorpayOrderRequest(
            amount=10_001,
            currency="INR",
            receipt=receipt_for_decision_nonce(DECISION_NONCE),
        ),
        RazorpayOrderRequest(
            amount=10_000,
            currency="USD",
            receipt=receipt_for_decision_nonce(DECISION_NONCE),
        ),
        RazorpayOrderRequest(amount=10_000, currency="INR", receipt="mg_mutated"),
    ],
)
def test_signed_hash_for_mutated_request_cannot_change_outbound_request(
    mutated_request, tmp_path
) -> None:
    capability, result, mandate, transaction = make_signed_allow()
    changed_hash = execution_request_sha256(mutated_request)
    mutated_capability = _signer().sign(
        replace(capability.payload, execution_request_sha256=changed_hash)
    )

    outcome, ledger = _gate(
        mutated_capability, result, mandate, transaction, tmp_path
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.EXECUTION_REQUEST_HASH_MISMATCH
    )
    assert ledger.get(DECISION_NONCE) is None
