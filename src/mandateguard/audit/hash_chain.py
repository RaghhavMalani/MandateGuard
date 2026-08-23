"""Integrity verification for the ordered decision-event hash chain."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

from mandateguard.audit.event import DecisionEvent, canonical_event_body_bytes


class HashChainError(ValueError):
    """Raised when an event hash or link in a decision journal is invalid."""


def event_body_sha256(event: DecisionEvent) -> str:
    """Hash the canonical event body, which excludes ``event_sha256`` itself."""

    return sha256(canonical_event_body_bytes(event)).hexdigest()


def verify_event_hash(event: DecisionEvent) -> None:
    expected = event_body_sha256(event)
    if event.event_sha256 != expected:
        raise HashChainError(
            f"event {event.sequence} has an incorrect event_sha256"
        )


def verify_hash_chain(events: Iterable[DecisionEvent]) -> None:
    """Validate hashes, one-based sequence continuity, and every previous link."""

    previous: DecisionEvent | None = None
    for expected_sequence, event in enumerate(events, start=1):
        if not isinstance(event, DecisionEvent):
            raise HashChainError(
                f"chain item {expected_sequence} is not a DecisionEvent"
            )
        if event.sequence != expected_sequence:
            raise HashChainError(
                f"expected sequence {expected_sequence}, found {event.sequence}"
            )
        expected_previous = None if previous is None else previous.event_sha256
        if event.previous_event_sha256 != expected_previous:
            raise HashChainError(
                f"event {event.sequence} has an incorrect previous_event_sha256"
            )
        verify_event_hash(event)
        previous = event
