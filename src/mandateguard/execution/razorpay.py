"""Narrow Razorpay Orders API adapter fixed to the production API origin and Test Mode."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
from typing import Mapping, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.execution.models import RazorpayOrderRequest, RazorpayOrderResult


RAZORPAY_API_ORIGIN = "https://api.razorpay.com"
RAZORPAY_ORDERS_PATH = "/v1/orders"


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    body: bytes


class RazorpayHTTPTransport(Protocol):
    def post_json(
        self,
        *,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HTTPResponse: ...


class RazorpayAdapterError(RuntimeError):
    """Base class whose messages are deliberately bounded and credential-free."""


class RazorpayProviderRejection(RazorpayAdapterError):
    pass


class RazorpayAmbiguousTransportError(RazorpayAdapterError):
    pass


class RazorpayResponseValidationError(RazorpayAdapterError):
    pass


class UrllibRazorpayTransport:
    """Production transport with a non-configurable Razorpay API origin."""

    def post_json(
        self,
        *,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HTTPResponse:
        if path != RAZORPAY_ORDERS_PATH:
            raise ValueError("D6 transport only supports the Razorpay Orders path")
        outbound = urllib_request.Request(
            RAZORPAY_API_ORIGIN + path,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib_request.urlopen(outbound, timeout=timeout_seconds) as response:
                return HTTPResponse(status_code=response.status, body=response.read())
        except urllib_error.HTTPError as error:
            return HTTPResponse(status_code=error.code, body=error.read())
        except (TimeoutError, urllib_error.URLError, OSError):
            raise RazorpayAmbiguousTransportError(
                "Razorpay transport outcome is uncertain"
            ) from None


class RazorpayOrdersClient(Protocol):
    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult: ...


class RazorpayTestOrdersAdapter:
    """Create Razorpay Test Mode Orders; live key IDs are always rejected."""

    __slots__ = ("_authorization", "_timeout_seconds", "_transport")

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        transport: RazorpayHTTPTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not isinstance(key_id, str) or not key_id.startswith("rzp_test_"):
            raise ValueError("D6 Razorpay adapter requires an rzp_test_ key ID")
        if not isinstance(key_secret, str) or not key_secret:
            raise ValueError("Razorpay key secret must be non-empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        basic_token = base64.b64encode(
            f"{key_id}:{key_secret}".encode("utf-8")
        ).decode("ascii")
        self._authorization = f"Basic {basic_token}"
        self._transport = transport or UrllibRazorpayTransport()
        self._timeout_seconds = float(timeout_seconds)

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        if not isinstance(request, RazorpayOrderRequest):
            raise TypeError("request must be RazorpayOrderRequest")
        try:
            response = self._transport.post_json(
                path=RAZORPAY_ORDERS_PATH,
                headers={
                    "Authorization": self._authorization,
                    "Content-Type": "application/json",
                },
                body=canonical_json_bytes(request),
                timeout_seconds=self._timeout_seconds,
            )
        except RazorpayProviderRejection:
            raise RazorpayProviderRejection(
                "Razorpay rejected the order request"
            ) from None
        except RazorpayResponseValidationError:
            raise RazorpayResponseValidationError(
                "Razorpay returned an invalid successful response"
            ) from None
        except RazorpayAmbiguousTransportError:
            raise RazorpayAmbiguousTransportError(
                "Razorpay transport outcome is uncertain"
            ) from None
        except Exception:
            # Transport exception text is untrusted and can echo request headers.
            raise RazorpayAmbiguousTransportError(
                "Razorpay transport outcome is uncertain"
            ) from None
        if (
            not isinstance(response, HTTPResponse)
            or isinstance(response.status_code, bool)
            or not isinstance(response.status_code, int)
            or not isinstance(response.body, bytes)
        ):
            raise RazorpayAmbiguousTransportError(
                "Razorpay transport outcome is uncertain"
            )
        if not 200 <= response.status_code < 300:
            raise RazorpayProviderRejection(
                "Razorpay rejected the order request"
            )
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RazorpayResponseValidationError(
                "Razorpay returned an invalid successful response"
            ) from None
        return _validate_response(decoded, request)


def _validate_response(
    decoded: object, request: RazorpayOrderRequest
) -> RazorpayOrderResult:
    if not isinstance(decoded, dict):
        raise RazorpayResponseValidationError(
            "Razorpay returned an invalid successful response"
        )
    order_id = decoded.get("id")
    amount = decoded.get("amount")
    currency = decoded.get("currency")
    receipt = decoded.get("receipt")
    status = decoded.get("status")
    valid = (
        decoded.get("entity") == "order"
        and isinstance(order_id, str)
        and bool(order_id)
        and not isinstance(amount, bool)
        and isinstance(amount, int)
        and amount == request.amount
        and currency == request.currency
        and receipt == request.receipt
        and status == "created"
    )
    if not valid:
        raise RazorpayResponseValidationError(
            "Razorpay returned a mismatched successful response"
        )
    return RazorpayOrderResult(
        razorpay_order_id=order_id,
        amount=amount,
        currency=currency,
        receipt=receipt,
        status=status,
    )
