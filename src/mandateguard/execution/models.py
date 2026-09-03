"""Immutable value objects for capability-scoped Razorpay Test Mode execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import re
from uuid import UUID

from mandateguard.models.decision import DecisionAction


EXECUTION_SCHEMA_VERSION = "1.0"
RAZORPAY_TEST_ENVIRONMENT = "TEST"
RAZORPAY_ORDERS_AUDIENCE = "razorpay-orders"
MAX_CAPABILITY_LIFETIME = timedelta(minutes=5)
MAX_ISSUED_AT_FUTURE_SKEW = timedelta(seconds=30)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_RECEIPT_RE = re.compile(r"^[\x21-\x7e]{1,40}$")


def _require_nonempty(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")


def _require_digest(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        suffix = " or null" if nullable else ""
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest{suffix}")


def _require_aware(value: object, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be a timezone-aware datetime")


class ExecutionRefusalReason(str, Enum):
    AUTHORIZATION_BLOCKED = "AUTHORIZATION_BLOCKED"
    AUTHORIZATION_REVIEW_REQUIRED = "AUTHORIZATION_REVIEW_REQUIRED"
    AUTHORIZATION_CONTEXT_MISMATCH = "AUTHORIZATION_CONTEXT_MISMATCH"
    AUTHORIZATION_CONTEXT_UNVERIFIABLE = "AUTHORIZATION_CONTEXT_UNVERIFIABLE"
    AUTHORIZATION_RESULT_HASH_MISMATCH = "AUTHORIZATION_RESULT_HASH_MISMATCH"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    UNKNOWN_SIGNING_KEY = "UNKNOWN_SIGNING_KEY"
    CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
    CAPABILITY_NOT_YET_VALID = "CAPABILITY_NOT_YET_VALID"
    INVALID_CAPABILITY_LIFETIME = "INVALID_CAPABILITY_LIFETIME"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    AUDIENCE_MISMATCH = "AUDIENCE_MISMATCH"
    ACCOUNT_SCOPE_MISMATCH = "ACCOUNT_SCOPE_MISMATCH"
    MERCHANT_MISMATCH = "MERCHANT_MISMATCH"
    MANDATE_HASH_MISMATCH = "MANDATE_HASH_MISMATCH"
    MANDATE_ID_MISMATCH = "MANDATE_ID_MISMATCH"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_SUPERSEDED = "MANDATE_SUPERSEDED"
    MANDATE_STATE_CORRUPT = "MANDATE_STATE_CORRUPT"
    MANDATE_STATE_MISSING = "MANDATE_STATE_MISSING"
    MANDATE_VERSION_MISMATCH = "MANDATE_VERSION_MISMATCH"
    TRANSACTION_HASH_MISMATCH = "TRANSACTION_HASH_MISMATCH"
    EXECUTION_REQUEST_HASH_MISMATCH = "EXECUTION_REQUEST_HASH_MISMATCH"
    NONCE_ALREADY_USED = "NONCE_ALREADY_USED"


@dataclass(frozen=True, slots=True)
class ExecutionRefusal:
    reason: ExecutionRefusalReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, ExecutionRefusalReason):
            raise TypeError("reason must be an ExecutionRefusalReason")


@dataclass(frozen=True, slots=True)
class TrustedExecutionConfig:
    """Trusted mapping from one MandateGuard merchant to one PSP account scope."""

    merchant_id: str
    account_scope: str
    environment: str = RAZORPAY_TEST_ENVIRONMENT
    audience: str = RAZORPAY_ORDERS_AUDIENCE

    def __post_init__(self) -> None:
        _require_nonempty(self.merchant_id, "merchant_id", 128)
        _require_nonempty(self.account_scope, "account_scope", 256)
        if self.environment != RAZORPAY_TEST_ENVIRONMENT:
            raise ValueError("D6 trusted execution environment must be TEST")
        if self.audience != RAZORPAY_ORDERS_AUDIENCE:
            raise ValueError("D6 trusted execution audience must be razorpay-orders")


@dataclass(frozen=True, slots=True)
class ExecutionAuthorizationPayload:
    schema_version: str
    decision_nonce: str
    action: DecisionAction
    issued_at: datetime
    expires_at: datetime
    environment: str
    audience: str
    account_scope: str
    merchant_id: str
    mandate_id: str
    mandate_version: int
    mandate_payload_sha256: str
    transaction_body_sha256: str
    authorization_result_sha256: str
    execution_request_sha256: str
    semantic_input_sha256: str | None
    semantic_output_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_SCHEMA_VERSION:
            raise ValueError("schema_version must be 1.0")
        if not isinstance(self.decision_nonce, str) or not _NONCE_RE.fullmatch(
            self.decision_nonce
        ):
            raise ValueError("decision_nonce must be 16-128 ASCII letters, digits, '_' or '-'")
        if self.action is not DecisionAction.ALLOW:
            raise ValueError("execution authorization action must be ALLOW")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.issued_at >= self.expires_at:
            raise ValueError("issued_at must be before expires_at")
        if self.expires_at - self.issued_at > MAX_CAPABILITY_LIFETIME:
            raise ValueError("capability lifetime must not exceed five minutes")
        _require_nonempty(self.environment, "environment", 32)
        _require_nonempty(self.audience, "audience", 128)
        _require_nonempty(self.account_scope, "account_scope", 256)
        _require_nonempty(self.merchant_id, "merchant_id", 128)
        try:
            UUID(self.mandate_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("mandate_id must be a UUID string") from error
        if (
            isinstance(self.mandate_version, bool)
            or not isinstance(self.mandate_version, int)
            or self.mandate_version < 1
        ):
            raise ValueError("mandate_version must be a positive integer")
        for value, name in (
            (self.mandate_payload_sha256, "mandate_payload_sha256"),
            (self.transaction_body_sha256, "transaction_body_sha256"),
            (self.authorization_result_sha256, "authorization_result_sha256"),
            (self.execution_request_sha256, "execution_request_sha256"),
        ):
            _require_digest(value, name)
        _require_digest(
            self.semantic_input_sha256, "semantic_input_sha256", nullable=True
        )
        _require_digest(
            self.semantic_output_sha256, "semantic_output_sha256", nullable=True
        )
        if (self.semantic_input_sha256 is None) != (
            self.semantic_output_sha256 is None
        ):
            raise ValueError("semantic input and output hashes must both be present or both be null")


@dataclass(frozen=True, slots=True)
class SignedExecutionAuthorization:
    payload: ExecutionAuthorizationPayload
    key_id: str
    algorithm: str
    signature: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.payload, ExecutionAuthorizationPayload):
            raise TypeError("payload must be ExecutionAuthorizationPayload")
        _require_nonempty(self.key_id, "key_id", 256)
        if self.algorithm != "HMAC-SHA256":
            raise ValueError("algorithm must be HMAC-SHA256")
        if not isinstance(self.signature, bytes) or len(self.signature) != 32:
            raise ValueError("signature must be a 32-byte HMAC-SHA256 digest")


@dataclass(frozen=True, slots=True)
class RazorpayOrderRequest:
    amount: int
    currency: str
    receipt: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int) or self.amount < 0:
            raise ValueError("amount must be a non-negative integer in minor units")
        if not isinstance(self.currency, str) or not _CURRENCY_RE.fullmatch(self.currency):
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.receipt, str) or not _RECEIPT_RE.fullmatch(self.receipt):
            raise ValueError("receipt must be 1-40 printable ASCII characters")


@dataclass(frozen=True, slots=True)
class ValidatedExecutionGrant:
    decision_nonce: str
    execution_request_sha256: str
    request: RazorpayOrderRequest

    def __post_init__(self) -> None:
        if not isinstance(self.decision_nonce, str) or not _NONCE_RE.fullmatch(
            self.decision_nonce
        ):
            raise ValueError("decision_nonce is invalid")
        _require_digest(self.execution_request_sha256, "execution_request_sha256")
        if not isinstance(self.request, RazorpayOrderRequest):
            raise TypeError("request must be RazorpayOrderRequest")


class ExecutionLedgerStatus(str, Enum):
    RESERVED = "RESERVED"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class ExecutionLedgerRecord:
    decision_nonce: str
    execution_request_sha256: str
    status: ExecutionLedgerStatus
    razorpay_order_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.decision_nonce, str) or not _NONCE_RE.fullmatch(
            self.decision_nonce
        ):
            raise ValueError("decision_nonce is invalid")
        _require_digest(self.execution_request_sha256, "execution_request_sha256")
        if not isinstance(self.status, ExecutionLedgerStatus):
            raise TypeError("status must be ExecutionLedgerStatus")
        if self.status is ExecutionLedgerStatus.SUCCEEDED:
            _require_nonempty(self.razorpay_order_id, "razorpay_order_id", 256)
        elif self.razorpay_order_id is not None:
            raise ValueError("only a SUCCEEDED record may contain a Razorpay order ID")


@dataclass(frozen=True, slots=True)
class RazorpayOrderResult:
    razorpay_order_id: str
    amount: int
    currency: str
    receipt: str
    status: str

    def __post_init__(self) -> None:
        _require_nonempty(self.razorpay_order_id, "razorpay_order_id", 256)
        RazorpayOrderRequest(
            amount=self.amount, currency=self.currency, receipt=self.receipt
        )
        if self.status != "created":
            raise ValueError("Razorpay Order result status must be created")


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    execution_request_sha256: str
    decision_nonce: str
    razorpay_order_id: str
    amount: int
    currency: str
    receipt: str
    status: str

    def __post_init__(self) -> None:
        _require_digest(self.execution_request_sha256, "execution_request_sha256")
        if not isinstance(self.decision_nonce, str) or not _NONCE_RE.fullmatch(
            self.decision_nonce
        ):
            raise ValueError("decision_nonce is invalid")
        RazorpayOrderResult(
            razorpay_order_id=self.razorpay_order_id,
            amount=self.amount,
            currency=self.currency,
            receipt=self.receipt,
            status=self.status,
        )


class ExecutionFailureReason(str, Enum):
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"
    LEDGER_TRANSITION_FAILED = "LEDGER_TRANSITION_FAILED"


class ExecutionError(RuntimeError):
    """Safe, typed execution failure that never incorporates provider error text."""

    def __init__(
        self, reason: ExecutionFailureReason, ledger_status: ExecutionLedgerStatus
    ) -> None:
        self.reason = reason
        self.ledger_status = ledger_status
        super().__init__(f"execution failed: {reason.value}; ledger={ledger_status.value}")
