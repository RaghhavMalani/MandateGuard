"""Trusted current-consent state used by the execution kill switch."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import ContextManager, Iterator, Protocol
from uuid import UUID

from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import sha256_canonical


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MandateStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


class MandateAuditEventType(str, Enum):
    MANDATE_REGISTERED_ACTIVE = "MANDATE_REGISTERED_ACTIVE"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_SUPERSEDED = "MANDATE_SUPERSEDED"
    EXECUTION_REFUSED_MANDATE_STATE = "EXECUTION_REFUSED_MANDATE_STATE"


class MandateStateTransitionError(RuntimeError):
    """A trusted mandate-state transition would weaken lifecycle semantics."""


def _validate_mandate_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("mandate_id must be a UUID string")
    try:
        UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("mandate_id must be a UUID string") from error
    return value


def _validate_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("mandate version must be a positive integer")
    return value


def _validate_time(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _validate_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _time_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class MandateState:
    mandate_id: str
    version: int
    status: MandateStatus
    updated_at: datetime
    revoked_at: datetime | None = None
    superseded_by_version: int | None = None

    def __post_init__(self) -> None:
        _validate_mandate_id(self.mandate_id)
        _validate_version(self.version)
        if not isinstance(self.status, MandateStatus):
            raise TypeError("status must be a MandateStatus")
        _validate_time(self.updated_at, "updated_at")
        if self.status is MandateStatus.ACTIVE:
            if self.revoked_at is not None or self.superseded_by_version is not None:
                raise ValueError("ACTIVE state cannot carry terminal-state metadata")
        elif self.status is MandateStatus.REVOKED:
            _validate_time(self.revoked_at, "revoked_at")
            if self.revoked_at != self.updated_at:
                raise ValueError("revoked_at must equal the REVOKED update time")
            if self.superseded_by_version is not None:
                raise ValueError("REVOKED state cannot be superseded")
        else:
            if self.revoked_at is not None:
                raise ValueError("SUPERSEDED state cannot be revoked")
            next_version = _validate_version(self.superseded_by_version)
            if next_version <= self.version:
                raise ValueError("superseded_by_version must be newer")


class MandateStateRegistry(Protocol):
    def get_current(self, mandate_id: str) -> MandateState | None: ...

    def get_version(self, mandate_id: str, version: int) -> MandateState | None: ...

    def register_active(
        self, mandate_id: str, version: int, *, updated_at: datetime
    ) -> MandateState: ...

    def revoke(
        self, mandate_id: str, version: int, *, revoked_at: datetime
    ) -> MandateState: ...

    def supersede(
        self,
        mandate_id: str,
        version: int,
        *,
        superseded_by_version: int,
        updated_at: datetime,
    ) -> MandateState: ...

    def record_execution_refusal(
        self,
        *,
        mandate_id: str,
        version: int,
        occurred_at: datetime,
        reason: str,
        decision_nonce: str,
        execution_request_sha256: str,
        authorization_result_sha256: str,
    ) -> None: ...

    def audit_events(self, mandate_id: str) -> tuple[dict[str, object], ...]: ...

    def execution_guard(self) -> ContextManager[None]: ...


def _state_event_body(
    *,
    mandate_id: str,
    version: int,
    event: MandateAuditEventType,
    previous_status: MandateStatus | None,
    current_status: MandateStatus | None,
    recorded_at: datetime,
    reason: str,
    decision_nonce: str | None,
    execution_request_sha256: str | None,
    authorization_result_sha256: str | None,
    sequence: int,
    previous_event_sha256: str | None,
) -> dict[str, object]:
    return {
        "mandate_id": _validate_mandate_id(mandate_id),
        "mandate_version": _validate_version(version),
        "event": event.value,
        "previous_status": previous_status.value if previous_status else None,
        "current_status": current_status.value if current_status else None,
        "recorded_at": _time_text(_validate_time(recorded_at, "recorded_at")),
        "reason": reason,
        "decision_nonce": decision_nonce,
        "execution_request_sha256": execution_request_sha256,
        "authorization_result_sha256": authorization_result_sha256,
        "sequence": sequence,
        "previous_event_sha256": previous_event_sha256,
    }


class InMemoryMandateStateRegistry:
    """Deterministic fake that retains the same irreversible transition rules."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], MandateState] = {}
        self._current: dict[str, int] = {}
        self._audit: dict[str, list[dict[str, object]]] = {}
        self._lock = RLock()

    def get_current(self, mandate_id: str) -> MandateState | None:
        _validate_mandate_id(mandate_id)
        with self._lock:
            version = self._current.get(mandate_id)
            return None if version is None else self._states[(mandate_id, version)]

    def get_version(self, mandate_id: str, version: int) -> MandateState | None:
        _validate_mandate_id(mandate_id)
        _validate_version(version)
        with self._lock:
            return self._states.get((mandate_id, version))

    def _append(
        self,
        *,
        state: MandateState,
        event: MandateAuditEventType,
        previous_status: MandateStatus | None,
        current_status: MandateStatus | None,
        recorded_at: datetime,
        reason: str,
        decision_nonce: str | None = None,
        execution_request_sha256: str | None = None,
        authorization_result_sha256: str | None = None,
    ) -> None:
        events = self._audit.setdefault(state.mandate_id, [])
        previous_hash = events[-1]["event_sha256"] if events else None
        body = _state_event_body(
            mandate_id=state.mandate_id,
            version=state.version,
            event=event,
            previous_status=previous_status,
            current_status=current_status,
            recorded_at=recorded_at,
            reason=reason,
            decision_nonce=decision_nonce,
            execution_request_sha256=execution_request_sha256,
            authorization_result_sha256=authorization_result_sha256,
            sequence=len(events) + 1,
            previous_event_sha256=previous_hash if isinstance(previous_hash, str) else None,
        )
        events.append({**body, "event_sha256": sha256_canonical(body)})

    def register_active(
        self, mandate_id: str, version: int, *, updated_at: datetime
    ) -> MandateState:
        _validate_mandate_id(mandate_id)
        _validate_version(version)
        _validate_time(updated_at, "updated_at")
        with self._lock:
            current = self.get_current(mandate_id)
            if current is not None:
                if current.version == version and current.status is MandateStatus.ACTIVE:
                    return current
                if version <= current.version:
                    raise MandateStateTransitionError(
                        "a revoked or superseded mandate version cannot be reactivated"
                    )
                if current.status is MandateStatus.ACTIVE:
                    raise MandateStateTransitionError(
                        "an ACTIVE mandate must be replaced through supersede"
                    )
                if updated_at < current.updated_at:
                    raise MandateStateTransitionError("mandate state time moved backwards")
            state = MandateState(
                mandate_id=mandate_id,
                version=version,
                status=MandateStatus.ACTIVE,
                updated_at=updated_at,
            )
            self._states[(mandate_id, version)] = state
            self._current[mandate_id] = version
            self._append(
                state=state,
                event=MandateAuditEventType.MANDATE_REGISTERED_ACTIVE,
                previous_status=current.status if current else None,
                current_status=MandateStatus.ACTIVE,
                recorded_at=updated_at,
                reason="TRUSTED_SERVER_REGISTRATION",
            )
            return state

    def revoke(
        self, mandate_id: str, version: int, *, revoked_at: datetime
    ) -> MandateState:
        _validate_time(revoked_at, "revoked_at")
        with self._lock:
            current = self.get_current(mandate_id)
            if current is None:
                raise MandateStateTransitionError("mandate state is missing")
            if current.version != version:
                raise MandateStateTransitionError("mandate version is not current")
            if current.status is MandateStatus.REVOKED:
                return current
            if current.status is not MandateStatus.ACTIVE:
                raise MandateStateTransitionError("only an ACTIVE mandate can be revoked")
            if revoked_at < current.updated_at:
                raise MandateStateTransitionError("mandate state time moved backwards")
            state = replace(
                current,
                status=MandateStatus.REVOKED,
                updated_at=revoked_at,
                revoked_at=revoked_at,
            )
            self._states[(mandate_id, version)] = state
            self._append(
                state=state,
                event=MandateAuditEventType.MANDATE_REVOKED,
                previous_status=MandateStatus.ACTIVE,
                current_status=MandateStatus.REVOKED,
                recorded_at=revoked_at,
                reason="DEMO_USER_REVOCATION",
            )
            return state

    def supersede(
        self,
        mandate_id: str,
        version: int,
        *,
        superseded_by_version: int,
        updated_at: datetime,
    ) -> MandateState:
        _validate_time(updated_at, "updated_at")
        _validate_version(superseded_by_version)
        with self._lock:
            current = self.get_current(mandate_id)
            if current is None or current.version != version:
                raise MandateStateTransitionError("mandate version is not current")
            if current.status is not MandateStatus.ACTIVE:
                raise MandateStateTransitionError("only an ACTIVE mandate can be superseded")
            if superseded_by_version <= version:
                raise MandateStateTransitionError("replacement version must be newer")
            if updated_at < current.updated_at:
                raise MandateStateTransitionError("mandate state time moved backwards")
            old = replace(
                current,
                status=MandateStatus.SUPERSEDED,
                updated_at=updated_at,
                superseded_by_version=superseded_by_version,
            )
            new = MandateState(
                mandate_id=mandate_id,
                version=superseded_by_version,
                status=MandateStatus.ACTIVE,
                updated_at=updated_at,
            )
            self._states[(mandate_id, version)] = old
            self._states[(mandate_id, superseded_by_version)] = new
            self._current[mandate_id] = superseded_by_version
            self._append(
                state=old,
                event=MandateAuditEventType.MANDATE_SUPERSEDED,
                previous_status=MandateStatus.ACTIVE,
                current_status=MandateStatus.SUPERSEDED,
                recorded_at=updated_at,
                reason=f"SUPERSEDED_BY_VERSION_{superseded_by_version}",
            )
            self._append(
                state=new,
                event=MandateAuditEventType.MANDATE_REGISTERED_ACTIVE,
                previous_status=MandateStatus.SUPERSEDED,
                current_status=MandateStatus.ACTIVE,
                recorded_at=updated_at,
                reason=f"NEW_VERSION_AFTER_{version}",
            )
            return new

    def record_execution_refusal(
        self,
        *,
        mandate_id: str,
        version: int,
        occurred_at: datetime,
        reason: str,
        decision_nonce: str,
        execution_request_sha256: str,
        authorization_result_sha256: str,
    ) -> None:
        _validate_digest(execution_request_sha256, "execution_request_sha256")
        _validate_digest(authorization_result_sha256, "authorization_result_sha256")
        with self._lock:
            state = self.get_version(mandate_id, version) or MandateState(
                mandate_id=mandate_id,
                version=version,
                status=MandateStatus.ACTIVE,
                updated_at=occurred_at,
            )
            current = self.get_current(mandate_id)
            self._append(
                state=state,
                event=MandateAuditEventType.EXECUTION_REFUSED_MANDATE_STATE,
                previous_status=current.status if current else None,
                current_status=current.status if current else None,
                recorded_at=occurred_at,
                reason=reason,
                decision_nonce=decision_nonce,
                execution_request_sha256=execution_request_sha256,
                authorization_result_sha256=authorization_result_sha256,
            )

    def audit_events(self, mandate_id: str) -> tuple[dict[str, object], ...]:
        _validate_mandate_id(mandate_id)
        with self._lock:
            return tuple(dict(event) for event in self._audit.get(mandate_id, ()))

    @contextmanager
    def execution_guard(self) -> Iterator[None]:
        with self._lock:
            yield


class SQLiteMandateStateRegistry:
    """Persistent mandate state and append-only transition audit in SQLite."""

    def __init__(self, path: str | Path) -> None:
        if not isinstance(path, (str, Path)):
            raise TypeError("path must be a string or Path")
        self._connection = sqlite3.connect(
            str(path), timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._lock = RLock()
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mandate_states (
                    mandate_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED', 'SUPERSEDED')),
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT NULL,
                    superseded_by_version INTEGER NULL,
                    PRIMARY KEY (mandate_id, version)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mandate_current (
                    mandate_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mandate_state_audit (
                    mandate_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    previous_event_sha256 TEXT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (mandate_id, sequence)
                )
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    @staticmethod
    def _decode_state(row: tuple[object, ...] | None) -> MandateState | None:
        if row is None:
            return None
        return MandateState(
            mandate_id=str(row[0]),
            version=int(row[1]),
            status=MandateStatus(str(row[2])),
            updated_at=datetime.fromisoformat(str(row[3])),
            revoked_at=(None if row[4] is None else datetime.fromisoformat(str(row[4]))),
            superseded_by_version=(None if row[5] is None else int(row[5])),
        )

    def _get_version_unlocked(self, mandate_id: str, version: int) -> MandateState | None:
        row = self._connection.execute(
            """
            SELECT mandate_id, version, status, updated_at, revoked_at,
                   superseded_by_version
            FROM mandate_states WHERE mandate_id = ? AND version = ?
            """,
            (mandate_id, version),
        ).fetchone()
        return self._decode_state(row)

    def _get_current_unlocked(self, mandate_id: str) -> MandateState | None:
        row = self._connection.execute(
            """
            SELECT s.mandate_id, s.version, s.status, s.updated_at, s.revoked_at,
                   s.superseded_by_version
            FROM mandate_current c
            JOIN mandate_states s
              ON s.mandate_id = c.mandate_id AND s.version = c.version
            WHERE c.mandate_id = ?
            """,
            (mandate_id,),
        ).fetchone()
        return self._decode_state(row)

    def get_current(self, mandate_id: str) -> MandateState | None:
        _validate_mandate_id(mandate_id)
        with self._lock:
            return self._get_current_unlocked(mandate_id)

    def get_version(self, mandate_id: str, version: int) -> MandateState | None:
        _validate_mandate_id(mandate_id)
        _validate_version(version)
        with self._lock:
            return self._get_version_unlocked(mandate_id, version)

    def _store_state_unlocked(self, state: MandateState) -> None:
        self._connection.execute(
            """
            INSERT INTO mandate_states (
                mandate_id, version, status, updated_at, revoked_at,
                superseded_by_version
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(mandate_id, version) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at,
                revoked_at = excluded.revoked_at,
                superseded_by_version = excluded.superseded_by_version
            """,
            (
                state.mandate_id,
                state.version,
                state.status.value,
                _time_text(state.updated_at),
                _time_text(state.revoked_at) if state.revoked_at else None,
                state.superseded_by_version,
            ),
        )

    def _set_current_unlocked(self, mandate_id: str, version: int) -> None:
        self._connection.execute(
            """
            INSERT INTO mandate_current (mandate_id, version) VALUES (?, ?)
            ON CONFLICT(mandate_id) DO UPDATE SET version = excluded.version
            """,
            (mandate_id, version),
        )

    def _append_unlocked(
        self,
        *,
        state: MandateState,
        event: MandateAuditEventType,
        previous_status: MandateStatus | None,
        current_status: MandateStatus | None,
        recorded_at: datetime,
        reason: str,
        decision_nonce: str | None = None,
        execution_request_sha256: str | None = None,
        authorization_result_sha256: str | None = None,
    ) -> None:
        previous = self._connection.execute(
            """
            SELECT sequence, event_sha256 FROM mandate_state_audit
            WHERE mandate_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (state.mandate_id,),
        ).fetchone()
        sequence = 1 if previous is None else int(previous[0]) + 1
        previous_hash = None if previous is None else str(previous[1])
        body = _state_event_body(
            mandate_id=state.mandate_id,
            version=state.version,
            event=event,
            previous_status=previous_status,
            current_status=current_status,
            recorded_at=recorded_at,
            reason=reason,
            decision_nonce=decision_nonce,
            execution_request_sha256=execution_request_sha256,
            authorization_result_sha256=authorization_result_sha256,
            sequence=sequence,
            previous_event_sha256=previous_hash,
        )
        event_hash = sha256_canonical(body)
        self._connection.execute(
            """
            INSERT INTO mandate_state_audit (
                mandate_id, sequence, event_type, event_sha256,
                previous_event_sha256, event_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                state.mandate_id,
                sequence,
                event.value,
                event_hash,
                previous_hash,
                canonical_json_text({**body, "event_sha256": event_hash}),
            ),
        )

    def register_active(
        self, mandate_id: str, version: int, *, updated_at: datetime
    ) -> MandateState:
        _validate_mandate_id(mandate_id)
        _validate_version(version)
        _validate_time(updated_at, "updated_at")
        with self._transaction():
            current = self._get_current_unlocked(mandate_id)
            if current is not None:
                if current.version == version and current.status is MandateStatus.ACTIVE:
                    return current
                if version <= current.version:
                    raise MandateStateTransitionError(
                        "a revoked or superseded mandate version cannot be reactivated"
                    )
                if current.status is MandateStatus.ACTIVE:
                    raise MandateStateTransitionError(
                        "an ACTIVE mandate must be replaced through supersede"
                    )
                if updated_at < current.updated_at:
                    raise MandateStateTransitionError("mandate state time moved backwards")
            state = MandateState(
                mandate_id=mandate_id,
                version=version,
                status=MandateStatus.ACTIVE,
                updated_at=updated_at,
            )
            self._store_state_unlocked(state)
            self._set_current_unlocked(mandate_id, version)
            self._append_unlocked(
                state=state,
                event=MandateAuditEventType.MANDATE_REGISTERED_ACTIVE,
                previous_status=current.status if current else None,
                current_status=MandateStatus.ACTIVE,
                recorded_at=updated_at,
                reason="TRUSTED_SERVER_REGISTRATION",
            )
            return state

    def revoke(
        self, mandate_id: str, version: int, *, revoked_at: datetime
    ) -> MandateState:
        _validate_time(revoked_at, "revoked_at")
        with self._transaction():
            current = self._get_current_unlocked(mandate_id)
            if current is None:
                raise MandateStateTransitionError("mandate state is missing")
            if current.version != version:
                raise MandateStateTransitionError("mandate version is not current")
            if current.status is MandateStatus.REVOKED:
                return current
            if current.status is not MandateStatus.ACTIVE:
                raise MandateStateTransitionError("only an ACTIVE mandate can be revoked")
            if revoked_at < current.updated_at:
                raise MandateStateTransitionError("mandate state time moved backwards")
            state = replace(
                current,
                status=MandateStatus.REVOKED,
                updated_at=revoked_at,
                revoked_at=revoked_at,
            )
            self._store_state_unlocked(state)
            self._append_unlocked(
                state=state,
                event=MandateAuditEventType.MANDATE_REVOKED,
                previous_status=MandateStatus.ACTIVE,
                current_status=MandateStatus.REVOKED,
                recorded_at=revoked_at,
                reason="DEMO_USER_REVOCATION",
            )
            return state

    def supersede(
        self,
        mandate_id: str,
        version: int,
        *,
        superseded_by_version: int,
        updated_at: datetime,
    ) -> MandateState:
        _validate_time(updated_at, "updated_at")
        _validate_version(superseded_by_version)
        with self._transaction():
            current = self._get_current_unlocked(mandate_id)
            if current is None or current.version != version:
                raise MandateStateTransitionError("mandate version is not current")
            if current.status is not MandateStatus.ACTIVE:
                raise MandateStateTransitionError("only an ACTIVE mandate can be superseded")
            if superseded_by_version <= version:
                raise MandateStateTransitionError("replacement version must be newer")
            if updated_at < current.updated_at:
                raise MandateStateTransitionError("mandate state time moved backwards")
            old = replace(
                current,
                status=MandateStatus.SUPERSEDED,
                updated_at=updated_at,
                superseded_by_version=superseded_by_version,
            )
            new = MandateState(
                mandate_id=mandate_id,
                version=superseded_by_version,
                status=MandateStatus.ACTIVE,
                updated_at=updated_at,
            )
            self._store_state_unlocked(old)
            self._store_state_unlocked(new)
            self._set_current_unlocked(mandate_id, superseded_by_version)
            self._append_unlocked(
                state=old,
                event=MandateAuditEventType.MANDATE_SUPERSEDED,
                previous_status=MandateStatus.ACTIVE,
                current_status=MandateStatus.SUPERSEDED,
                recorded_at=updated_at,
                reason=f"SUPERSEDED_BY_VERSION_{superseded_by_version}",
            )
            self._append_unlocked(
                state=new,
                event=MandateAuditEventType.MANDATE_REGISTERED_ACTIVE,
                previous_status=MandateStatus.SUPERSEDED,
                current_status=MandateStatus.ACTIVE,
                recorded_at=updated_at,
                reason=f"NEW_VERSION_AFTER_{version}",
            )
            return new

    def record_execution_refusal(
        self,
        *,
        mandate_id: str,
        version: int,
        occurred_at: datetime,
        reason: str,
        decision_nonce: str,
        execution_request_sha256: str,
        authorization_result_sha256: str,
    ) -> None:
        _validate_digest(execution_request_sha256, "execution_request_sha256")
        _validate_digest(authorization_result_sha256, "authorization_result_sha256")

        def append() -> None:
            state = self._get_version_unlocked(mandate_id, version) or MandateState(
                mandate_id=mandate_id,
                version=version,
                status=MandateStatus.ACTIVE,
                updated_at=occurred_at,
            )
            current = self._get_current_unlocked(mandate_id)
            self._append_unlocked(
                state=state,
                event=MandateAuditEventType.EXECUTION_REFUSED_MANDATE_STATE,
                previous_status=current.status if current else None,
                current_status=current.status if current else None,
                recorded_at=occurred_at,
                reason=reason,
                decision_nonce=decision_nonce,
                execution_request_sha256=execution_request_sha256,
                authorization_result_sha256=authorization_result_sha256,
            )

        with self._lock:
            if self._connection.in_transaction:
                append()
            else:
                with self._transaction():
                    append()

    def audit_events(self, mandate_id: str) -> tuple[dict[str, object], ...]:
        _validate_mandate_id(mandate_id)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_json FROM mandate_state_audit
                WHERE mandate_id = ? ORDER BY sequence
                """,
                (mandate_id,),
            ).fetchall()
        events = tuple(json.loads(str(row[0])) for row in rows)
        previous_hash: str | None = None
        for sequence, event in enumerate(events, start=1):
            event_hash = event.get("event_sha256")
            body = {key: value for key, value in event.items() if key != "event_sha256"}
            if (
                event.get("sequence") != sequence
                or event.get("previous_event_sha256") != previous_hash
                or sha256_canonical(body) != event_hash
            ):
                raise MandateStateTransitionError(
                    "persisted mandate-state audit chain failed validation"
                )
            previous_hash = str(event_hash)
        return events

    @contextmanager
    def execution_guard(self) -> Iterator[None]:
        """Serialize state validation through provider return in this SQLite TCB."""

        with self._transaction():
            yield

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteMandateStateRegistry:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "InMemoryMandateStateRegistry",
    "MandateAuditEventType",
    "MandateState",
    "MandateStateRegistry",
    "MandateStateTransitionError",
    "MandateStatus",
    "SQLiteMandateStateRegistry",
]
