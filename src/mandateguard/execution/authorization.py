"""Issuance of short-lived execution capabilities from frozen authorization results."""

from __future__ import annotations

from datetime import datetime

from mandateguard.core.hashing import (
    mandate_payload_sha256,
    sha256_canonical,
    transaction_body_sha256,
)
from mandateguard.execution.models import (
    EXECUTION_SCHEMA_VERSION,
    MAX_CAPABILITY_LIFETIME,
    ExecutionAuthorizationPayload,
    ExecutionRefusal,
    ExecutionRefusalReason,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
)
from mandateguard.execution.request import (
    build_razorpay_order_request,
    execution_request_sha256,
)
from mandateguard.execution.signing import ExecutionSigner
from mandateguard.models.decision import DecisionAction
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction
from mandateguard.semantic.models import AuthorizationResult


def authorization_result_sha256(result: AuthorizationResult) -> str:
    """Commit the exact, existing D5 AuthorizationResult used for D6 issuance."""

    if not isinstance(result, AuthorizationResult):
        raise TypeError("result must be AuthorizationResult")
    return sha256_canonical(result)


def issue_execution_authorization(
    *,
    authorization_result: AuthorizationResult,
    mandate: Mandate,
    transaction: Transaction,
    issued_at: datetime,
    expires_at: datetime,
    decision_nonce: str,
    config: TrustedExecutionConfig,
    signer: ExecutionSigner,
) -> SignedExecutionAuthorization | ExecutionRefusal:
    """Issue only an ALLOW capability; BLOCK and REVIEW produce typed refusals."""

    if not isinstance(authorization_result, AuthorizationResult):
        raise TypeError("authorization_result must be AuthorizationResult")
    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")
    if not isinstance(config, TrustedExecutionConfig):
        raise TypeError("config must be TrustedExecutionConfig")

    if authorization_result.final_action is DecisionAction.BLOCK:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_BLOCKED)
    if authorization_result.final_action is DecisionAction.REVIEW:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_REVIEW_REQUIRED)

    current_transaction_sha256 = transaction_body_sha256(transaction)
    if (
        authorization_result.deterministic_decision.transaction_sha256
        != current_transaction_sha256
    ):
        return ExecutionRefusal(ExecutionRefusalReason.TRANSACTION_HASH_MISMATCH)
    if transaction.payload.merchant_id != config.merchant_id:
        return ExecutionRefusal(ExecutionRefusalReason.MERCHANT_MISMATCH)

    for value in (issued_at, expires_at):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            return ExecutionRefusal(ExecutionRefusalReason.INVALID_CAPABILITY_LIFETIME)
    if (
        issued_at >= expires_at
        or expires_at - issued_at > MAX_CAPABILITY_LIFETIME
        or expires_at > mandate.payload.expires_at
    ):
        return ExecutionRefusal(ExecutionRefusalReason.INVALID_CAPABILITY_LIFETIME)

    request = build_razorpay_order_request(transaction, decision_nonce)
    semantic_decision = authorization_result.semantic_decision
    payload = ExecutionAuthorizationPayload(
        schema_version=EXECUTION_SCHEMA_VERSION,
        decision_nonce=decision_nonce,
        action=DecisionAction.ALLOW,
        issued_at=issued_at,
        expires_at=expires_at,
        environment=config.environment,
        audience=config.audience,
        account_scope=config.account_scope,
        merchant_id=config.merchant_id,
        mandate_payload_sha256=mandate_payload_sha256(mandate),
        transaction_body_sha256=current_transaction_sha256,
        authorization_result_sha256=authorization_result_sha256(
            authorization_result
        ),
        execution_request_sha256=execution_request_sha256(request),
        semantic_input_sha256=(
            semantic_decision.semantic_input_sha256
            if semantic_decision is not None
            else None
        ),
        semantic_output_sha256=(
            semantic_decision.semantic_output_sha256
            if semantic_decision is not None
            else None
        ),
    )
    return signer.sign(payload)
