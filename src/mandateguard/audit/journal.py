"""Minimal append-only JSONL storage for canonical decision events."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mandateguard.audit.event import DecisionEvent, canonical_event_bytes
from mandateguard.audit.hash_chain import HashChainError, verify_event_hash, verify_hash_chain


class JournalError(ValueError):
    """Base class for journal read, format, and validation failures."""


class JournalFormatError(JournalError):
    """Raised for malformed, non-canonical, or truncated JSONL records."""


class JournalValidationError(JournalError):
    """Raised when a decoded event or its hash chain is invalid."""


def _reject_float(value: str) -> Any:
    raise ValueError(f"floating-point JSON numbers are forbidden: {value}")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON numbers are forbidden: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


class DecisionJournal:
    """A UTF-8, newline-terminated, canonical JSONL decision journal.

    The API only appends. Each append validates the existing file, writes one complete
    record, flushes Python's buffer, and calls ``fsync`` for the file. This asks the OS
    to persist file content and metadata, but it does not provide multi-process locking,
    transactional recovery, directory-entry fsync, protection from external rewrites,
    or guarantees beyond those of the underlying filesystem and storage hardware.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> tuple[DecisionEvent, ...]:
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
        except OSError as error:
            raise JournalError(f"could not read journal: {self.path}") from error
        if not raw:
            return ()
        if not raw.endswith(b"\n"):
            raise JournalFormatError(
                "journal must end with a newline; final record may be truncated"
            )

        events: list[DecisionEvent] = []
        for line_number, raw_line in enumerate(raw[:-1].split(b"\n"), start=1):
            if not raw_line:
                raise JournalFormatError(f"empty JSONL record at line {line_number}")
            try:
                text = raw_line.decode("utf-8", errors="strict")
                decoded = json.loads(
                    text,
                    object_pairs_hook=_object_without_duplicate_keys,
                    parse_float=_reject_float,
                    parse_constant=_reject_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise JournalFormatError(
                    f"malformed JSONL record at line {line_number}"
                ) from error
            try:
                event = DecisionEvent.from_mapping(decoded)
            except (TypeError, ValueError) as error:
                raise JournalValidationError(
                    f"invalid decision event at line {line_number}"
                ) from error
            if canonical_event_bytes(event) != raw_line:
                raise JournalFormatError(
                    f"non-canonical decision event at line {line_number}"
                )
            events.append(event)

        try:
            verify_hash_chain(events)
        except HashChainError as error:
            raise JournalValidationError("decision journal hash chain is invalid") from error
        return tuple(events)

    def append(self, event: DecisionEvent) -> None:
        if not isinstance(event, DecisionEvent):
            raise TypeError("event must be a DecisionEvent")
        existing = self.read_all()
        expected_sequence = len(existing) + 1
        expected_previous = existing[-1].event_sha256 if existing else None
        if event.sequence != expected_sequence:
            raise JournalValidationError(
                f"expected sequence {expected_sequence}, found {event.sequence}"
            )
        if event.previous_event_sha256 != expected_previous:
            raise JournalValidationError("event does not link to the current journal head")
        try:
            verify_event_hash(event)
        except HashChainError as error:
            raise JournalValidationError("event_sha256 is invalid") from error

        record = canonical_event_bytes(event) + b"\n"
        try:
            with self.path.open("ab") as handle:
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise JournalError(f"could not append journal: {self.path}") from error
