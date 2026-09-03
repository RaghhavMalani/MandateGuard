"""Side-effect orchestration after capability validation and nonce reservation."""

from __future__ import annotations

from datetime import datetime

from mandateguard.execution.gate import validate_and_reserve_execution
from mandateguard.execution.ledger import ExecutionLedger
from mandateguard.execution.mandate_state import MandateStateRegistry
from mandateguard.execution.models import (
    ExecutionError,
    ExecutionFailureReason,
    ExecutionLedgerStatus,
    ExecutionReceipt,
    ExecutionRefusal,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
)
from mandateguard.execution.razorpay import (
    RazorpayAmbiguousTransportError,
    RazorpayOrdersClient,
    RazorpayProviderRejection,
    RazorpayResponseValidationError,
)
from mandateguard.execution.signing import ExecutionVerifier
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction
from mandateguard.semantic.models import AuthorizationResult


def execute_razorpay_order(
    *,
    authorization: SignedExecutionAuthorization,
    authorization_result: AuthorizationResult,
    mandate: Mandate,
    transaction: Transaction,
    now: datetime,
    config: TrustedExecutionConfig,
    verifier: ExecutionVerifier,
    ledger: ExecutionLedger,
    client: RazorpayOrdersClient,
    mandate_state_registry: MandateStateRegistry,
) -> ExecutionReceipt | ExecutionRefusal:
    """Make at most one provider call under the trusted mandate-state guard."""

    if not callable(getattr(mandate_state_registry, "execution_guard", None)):
        raise TypeError("mandate_state_registry must be trusted server state")
    # SQLite holds a write reservation from current-state validation through
    # provider return. A concurrent revoke therefore orders before the call
    # (and refuses it) or after the call; it cannot interleave between them.
    with mandate_state_registry.execution_guard():
        return _execute_guarded(
            authorization=authorization,
            authorization_result=authorization_result,
            mandate=mandate,
            transaction=transaction,
            now=now,
            config=config,
            verifier=verifier,
            ledger=ledger,
            client=client,
            mandate_state_registry=mandate_state_registry,
        )


def _execute_guarded(
    *,
    authorization: SignedExecutionAuthorization,
    authorization_result: AuthorizationResult,
    mandate: Mandate,
    transaction: Transaction,
    now: datetime,
    config: TrustedExecutionConfig,
    verifier: ExecutionVerifier,
    ledger: ExecutionLedger,
    client: RazorpayOrdersClient,
    mandate_state_registry: MandateStateRegistry,
) -> ExecutionReceipt | ExecutionRefusal:
    """Validate, reserve, and perform provider I/O while state is serialized."""

    grant = validate_and_reserve_execution(
        authorization=authorization,
        authorization_result=authorization_result,
        mandate=mandate,
        transaction=transaction,
        now=now,
        config=config,
        verifier=verifier,
        ledger=ledger,
        mandate_state_registry=mandate_state_registry,
    )
    if isinstance(grant, ExecutionRefusal):
        return grant

    try:
        result = client.create_order(grant.request)
    except RazorpayProviderRejection:
        if not ledger.mark_rejected(
            grant.decision_nonce, grant.execution_request_sha256
        ):
            raise ExecutionError(
                ExecutionFailureReason.LEDGER_TRANSITION_FAILED,
                ExecutionLedgerStatus.RESERVED,
            ) from None
        raise ExecutionError(
            ExecutionFailureReason.PROVIDER_REJECTED,
            ExecutionLedgerStatus.REJECTED,
        ) from None
    except (RazorpayAmbiguousTransportError, RazorpayResponseValidationError):
        if not ledger.mark_uncertain(
            grant.decision_nonce, grant.execution_request_sha256
        ):
            raise ExecutionError(
                ExecutionFailureReason.LEDGER_TRANSITION_FAILED,
                ExecutionLedgerStatus.RESERVED,
            ) from None
        raise ExecutionError(
            ExecutionFailureReason.OUTCOME_UNCERTAIN,
            ExecutionLedgerStatus.UNCERTAIN,
        ) from None

    if not ledger.mark_succeeded(
        grant.decision_nonce,
        grant.execution_request_sha256,
        result.razorpay_order_id,
    ):
        raise ExecutionError(
            ExecutionFailureReason.LEDGER_TRANSITION_FAILED,
            ExecutionLedgerStatus.RESERVED,
        )
    return ExecutionReceipt(
        execution_request_sha256=grant.execution_request_sha256,
        decision_nonce=grant.decision_nonce,
        razorpay_order_id=result.razorpay_order_id,
        amount=result.amount,
        currency=result.currency,
        receipt=result.receipt,
        status=result.status,
    )
