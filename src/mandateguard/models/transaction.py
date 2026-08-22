"""Agent-declared transaction structures committed before execution."""

from __future__ import annotations

from dataclasses import dataclass
import re


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: object, name: str, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")


def _minor_units(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer in minor units")


@dataclass(frozen=True, slots=True)
class TransactionLine:
    sku: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int
    recurring: bool

    def __post_init__(self) -> None:
        _nonempty(self.sku, "sku", 128)
        _minor_units(self.unit_price_minor, "unit_price_minor")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity < 1:
            raise ValueError("quantity must be a positive integer")
        _minor_units(self.line_total_minor, "line_total_minor")
        if not isinstance(self.recurring, bool):
            raise ValueError("recurring must be a boolean")


@dataclass(frozen=True, slots=True)
class TransactionPayload:
    """Exact agent-supplied state that an executor can be bound to."""

    transaction_id: str
    merchant_id: str
    cart_currency: str
    order_currency: str
    declared_order_total_minor: int
    declared_aggregate_quantity: int
    cart_recurring: bool
    order_recurring: bool
    lines: tuple[TransactionLine, ...]

    def __post_init__(self) -> None:
        _nonempty(self.transaction_id, "transaction_id")
        _nonempty(self.merchant_id, "merchant_id", 128)
        for currency, name in (
            (self.cart_currency, "cart_currency"),
            (self.order_currency, "order_currency"),
        ):
            if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
                raise ValueError(f"{name} must be a three-letter uppercase code")
        _minor_units(self.declared_order_total_minor, "declared_order_total_minor")
        if (
            isinstance(self.declared_aggregate_quantity, bool)
            or not isinstance(self.declared_aggregate_quantity, int)
            or self.declared_aggregate_quantity < 0
        ):
            raise ValueError("declared_aggregate_quantity must be a non-negative integer")
        if not isinstance(self.cart_recurring, bool) or not isinstance(self.order_recurring, bool):
            raise ValueError("transaction recurrence fields must be booleans")
        if not isinstance(self.lines, tuple) or not self.lines:
            raise ValueError("lines must be a non-empty tuple")
        if not all(isinstance(line, TransactionLine) for line in self.lines):
            raise ValueError("lines contains an invalid transaction line")


@dataclass(frozen=True, slots=True)
class Transaction:
    payload: TransactionPayload
    declared_payload_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.payload, TransactionPayload):
            raise ValueError("payload must be TransactionPayload")
        if not isinstance(self.declared_payload_sha256, str) or not _SHA256_RE.fullmatch(
            self.declared_payload_sha256
        ):
            raise ValueError("declared_payload_sha256 must be a lowercase SHA-256 hex digest")
