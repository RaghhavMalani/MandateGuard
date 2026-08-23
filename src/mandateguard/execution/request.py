"""Deterministic construction and hashing of the only D6 Razorpay request shape."""

from __future__ import annotations

from hashlib import sha256

from mandateguard.core.hashing import sha256_canonical
from mandateguard.execution.models import RazorpayOrderRequest
from mandateguard.models.transaction import Transaction


def receipt_for_decision_nonce(decision_nonce: str) -> str:
    if not isinstance(decision_nonce, str):
        raise TypeError("decision_nonce must be a string")
    # Forty printable ASCII characters, derived only from trusted capability data.
    return "mg_" + sha256(decision_nonce.encode("ascii")).hexdigest()[:37]


def build_razorpay_order_request(
    transaction: Transaction, decision_nonce: str
) -> RazorpayOrderRequest:
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")
    return RazorpayOrderRequest(
        amount=transaction.payload.declared_order_total_minor,
        currency=transaction.payload.order_currency,
        receipt=receipt_for_decision_nonce(decision_nonce),
    )


def execution_request_sha256(request: RazorpayOrderRequest) -> str:
    if not isinstance(request, RazorpayOrderRequest):
        raise TypeError("request must be RazorpayOrderRequest")
    return sha256_canonical(request)
