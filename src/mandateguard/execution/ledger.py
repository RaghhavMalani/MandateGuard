"""Atomic, process-persistent single-use ledger for D6 execution nonces."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from threading import RLock
from typing import Protocol

from mandateguard.execution.models import (
    ExecutionLedgerRecord,
    ExecutionLedgerStatus,
)


class ExecutionLedger(Protocol):
    def reserve(self, decision_nonce: str, execution_request_sha256: str) -> bool: ...

    def mark_succeeded(
        self,
        decision_nonce: str,
        execution_request_sha256: str,
        razorpay_order_id: str,
    ) -> bool: ...

    def mark_rejected(
        self, decision_nonce: str, execution_request_sha256: str
    ) -> bool: ...

    def mark_uncertain(
        self, decision_nonce: str, execution_request_sha256: str
    ) -> bool: ...


class SQLiteExecutionLedger:
    """SQLite-backed nonce reservation with a primary-key uniqueness boundary."""

    __slots__ = ("_connection", "_lock")

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
                CREATE TABLE IF NOT EXISTS execution_ledger (
                    decision_nonce TEXT PRIMARY KEY,
                    execution_request_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('RESERVED', 'SUCCEEDED', 'REJECTED', 'UNCERTAIN')
                    ),
                    razorpay_order_id TEXT NULL
                )
                """
            )

    def reserve(self, decision_nonce: str, execution_request_sha256: str) -> bool:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """
                    INSERT INTO execution_ledger (
                        decision_nonce, execution_request_sha256, status,
                        razorpay_order_id
                    ) VALUES (?, ?, 'RESERVED', NULL)
                    """,
                    (decision_nonce, execution_request_sha256),
                )
                self._connection.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                self._connection.execute("ROLLBACK")
                return False
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _transition(
        self,
        *,
        decision_nonce: str,
        execution_request_sha256: str,
        status: ExecutionLedgerStatus,
        razorpay_order_id: str | None,
    ) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE execution_ledger
                SET status = ?, razorpay_order_id = ?
                WHERE decision_nonce = ?
                  AND execution_request_sha256 = ?
                  AND status = 'RESERVED'
                """,
                (
                    status.value,
                    razorpay_order_id,
                    decision_nonce,
                    execution_request_sha256,
                ),
            )
            return cursor.rowcount == 1

    def mark_succeeded(
        self,
        decision_nonce: str,
        execution_request_sha256: str,
        razorpay_order_id: str,
    ) -> bool:
        return self._transition(
            decision_nonce=decision_nonce,
            execution_request_sha256=execution_request_sha256,
            status=ExecutionLedgerStatus.SUCCEEDED,
            razorpay_order_id=razorpay_order_id,
        )

    def mark_rejected(
        self, decision_nonce: str, execution_request_sha256: str
    ) -> bool:
        return self._transition(
            decision_nonce=decision_nonce,
            execution_request_sha256=execution_request_sha256,
            status=ExecutionLedgerStatus.REJECTED,
            razorpay_order_id=None,
        )

    def mark_uncertain(
        self, decision_nonce: str, execution_request_sha256: str
    ) -> bool:
        return self._transition(
            decision_nonce=decision_nonce,
            execution_request_sha256=execution_request_sha256,
            status=ExecutionLedgerStatus.UNCERTAIN,
            razorpay_order_id=None,
        )

    def get(self, decision_nonce: str) -> ExecutionLedgerRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT decision_nonce, execution_request_sha256, status,
                       razorpay_order_id
                FROM execution_ledger
                WHERE decision_nonce = ?
                """,
                (decision_nonce,),
            ).fetchone()
        if row is None:
            return None
        return ExecutionLedgerRecord(
            decision_nonce=row[0],
            execution_request_sha256=row[1],
            status=ExecutionLedgerStatus(row[2]),
            razorpay_order_id=row[3],
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteExecutionLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
