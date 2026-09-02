"""SQLite semantic-result cache inside the MandateGuard trusted computing base.

The unkeyed SHA-256 commitments detect accidental corruption and inconsistent
records. They do not provide tamper resistance against a malicious process or
operator with write access to the cache database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import sha256_canonical
from mandateguard.intelligence.models import CacheStatus
from mandateguard.semantic.cache import (
    InMemorySemanticCache,
    SemanticCacheError,
    SemanticCacheIntegrityError,
    SemanticCacheRecord,
)
from mandateguard.semantic.models import (
    SemanticRequest,
    normalize_model_output,
    normalized_output_to_mapping,
    reduce_semantic_verdict,
    semantic_input_sha256,
)


_SELECT = """
    SELECT semantic_input_sha256, model_id, prompt_version, verdict,
           structured_model_result_json, semantic_output_sha256,
           created_at, record_sha256
    FROM semantic_decision_cache
    WHERE cache_key = ?
"""


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _record_commitment(
    *,
    cache_key: str,
    semantic_input_hash: str,
    model_id: str,
    prompt_version: str,
    verdict: str,
    structured_result_json: str,
    semantic_output_hash: str,
    created_at: str,
) -> str:
    return sha256_canonical(
        {
            "cache_key": cache_key,
            "semantic_input_sha256": semantic_input_hash,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "verdict": verdict,
            "structured_model_result_json": structured_result_json,
            "semantic_output_sha256": semantic_output_hash,
            "created_at": created_at,
        }
    )


class SQLiteSemanticCache:
    """Persistent exact-input cache with structural and content integrity checks.

    ``semantic_input_sha256`` already commits the detector/prompt/model IDs,
    mandate payload, transaction body, catalog snapshot, trusted evidence hash,
    constraints, and selected evidence. No SKU- or prose-only lookup exists.
    The database is therefore part of the trusted computing base; the record
    commitment is an error-detection mechanism, not authentication of storage.
    """

    __slots__ = (
        "_connection",
        "_lock",
        "_clock",
        "last_status",
        "last_integrity_failure",
        "last_key",
    )

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(path, (str, Path)):
            raise TypeError("path must be a string or Path")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._connection = sqlite3.connect(
            str(path), timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._lock = RLock()
        self.last_status: CacheStatus | None = None
        self.last_integrity_failure = False
        self.last_key: str | None = None
        try:
            with self._lock:
                self._connection.execute("PRAGMA busy_timeout = 5000")
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS semantic_decision_cache (
                        cache_key TEXT PRIMARY KEY,
                        semantic_input_sha256 TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        prompt_version TEXT NOT NULL,
                        verdict TEXT NOT NULL CHECK (
                            verdict IN ('PASS', 'VIOLATION', 'ABSTAIN')
                        ),
                        structured_model_result_json TEXT NOT NULL,
                        semantic_output_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        record_sha256 TEXT NOT NULL
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in self._connection.execute(
                        "PRAGMA table_info(semantic_decision_cache)"
                    )
                }
                if "verdict" not in columns:
                    # Safe compatibility for pre-INT-1 local development caches.
                    # Legacy rows lack a verdict and will fail closed on lookup.
                    self._connection.execute(
                        "ALTER TABLE semantic_decision_cache ADD COLUMN verdict TEXT"
                    )
        except sqlite3.Error as exc:
            raise SemanticCacheError("semantic cache could not be initialized") from exc

    def get(self, request: SemanticRequest) -> SemanticCacheRecord | None:
        if not isinstance(request, SemanticRequest):
            raise TypeError("request must be SemanticRequest")
        key = semantic_input_sha256(request)
        self.last_key = key
        self.last_integrity_failure = False
        try:
            with self._lock:
                row = self._connection.execute(_SELECT, (key,)).fetchone()
        except sqlite3.Error as exc:
            self.last_status = CacheStatus.MISS
            raise SemanticCacheError("semantic cache lookup failed") from exc
        if row is None:
            self.last_status = CacheStatus.MISS
            return None
        try:
            (
                stored_input_hash,
                model_id,
                prompt_version,
                verdict,
                structured_result_json,
                output_hash,
                created_at,
                stored_record_hash,
            ) = row
            expected_record_hash = _record_commitment(
                cache_key=key,
                semantic_input_hash=stored_input_hash,
                model_id=model_id,
                prompt_version=prompt_version,
                verdict=verdict,
                structured_result_json=structured_result_json,
                semantic_output_hash=output_hash,
                created_at=created_at,
            )
            if stored_record_hash != expected_record_hash:
                raise ValueError("record commitment mismatch")
            parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
                raise ValueError("created_at must be timezone-aware")
            decoded = json.loads(
                structured_result_json,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_json_number,
            )
            normalized = normalize_model_output(decoded, request.constraints)
            if reduce_semantic_verdict(normalized.constraint_results).value != verdict:
                raise ValueError("stored verdict does not match normalized results")
            record = SemanticCacheRecord(
                semantic_input_sha256=stored_input_hash,
                model_id=model_id,
                prompt_version=prompt_version,
                structured_model_result=normalized,
                semantic_output_sha256=output_hash,
                provider_response_id=None,
            )
            InMemorySemanticCache({key: record}).get(request)
        except (TypeError, ValueError, OverflowError) as exc:
            self.last_status = CacheStatus.MISS
            self.last_integrity_failure = True
            raise SemanticCacheIntegrityError(
                "semantic cache record failed integrity validation"
            ) from exc
        self.last_status = CacheStatus.HIT
        return record

    def put(self, request: SemanticRequest, record: SemanticCacheRecord) -> None:
        if not isinstance(request, SemanticRequest):
            raise TypeError("request must be SemanticRequest")
        if not isinstance(record, SemanticCacheRecord):
            raise TypeError("record must be SemanticCacheRecord")
        key = semantic_input_sha256(request)
        InMemorySemanticCache().put(request, record)
        existing = self.get(request)
        if existing is not None:
            if existing != SemanticCacheRecord(
                semantic_input_sha256=record.semantic_input_sha256,
                model_id=record.model_id,
                prompt_version=record.prompt_version,
                structured_model_result=record.structured_model_result,
                semantic_output_sha256=record.semantic_output_sha256,
                provider_response_id=None,
            ):
                raise SemanticCacheIntegrityError(
                    "refusing to replace an existing semantic cache record"
                )
            return
        created = self._clock()
        if (
            not isinstance(created, datetime)
            or created.tzinfo is None
            or created.utcoffset() is None
        ):
            raise SemanticCacheError("semantic cache clock must be timezone-aware")
        created_at = (
            created.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        structured_result_json = canonical_json_text(
            normalized_output_to_mapping(record.structured_model_result)
        )
        verdict = reduce_semantic_verdict(
            record.structured_model_result.constraint_results
        ).value
        commitment = _record_commitment(
            cache_key=key,
            semantic_input_hash=record.semantic_input_sha256,
            model_id=record.model_id,
            prompt_version=record.prompt_version,
            verdict=verdict,
            structured_result_json=structured_result_json,
            semantic_output_hash=record.semantic_output_sha256,
            created_at=created_at,
        )
        try:
            with self._lock:
                self._connection.execute(
                    """
                    INSERT INTO semantic_decision_cache (
                        cache_key, semantic_input_sha256, model_id,
                        prompt_version, verdict, structured_model_result_json,
                        semantic_output_sha256, created_at, record_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        record.semantic_input_sha256,
                        record.model_id,
                        record.prompt_version,
                        verdict,
                        structured_result_json,
                        record.semantic_output_sha256,
                        created_at,
                        commitment,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SemanticCacheIntegrityError(
                "refusing to replace an existing semantic cache record"
            ) from exc
        except sqlite3.Error as exc:
            raise SemanticCacheError("semantic cache record could not be stored") from exc

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteSemanticCache:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
