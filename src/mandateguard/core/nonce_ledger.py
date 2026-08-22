"""Immutable V1 single-use nonce state."""

from __future__ import annotations

from dataclasses import dataclass


class NonceAlreadyConsumed(ValueError):
    """Raised when V1 nonce consumption is attempted more than once."""


@dataclass(frozen=True, slots=True)
class NonceLedgerState:
    consumed_nonces: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.consumed_nonces, frozenset) or not all(
            isinstance(nonce, str) and nonce for nonce in self.consumed_nonces
        ):
            raise ValueError("consumed_nonces must be a frozenset of non-empty strings")

    def is_consumed(self, nonce: str) -> bool:
        return nonce in self.consumed_nonces

    def consume(self, nonce: str) -> NonceLedgerState:
        """Return new ledger state; every V1 nonce is single-use."""

        if not isinstance(nonce, str) or not nonce:
            raise ValueError("nonce must be a non-empty string")
        if self.is_consumed(nonce):
            raise NonceAlreadyConsumed(f"nonce has already been consumed: {nonce}")
        return NonceLedgerState(self.consumed_nonces | {nonce})
