"""Execution gate that validates, rebuilds, rehashes, and reserves before I/O."""

from __future__ import annotations

from datetime import datetime

from mandateguard.core.hashing import mandate_payload_sha256, transaction_body_sha256
from mandateguard.execution.authorization import authorization_result_sha256
from mandateguard.execution.ledger import ExecutionLedger
from mandateguard.execution.models import (
    MAX_ISSUED_AT_FUTURE_SKEW,
    ExecutionRefusal,
    ExecutionRefusalReason,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
    ValidatedExecutionGrant,
)
from mandateguard.execution.request import (
    build_razorpay_order_request,
    execution_request_sha256,
)
from mandateguard.execution.signing import (
    ExecutionVerifier,
    SignatureVerification,
)
from mandateguard.models.decision import DecisionAction
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction
from mandateguard.semantic.models import AuthorizationResult


def validate_and_reserve_execution(
    *,
    authorization: SignedExecutionAuthorization,
    authorization_result: AuthorizationResult,
    mandate: Mandate,
    transaction: Transaction,
    now: datetime,
    config: TrustedExecutionConfig,
    verifier: ExecutionVerifier,
    ledger: ExecutionLedger,
) -> ValidatedExecutionGrant | ExecutionRefusal:
    """Return a reserved grant only after every signed commitment is recomputed."""

    if not isinstance(authorization, SignedExecutionAuthorization):
        raise TypeError("authorization must be SignedExecutionAuthorization")
    if not isinstance(authorization_result, AuthorizationResult):
        raise TypeError("authorization_result must be AuthorizationResult")
    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("now must be a timezone-aware injected datetime")
    if not isinstance(config, TrustedExecutionConfig):
        raise TypeError("config must be TrustedExecutionConfig")

    verification = verifier.verify(authorization)
    if verification is SignatureVerification.UNKNOWN_KEY:
        return ExecutionRefusal(ExecutionRefusalReason.UNKNOWN_SIGNING_KEY)
    if verification is not SignatureVerification.VALID:
        return ExecutionRefusal(ExecutionRefusalReason.SIGNATURE_INVALID)

    payload = authorization.payload
    if payload.action is DecisionAction.BLOCK:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_BLOCKED)
    if payload.action is DecisionAction.REVIEW:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_REVIEW_REQUIRED)
    if authorization_result.final_action is DecisionAction.BLOCK:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_BLOCKED)
    if authorization_result.final_action is DecisionAction.REVIEW:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_REVIEW_REQUIRED)

    if now >= payload.expires_at:
        return ExecutionRefusal(ExecutionRefusalReason.CAPABILITY_EXPIRED)
    if payload.issued_at > now + MAX_ISSUED_AT_FUTURE_SKEW:
        return ExecutionRefusal(ExecutionRefusalReason.CAPABILITY_NOT_YET_VALID)
    if payload.environment != config.environment:
        return ExecutionRefusal(ExecutionRefusalReason.ENVIRONMENT_MISMATCH)
    if payload.audience != config.audience:
        return ExecutionRefusal(ExecutionRefusalReason.AUDIENCE_MISMATCH)
    if payload.account_scope != config.account_scope:
        return ExecutionRefusal(ExecutionRefusalReason.ACCOUNT_SCOPE_MISMATCH)
    if payload.merchant_id != config.merchant_id:
        return ExecutionRefusal(ExecutionRefusalReason.MERCHANT_MISMATCH)
    if transaction.payload.merchant_id != config.merchant_id:
        return ExecutionRefusal(ExecutionRefusalReason.MERCHANT_MISMATCH)

    if mandate_payload_sha256(mandate) != payload.mandate_payload_sha256:
        return ExecutionRefusal(ExecutionRefusalReason.MANDATE_HASH_MISMATCH)
    if (
        authorization_result_sha256(authorization_result)
        != payload.authorization_result_sha256
    ):
        return ExecutionRefusal(
            ExecutionRefusalReason.AUTHORIZATION_RESULT_HASH_MISMATCH
        )

    current_transaction_sha256 = transaction_body_sha256(transaction)
    if current_transaction_sha256 != payload.transaction_body_sha256:
        return ExecutionRefusal(ExecutionRefusalReason.TRANSACTION_HASH_MISMATCH)

    rebuilt_request = build_razorpay_order_request(
        transaction, payload.decision_nonce
    )
    rebuilt_request_sha256 = execution_request_sha256(rebuilt_request)
    if rebuilt_request_sha256 != payload.execution_request_sha256:
        return ExecutionRefusal(
            ExecutionRefusalReason.EXECUTION_REQUEST_HASH_MISMATCH
        )

    if not ledger.reserve(payload.decision_nonce, rebuilt_request_sha256):
        return ExecutionRefusal(ExecutionRefusalReason.NONCE_ALREADY_USED)
    return ValidatedExecutionGrant(
        decision_nonce=payload.decision_nonce,
        execution_request_sha256=rebuilt_request_sha256,
        request=rebuilt_request,
    )
