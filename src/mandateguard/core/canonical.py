"""MandateGuard canonical JSON serialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by canonical JSON."""


class FloatNotAllowedError(CanonicalizationError):
    """Raised whenever a float occurs at any depth of the input."""


def _normalize(value: Any, path: str) -> Any:
    if isinstance(value, float):
        raise FloatNotAllowedError(f"floats are forbidden in canonical structures at {path}")
    if isinstance(value, Enum):
        return _normalize(value.value, path)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalizationError(f"datetime must be timezone-aware at {path}")
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _normalize(getattr(value, field.name), f"{path}.{field.name}")
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"canonical object keys must be strings at {path}")
            normalized[key] = _normalize(item, f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise CanonicalizationError(
        f"unsupported canonical value {type(value).__name__} at {path}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize to sorted, whitespace-free, UTF-8 JSON and reject floats recursively."""

    normalized = _normalize(value, "$")
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CanonicalizationError("value cannot be serialized as canonical JSON") from error
    return text.encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")
