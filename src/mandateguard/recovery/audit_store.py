"""Persistent local recovery provenance for the Commerce Lab engineering surface."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import sha256_canonical
from mandateguard.recovery.models import RecoveryAuditEvent


class RecoveryAuditStoreError(RuntimeError):
    pass


class SQLiteRecoveryAuditStore:
    """Append-only SQLite event storage inside the local trusted computing base."""

    __slots__ = ("_connection", "_lock")

    def __init__(self, path: str | Path) -> None:
        if not isinstance(path, (str, Path)):
            raise TypeError("path must be a string or Path")
        self._connection = sqlite3.connect(
            str(path), timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._lock = RLock()
        try:
            with self._lock:
                self._connection.execute("PRAGMA busy_timeout = 5000")
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS recovery_audit_events (
                        review_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL UNIQUE,
                        previous_event_sha256 TEXT,
                        event_json TEXT NOT NULL,
                        PRIMARY KEY (review_id, sequence)
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise RecoveryAuditStoreError(
                "recovery audit store could not be initialized"
            ) from exc

    def append(self, events: tuple[RecoveryAuditEvent, ...]) -> None:
        if not isinstance(events, tuple) or not all(
            isinstance(event, RecoveryAuditEvent) for event in events
        ):
            raise TypeError("events must be a RecoveryAuditEvent tuple")
        if not events:
            return
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                for event in events:
                    encoded = canonical_json_text(
                        {**event.body_data(), "event_sha256": event.event_sha256}
                    )
                    existing = self._connection.execute(
                        """
                        SELECT event_sha256, event_json
                        FROM recovery_audit_events
                        WHERE review_id = ? AND sequence = ?
                        """,
                        (event.review_id, event.sequence),
                    ).fetchone()
                    if existing is not None:
                        if existing != (event.event_sha256, encoded):
                            raise RecoveryAuditStoreError(
                                "refusing to replace recovery audit provenance"
                            )
                        continue
                    previous = self._connection.execute(
                        """
                        SELECT event_sha256
                        FROM recovery_audit_events
                        WHERE review_id = ? AND sequence = ?
                        """,
                        (event.review_id, event.sequence - 1),
                    ).fetchone()
                    expected_previous = previous[0] if previous is not None else None
                    if event.previous_event_sha256 != expected_previous:
                        raise RecoveryAuditStoreError(
                            "recovery audit event does not link to the persisted head"
                        )
                    self._connection.execute(
                        """
                        INSERT INTO recovery_audit_events (
                            review_id, sequence, event_type, event_sha256,
                            previous_event_sha256, event_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.review_id,
                            event.sequence,
                            event.event.value,
                            event.event_sha256,
                            event.previous_event_sha256,
                            encoded,
                        ),
                    )
                self._connection.execute("COMMIT")
        except RecoveryAuditStoreError:
            with self._lock:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            with self._lock:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise RecoveryAuditStoreError(
                "recovery audit provenance could not be persisted"
            ) from exc

    def read(self, review_id: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(review_id, str) or not review_id:
            raise ValueError("review_id must be non-empty")
        try:
            with self._lock:
                rows = self._connection.execute(
                    """
                    SELECT event_json FROM recovery_audit_events
                    WHERE review_id = ? ORDER BY sequence
                    """,
                    (review_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RecoveryAuditStoreError(
                "recovery audit provenance could not be read"
            ) from exc
        decoded = tuple(json.loads(row[0]) for row in rows)
        previous: str | None = None
        for sequence, event in enumerate(decoded, start=1):
            event_hash = event.get("event_sha256")
            body = {key: value for key, value in event.items() if key != "event_sha256"}
            if (
                event.get("sequence") != sequence
                or event.get("previous_event_sha256") != previous
                or sha256_canonical(body) != event_hash
            ):
                raise RecoveryAuditStoreError(
                    "persisted recovery audit chain failed integrity validation"
                )
            previous = event_hash
        return decoded

    def close(self) -> None:
        with self._lock:
            self._connection.close()
