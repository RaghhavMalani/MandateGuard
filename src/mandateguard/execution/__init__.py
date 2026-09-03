"""Capability-scoped Razorpay Test Mode execution for MandateGuard D6."""

from mandateguard.execution.authorization import (
    authorization_result_sha256,
    issue_execution_authorization,
)
from mandateguard.execution.executor import execute_razorpay_order
from mandateguard.execution.gate import validate_and_reserve_execution
from mandateguard.execution.ledger import SQLiteExecutionLedger
from mandateguard.execution.mandate_state import (
    InMemoryMandateStateRegistry,
    MandateAuditEventType,
    MandateState,
    MandateStateBusyError,
    MandateStateCorruptionError,
    MandateStateRegistry,
    MandateStateTransitionError,
    MandateStatus,
    SQLiteMandateStateRegistry,
)
from mandateguard.execution.models import (
    ExecutionAuthorizationPayload,
    ExecutionError,
    ExecutionFailureReason,
    ExecutionLedgerRecord,
    ExecutionLedgerStatus,
    ExecutionReceipt,
    ExecutionRefusal,
    ExecutionRefusalReason,
    RazorpayOrderRequest,
    RazorpayOrderResult,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
    ValidatedExecutionGrant,
)
from mandateguard.execution.razorpay import RazorpayTestOrdersAdapter
from mandateguard.execution.request import (
    build_razorpay_order_request,
    execution_request_sha256,
    receipt_for_decision_nonce,
)
from mandateguard.execution.signing import HMACSHA256Signer, HMACSHA256Verifier

__all__ = [
    "ExecutionAuthorizationPayload",
    "ExecutionError",
    "ExecutionFailureReason",
    "ExecutionLedgerRecord",
    "ExecutionLedgerStatus",
    "ExecutionReceipt",
    "ExecutionRefusal",
    "ExecutionRefusalReason",
    "HMACSHA256Signer",
    "HMACSHA256Verifier",
    "InMemoryMandateStateRegistry",
    "MandateAuditEventType",
    "MandateState",
    "MandateStateBusyError",
    "MandateStateCorruptionError",
    "MandateStateRegistry",
    "MandateStateTransitionError",
    "MandateStatus",
    "RazorpayOrderRequest",
    "RazorpayOrderResult",
    "RazorpayTestOrdersAdapter",
    "SQLiteExecutionLedger",
    "SQLiteMandateStateRegistry",
    "SignedExecutionAuthorization",
    "TrustedExecutionConfig",
    "ValidatedExecutionGrant",
    "authorization_result_sha256",
    "build_razorpay_order_request",
    "execute_razorpay_order",
    "execution_request_sha256",
    "issue_execution_authorization",
    "receipt_for_decision_nonce",
    "validate_and_reserve_execution",
]
