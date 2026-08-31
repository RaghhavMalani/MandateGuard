"""Deterministic JSON serialization for INT-3 result artifacts.

The core canonical serializer intentionally rejects floats because it protects
hash-bound authorization structures.  INT-3 result artifacts contain frozen
model features and measured timings, so they need a separate, narrowly scoped
serializer that accepts finite Python floats without weakening core hashing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from typing import Any


class ResultArtifactSerializationError(ValueError):
    """Raised when a value cannot be represented in an INT-3 result artifact."""


def _normalize_result_value(value: Any, path: str) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResultArtifactSerializationError(
                f"non-finite float is forbidden in result artifacts at {path}"
            )
        # JSON has no distinct negative-zero value.  Emit a single stable zero
        # representation while leaving every non-zero float unchanged.
        return 0.0 if value == 0.0 else value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ResultArtifactSerializationError(
                    f"result artifact object keys must be strings at {path}"
                )
            normalized[key] = _normalize_result_value(item, f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [
            _normalize_result_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ResultArtifactSerializationError(
        f"unsupported result artifact value {type(value).__name__} at {path}"
    )


def result_artifact_json_bytes(value: Any) -> bytes:
    """Return deterministic compact UTF-8 JSON for an INT-3 result value."""

    normalized = _normalize_result_value(value, "$")
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ResultArtifactSerializationError(
            "value cannot be serialized as an INT-3 result artifact"
        ) from error
    return text.encode("utf-8")


def result_artifact_json_text(value: Any) -> str:
    """Return deterministic compact JSON text for an INT-3 result value."""

    return result_artifact_json_bytes(value).decode("utf-8")
