from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from mandateguard.execution import RazorpayOrderRequest, RazorpayTestOrdersAdapter
from mandateguard.execution.razorpay import (
    HTTPResponse,
    RAZORPAY_ORDERS_PATH,
    RazorpayAmbiguousTransportError,
    RazorpayProviderRejection,
    RazorpayResponseValidationError,
)
from mandateguard.execution.request import receipt_for_decision_nonce
from tests.execution_factories import DECISION_NONCE


@dataclass
class RecordingTransport:
    status_code: int = 200
    response_changes: dict[str, object] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)

    def post_json(self, *, path, headers, body, timeout_seconds):
        self.calls.append(
            {
                "path": path,
                "headers": headers,
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        request = json.loads(body)
        response = {
            "entity": "order",
            "id": "order_synthetic_adapter",
            "amount": request["amount"],
            "currency": request["currency"],
            "receipt": request["receipt"],
            "status": "created",
            **self.response_changes,
        }
        return HTTPResponse(
            status_code=self.status_code, body=json.dumps(response).encode("utf-8")
        )


def _request() -> RazorpayOrderRequest:
    return RazorpayOrderRequest(
        amount=10_000,
        currency="INR",
        receipt=receipt_for_decision_nonce(DECISION_NONCE),
    )


def _adapter(transport) -> RazorpayTestOrdersAdapter:
    return RazorpayTestOrdersAdapter(
        key_id="rzp_test_synthetic",
        key_secret="synthetic-non-secret",
        transport=transport,
        timeout_seconds=7,
    )


def test_test_key_is_accepted_and_only_narrow_request_fields_are_sent() -> None:
    transport = RecordingTransport()
    adapter = _adapter(transport)

    result = adapter.create_order(_request())

    assert result.razorpay_order_id == "order_synthetic_adapter"
    assert result.status == "created"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["path"] == RAZORPAY_ORDERS_PATH
    assert call["timeout_seconds"] == 7.0
    assert json.loads(call["body"]) == {
        "amount": 10_000,
        "currency": "INR",
        "receipt": _request().receipt,
    }
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.parametrize("key_id", ["rzp_live_synthetic", "not-a-razorpay-key", ""])
def test_live_and_non_test_keys_are_rejected(key_id) -> None:
    with pytest.raises(ValueError, match="rzp_test_"):
        RazorpayTestOrdersAdapter(
            key_id=key_id,
            key_secret="synthetic-non-secret",
            transport=RecordingTransport(),
        )


def test_provider_non_2xx_is_a_definite_rejection() -> None:
    adapter = _adapter(RecordingTransport(status_code=400))

    with pytest.raises(RazorpayProviderRejection):
        adapter.create_order(_request())


@pytest.mark.parametrize(
    "changes",
    [
        {"entity": "payment"},
        {"id": ""},
        {"amount": 10_001},
        {"amount": 10_000.0},
        {"currency": "USD"},
        {"receipt": "different-receipt"},
        {"status": "paid"},
    ],
)
def test_mismatched_success_response_is_rejected(changes) -> None:
    adapter = _adapter(RecordingTransport(response_changes=changes))

    with pytest.raises(RazorpayResponseValidationError):
        adapter.create_order(_request())


@dataclass
class SecretEchoingTransport:
    marker: str

    def post_json(self, *, path, headers, body, timeout_seconds):
        raise TimeoutError(self.marker + headers["Authorization"])


def test_transport_exception_does_not_expose_secret_or_authorization_header() -> None:
    marker = "synthetic-sensitive-marker"
    adapter = RazorpayTestOrdersAdapter(
        key_id="rzp_test_synthetic",
        key_secret=marker,
        transport=SecretEchoingTransport(marker),
    )

    with pytest.raises(RazorpayAmbiguousTransportError) as caught:
        adapter.create_order(_request())

    assert marker not in str(caught.value)
    assert "Basic " not in str(caught.value)


@dataclass
class InvalidJSONTransport:
    def post_json(self, *, path, headers, body, timeout_seconds):
        return HTTPResponse(status_code=200, body=b"not-json")


def test_invalid_json_success_response_is_rejected() -> None:
    with pytest.raises(RazorpayResponseValidationError):
        _adapter(InvalidJSONTransport()).create_order(_request())


def test_receipt_is_stable_bounded_ascii_and_nonce_specific() -> None:
    first = receipt_for_decision_nonce(DECISION_NONCE)
    same = receipt_for_decision_nonce(DECISION_NONCE)
    different = receipt_for_decision_nonce("decision_nonce_987654321")

    assert first == same
    assert first != different
    assert len(first) <= 40
    assert first.isascii()
