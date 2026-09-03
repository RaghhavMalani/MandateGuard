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
from mandateguard.execution.mandate_state import (
    MandateStateRegistry,
    MandateStatus,
)
from mandateguard.execution.request import (
    build_razorpay_order_request,
    execution_request_sha256,
)
from mandateguard.execution.signing import ExecutionSigner
from mandateguard.models.decision import DecisionAction
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.cache import SemanticCacheError
from mandateguard.semantic.evidence import SemanticEvidence
from mandateguard.semantic.models import AuthorizationResult
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier


def authorization_result_sha256(result: AuthorizationResult) -> str:
    """Commit the exact, existing D5 AuthorizationResult used for D6 issuance."""

    if not isinstance(result, AuthorizationResult):
        raise TypeError("result must be AuthorizationResult")
    return sha256_canonical(result)


def issue_execution_authorization(
    *,
    authorization_result: AuthorizationResult,
    authorization_scenario: ReplayScenario,
    semantic_evidence: SemanticEvidence | None,
    semantic_verifier: SemanticVerifier | None,
    issued_at: datetime,
    expires_at: datetime,
    decision_nonce: str,
    config: TrustedExecutionConfig,
    signer: ExecutionSigner,
    mandate_state_registry: MandateStateRegistry,
    mandate_version: int = 1,
) -> SignedExecutionAuthorization | ExecutionRefusal:
    """Issue only an ALLOW capability; BLOCK and REVIEW produce typed refusals."""

    if not isinstance(authorization_result, AuthorizationResult):
        raise TypeError("authorization_result must be AuthorizationResult")
    if not isinstance(authorization_scenario, ReplayScenario):
        raise TypeError("authorization_scenario must be ReplayScenario")
    if not isinstance(config, TrustedExecutionConfig):
        raise TypeError("config must be TrustedExecutionConfig")
    if not callable(getattr(mandate_state_registry, "get_current", None)):
        raise TypeError("mandate_state_registry must be trusted server state")
    if (
        isinstance(mandate_version, bool)
        or not isinstance(mandate_version, int)
        or mandate_version < 1
    ):
        raise ValueError("mandate_version must be a positive integer")

    if authorization_result.final_action is DecisionAction.BLOCK:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_BLOCKED)
    if authorization_result.final_action is DecisionAction.REVIEW:
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_REVIEW_REQUIRED)

    mandate = authorization_scenario.mandate
    transaction = authorization_scenario.transaction
    semantic_constraints_present = bool(mandate.payload.constraints.semantic)
    try:
        recomputed = authorize_transaction(
            mandate=mandate,
            transaction=transaction,
            catalog_snapshot=authorization_scenario.catalog_snapshot,
            server_time=authorization_scenario.server_time,
            nonce_state=authorization_scenario.nonce_state,
            committed_hashes=authorization_scenario.psp_committed_hashes,
            replay_seed=authorization_scenario.replay_seed,
            evaluated_at=authorization_scenario.evaluated_at,
            semantic_evidence=semantic_evidence,
            semantic_verifier=semantic_verifier,
            semantic_mode=SemanticMode.REPLAY,
        )
    except SemanticCacheError:
        return ExecutionRefusal(
            ExecutionRefusalReason.AUTHORIZATION_CONTEXT_UNVERIFIABLE
        )
    except (TypeError, ValueError):
        if not semantic_constraints_present:
            raise
        return ExecutionRefusal(
            ExecutionRefusalReason.AUTHORIZATION_CONTEXT_UNVERIFIABLE
        )

    recomputed_sha256 = authorization_result_sha256(recomputed)
    if (
        recomputed.final_action is not DecisionAction.ALLOW
        or recomputed_sha256 != authorization_result_sha256(authorization_result)
    ):
        return ExecutionRefusal(ExecutionRefusalReason.AUTHORIZATION_CONTEXT_MISMATCH)

    current_transaction_sha256 = transaction_body_sha256(transaction)
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

    current_state = mandate_state_registry.get_current(mandate.payload.mandate_id)
    if current_state is None:
        return ExecutionRefusal(ExecutionRefusalReason.MANDATE_STATE_MISSING)
    if current_state.version != mandate_version:
        bound_state = mandate_state_registry.get_version(
            mandate.payload.mandate_id, mandate_version
        )
        reason = (
            ExecutionRefusalReason.MANDATE_SUPERSEDED
            if bound_state is not None
            and bound_state.status is MandateStatus.SUPERSEDED
            else ExecutionRefusalReason.MANDATE_VERSION_MISMATCH
        )
        return ExecutionRefusal(reason)
    if current_state.status is MandateStatus.REVOKED:
        return ExecutionRefusal(ExecutionRefusalReason.MANDATE_REVOKED)
    if current_state.status is MandateStatus.SUPERSEDED:
        return ExecutionRefusal(ExecutionRefusalReason.MANDATE_SUPERSEDED)

    request = build_razorpay_order_request(transaction, decision_nonce)
    semantic_decision = recomputed.semantic_decision
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
        mandate_id=mandate.payload.mandate_id,
        mandate_version=mandate_version,
        mandate_payload_sha256=mandate_payload_sha256(mandate),
        transaction_body_sha256=current_transaction_sha256,
        authorization_result_sha256=recomputed_sha256,
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
