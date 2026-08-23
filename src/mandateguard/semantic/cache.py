"""Integrity-checked cache records for live Tier C reuse and hard replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.semantic.models import (
    NormalizedSemanticOutput,
    SemanticRequest,
    normalize_model_output,
    normalized_output_to_mapping,
    semantic_input_sha256,
    semantic_output_sha256,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORD_FIELDS = frozenset(
    {
        "semantic_input_sha256",
        "model_id",
        "prompt_version",
        "structured_model_result",
        "semantic_output_sha256",
        "provider_response_id",
    }
)


class SemanticCacheError(RuntimeError):
    """Base class for semantic cache failures."""


class SemanticCacheIntegrityError(SemanticCacheError):
    """A cache record failed input, output, or configuration integrity."""


class SemanticReplayMissError(SemanticCacheError):
    """Replay cannot proceed because no exact cached response exists."""


@dataclass(frozen=True, slots=True)
class SemanticCacheRecord:
    semantic_input_sha256: str
    model_id: str
    prompt_version: str
    structured_model_result: NormalizedSemanticOutput
    semantic_output_sha256: str
    provider_response_id: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.semantic_input_sha256, "semantic_input_sha256"),
            (self.semantic_output_sha256, "semantic_output_sha256"),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        if not isinstance(self.model_id, str) or not self.model_id or len(self.model_id) > 256:
            raise ValueError("model_id must be a bounded non-empty string")
        if (
            not isinstance(self.prompt_version, str)
            or not self.prompt_version
            or len(self.prompt_version) > 64
        ):
            raise ValueError("prompt_version must be a bounded non-empty string")
        if not isinstance(self.structured_model_result, NormalizedSemanticOutput):
            raise TypeError("structured_model_result must be NormalizedSemanticOutput")
        if (
            self.provider_response_id is not None
            and (
                not isinstance(self.provider_response_id, str)
                or not self.provider_response_id
                or len(self.provider_response_id) > 256
            )
        ):
            raise ValueError("provider_response_id must be null or a bounded string")
        if semantic_output_sha256(self.structured_model_result) != self.semantic_output_sha256:
            raise ValueError("semantic_output_sha256 does not commit structured_model_result")


def _record_to_mapping(record: SemanticCacheRecord) -> dict[str, Any]:
    return {
        "semantic_input_sha256": record.semantic_input_sha256,
        "model_id": record.model_id,
        "prompt_version": record.prompt_version,
        "structured_model_result": normalized_output_to_mapping(
            record.structured_model_result
        ),
        "semantic_output_sha256": record.semantic_output_sha256,
        "provider_response_id": record.provider_response_id,
    }


def _validate_record_for_request(
    record: SemanticCacheRecord, request: SemanticRequest
) -> None:
    expected_input_hash = semantic_input_sha256(request)
    if record.semantic_input_sha256 != expected_input_hash:
        raise SemanticCacheIntegrityError("cached semantic input hash mismatch")
    if record.model_id != request.model_id:
        raise SemanticCacheIntegrityError("cached model ID mismatch")
    if record.prompt_version != request.prompt_version:
        raise SemanticCacheIntegrityError("cached prompt version mismatch")
    if (
        semantic_output_sha256(record.structured_model_result)
        != record.semantic_output_sha256
    ):
        raise SemanticCacheIntegrityError("cached semantic output hash mismatch")
    expected_ids = tuple(item.constraint_id for item in request.constraints)
    actual_ids = tuple(
        item.constraint_id for item in record.structured_model_result.constraint_results
    )
    if actual_ids != expected_ids:
        raise SemanticCacheIntegrityError(
            "cached results do not exactly cover the requested constraints"
        )


@runtime_checkable
class SemanticCache(Protocol):
    def get(self, request: SemanticRequest) -> SemanticCacheRecord | None:
        """Return an exact verified record or None."""

    def put(self, request: SemanticRequest, record: SemanticCacheRecord) -> None:
        """Store one verified normalized result."""


@dataclass(slots=True, init=False)
class InMemorySemanticCache:
    """Deterministic cache used by unit tests and in-process callers."""

    _records: dict[str, SemanticCacheRecord]

    def __init__(
        self, records: Mapping[str, SemanticCacheRecord] | None = None
    ) -> None:
        if records is not None and not isinstance(records, Mapping):
            raise TypeError("records must be a mapping or None")
        self._records = dict(records or {})

    @property
    def records(self) -> Mapping[str, SemanticCacheRecord]:
        return MappingProxyType(self._records)

    def get(self, request: SemanticRequest) -> SemanticCacheRecord | None:
        if not isinstance(request, SemanticRequest):
            raise TypeError("request must be SemanticRequest")
        key = semantic_input_sha256(request)
        record = self._records.get(key)
        if record is None:
            return None
        if not isinstance(record, SemanticCacheRecord):
            raise SemanticCacheIntegrityError("cached value has an invalid type")
        _validate_record_for_request(record, request)
        return record

    def put(self, request: SemanticRequest, record: SemanticCacheRecord) -> None:
        if not isinstance(request, SemanticRequest):
            raise TypeError("request must be SemanticRequest")
        if not isinstance(record, SemanticCacheRecord):
            raise TypeError("record must be SemanticCacheRecord")
        _validate_record_for_request(record, request)
        key = semantic_input_sha256(request)
        existing = self._records.get(key)
        if existing is not None and existing != record:
            raise SemanticCacheIntegrityError(
                "refusing to replace an existing semantic cache record"
            )
        self._records[key] = record


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point values are not allowed: {value}")


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _decode_record(raw: str, request: SemanticRequest) -> SemanticCacheRecord:
    decoded = json.loads(
        raw,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_float=_reject_float,
        parse_constant=_reject_non_json_number,
    )
    if not isinstance(decoded, dict) or frozenset(decoded) != _RECORD_FIELDS:
        raise ValueError("semantic cache record has unexpected or missing fields")
    normalized = normalize_model_output(
        decoded["structured_model_result"], request.constraints
    )
    return SemanticCacheRecord(
        semantic_input_sha256=decoded["semantic_input_sha256"],
        model_id=decoded["model_id"],
        prompt_version=decoded["prompt_version"],
        structured_model_result=normalized,
        semantic_output_sha256=decoded["semantic_output_sha256"],
        provider_response_id=decoded["provider_response_id"],
    )


@dataclass(frozen=True, slots=True)
class FileSemanticCache:
    """Persistent one-record-per-input cache for historical replay."""

    directory: Path

    def __post_init__(self) -> None:
        if not isinstance(self.directory, Path):
            raise TypeError("directory must be pathlib.Path PSP configuration")

    def _record_path(self, semantic_input_hash: str) -> Path:
        if not _SHA256_RE.fullmatch(semantic_input_hash):
            raise ValueError("semantic input hash is invalid")
        return self.directory / f"{semantic_input_hash}.json"

    def get(self, request: SemanticRequest) -> SemanticCacheRecord | None:
        if not isinstance(request, SemanticRequest):
            raise TypeError("request must be SemanticRequest")
        path = self._record_path(semantic_input_sha256(request))
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise SemanticCacheIntegrityError(
                "semantic cache record is unreadable"
            ) from exc
        try:
            record = _decode_record(raw, request)
            _validate_record_for_request(record, request)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, SemanticCacheIntegrityError):
                raise
            raise SemanticCacheIntegrityError(
                "semantic cache record failed structural validation"
            ) from exc
        return record

    def put(self, request: SemanticRequest, record: SemanticCacheRecord) -> None:
        if not isinstance(request, SemanticRequest):
            raise TypeError("request must be SemanticRequest")
        if not isinstance(record, SemanticCacheRecord):
            raise TypeError("record must be SemanticCacheRecord")
        _validate_record_for_request(record, request)
        path = self._record_path(record.semantic_input_sha256)
        if path.exists():
            existing = self.get(request)
            if existing != record:
                raise SemanticCacheIntegrityError(
                    "refusing to replace an existing semantic cache record"
                )
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".semantic-", suffix=".tmp", dir=self.directory, delete=False
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(canonical_json_bytes(_record_to_mapping(record)))
                handle.flush()
            temporary_path.replace(path)
        except OSError as exc:
            raise SemanticCacheError("semantic cache record could not be stored") from exc
