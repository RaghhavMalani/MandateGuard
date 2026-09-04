"""Frozen index artifact container.

An artifact is a small, self-describing binary file: a JSON header followed by
named byte sections. Reading it needs nothing beyond the standard library, which
is what keeps the public runtime image dependency-free while the trainer that
produced it uses NumPy and scikit-learn.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping
import re


MAGIC = b"MGDX01\n"
ARTIFACT_SCHEMA_VERSION = "mgdx-container-v2"
_MAX_HEADER_BYTES = 4_194_304
_MAX_ARTIFACT_BYTES = 134_217_728
_MAX_SECTION_BYTES = 67_108_864
_MAX_SECTIONS = 128
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(RuntimeError):
    """A frozen artifact is missing, malformed, or built by another version."""


@dataclass(frozen=True, slots=True)
class Artifact:
    header: Mapping[str, object]
    sections: Mapping[str, bytes]

    def section(self, name: str) -> bytes:
        try:
            return self.sections[name]
        except KeyError as error:
            raise ArtifactError(f"artifact section {name!r} is missing") from error

    def require(self, name: str) -> object:
        if name not in self.header:
            raise ArtifactError(f"artifact header field {name!r} is missing")
        return self.header[name]


def validate_catalog_binding(
    artifact: Artifact,
    *,
    expected_catalog_sha256: str | None = None,
    expected_document_count: int | None = None,
) -> tuple[str, int]:
    """Validate the catalog identity carried by every discovery artifact."""

    digest = artifact.require("catalog_sha256")
    count = artifact.require("document_count")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ArtifactError("artifact catalog digest is invalid")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ArtifactError("artifact document count is invalid")
    if expected_catalog_sha256 is not None and digest != expected_catalog_sha256:
        raise ArtifactError("artifact catalog digest does not match the loaded catalog")
    if expected_document_count is not None and count != expected_document_count:
        raise ArtifactError("artifact document count does not match the loaded catalog")
    return digest, count


def write_artifact(
    path: Path, header: Mapping[str, object], sections: Mapping[str, bytes]
) -> tuple[int, str]:
    """Serialize one artifact; return ``(bytes_written, sha256)``."""

    if not isinstance(header, Mapping):
        raise TypeError("header must be a mapping")
    if len(sections) > _MAX_SECTIONS:
        raise ArtifactError("artifact declares too many sections")
    order: list[dict[str, object]] = []
    offset = 0
    body = bytearray()
    for name in sorted(sections):
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 128
            or not all(character.isalnum() or character in "._-" for character in name)
        ):
            raise ArtifactError("artifact section name is invalid")
        payload = sections[name]
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError(f"section {name!r} must be bytes")
        blob = bytes(payload)
        if len(blob) > _MAX_SECTION_BYTES:
            raise ArtifactError(f"artifact section {name!r} is too large")
        order.append({"name": name, "offset": offset, "length": len(blob)})
        body += blob
        offset += len(blob)
    full_header = dict(header)
    if "sections" in full_header or "artifact_schema_version" in full_header:
        raise ArtifactError("artifact container fields are reserved")
    full_header["artifact_schema_version"] = ARTIFACT_SCHEMA_VERSION
    full_header["sections"] = order
    encoded = json.dumps(
        full_header, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > _MAX_HEADER_BYTES:
        raise ArtifactError("artifact header is too large")
    payload = MAGIC + len(encoded).to_bytes(4, "big") + encoded + bytes(body)
    if len(payload) > _MAX_ARTIFACT_BYTES:
        raise ArtifactError("artifact is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return len(payload), sha256(payload).hexdigest()


def read_artifact(path: Path) -> Artifact:
    """Load one artifact fully into memory."""

    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise ArtifactError("artifact could not be read") from error
    if len(payload) > _MAX_ARTIFACT_BYTES:
        raise ArtifactError("artifact is too large")
    if not payload.startswith(MAGIC):
        raise ArtifactError("file is not a MandateGuard index artifact")
    cursor = len(MAGIC)
    if len(payload) < cursor + 4:
        raise ArtifactError("artifact header length is truncated")
    header_length = int.from_bytes(payload[cursor : cursor + 4], "big")
    cursor += 4
    if header_length <= 0 or header_length > _MAX_HEADER_BYTES:
        raise ArtifactError("artifact header length is invalid")
    if cursor + header_length > len(payload):
        raise ArtifactError("artifact header is truncated")
    try:
        header = json.loads(
            payload[cursor : cursor + header_length].decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ArtifactError("artifact header is not valid JSON") from error
    if not isinstance(header, dict):
        raise ArtifactError("artifact header must be a JSON object")
    cursor += header_length
    body = memoryview(payload)[cursor:]
    if header.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactError("artifact schema version is unsupported")
    entries = header.pop("sections", None)
    if not isinstance(entries, list):
        raise ArtifactError("artifact header does not declare its sections")
    if len(entries) > _MAX_SECTIONS:
        raise ArtifactError("artifact declares too many sections")
    sections: dict[str, bytes] = {}
    spans: list[tuple[int, int, str]] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"name", "offset", "length"}
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("offset"), int)
            or not isinstance(entry.get("length"), int)
            or isinstance(entry.get("offset"), bool)
            or isinstance(entry.get("length"), bool)
        ):
            raise ArtifactError("artifact section table is malformed")
        name = entry["name"]
        if (
            not name
            or len(name) > 128
            or not all(character.isalnum() or character in "._-" for character in name)
        ):
            raise ArtifactError("artifact section name is invalid")
        if name in sections:
            raise ArtifactError("artifact declares a duplicate section name")
        start = entry["offset"]
        length = entry["length"]
        if start < 0 or length < 0:
            raise ArtifactError("artifact section offset or length is negative")
        if length > _MAX_SECTION_BYTES:
            raise ArtifactError("artifact section is impossibly large")
        end = start + length
        if start > len(body) or end > len(body):
            raise ArtifactError(f"artifact section {name!r} is out of bounds")
        sections[name] = bytes(body[start:end])
        spans.append((start, end, name))
    spans.sort()
    expected_start = 0
    for start, end, _name in spans:
        if start < expected_start:
            raise ArtifactError("artifact sections overlap")
        if start != expected_start:
            raise ArtifactError("artifact contains unreferenced bytes between sections")
        expected_start = end
    if expected_start != len(body):
        raise ArtifactError("artifact contains unexpected trailing bytes")
    return Artifact(header=header, sections=sections)


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def pack_varints(values: list[int]) -> bytes:
    """LEB128-style unsigned varints. Used for delta-encoded posting lists."""

    out = bytearray()
    for value in values:
        if value < 0:
            raise ValueError("varints must be non-negative")
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
    return bytes(out)


def unpack_varints(blob: bytes, start: int, count: int) -> tuple[list[int], int]:
    """Decode ``count`` varints beginning at ``start``; return (values, cursor)."""

    values: list[int] = []
    cursor = start
    length = len(blob)
    for _ in range(count):
        shift = 0
        value = 0
        while True:
            if cursor >= length:
                raise ArtifactError("varint stream ended early")
            byte = blob[cursor]
            cursor += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift > 63:
                raise ArtifactError("varint is too large")
        values.append(value)
    return values, cursor


def pack_string_table(items: list[str]) -> tuple[bytes, bytes]:
    """Return ``(joined_utf8, offsets_uint32)`` for a sorted string table."""

    joined = bytearray()
    offsets = bytearray()
    offsets += (0).to_bytes(4, "big")
    for item in items:
        joined += item.encode("utf-8")
        offsets += len(joined).to_bytes(4, "big")
    return bytes(joined), bytes(offsets)


def unpack_string_table(joined: bytes, offsets: bytes) -> list[str]:
    if len(offsets) < 4 or len(offsets) % 4:
        raise ArtifactError("string table offsets are malformed")
    count = len(offsets) // 4 - 1
    if count < 0:
        raise ArtifactError("string table offsets are malformed")
    items: list[str] = []
    previous = int.from_bytes(offsets[0:4], "big")
    if previous != 0:
        raise ArtifactError("string table must begin at offset zero")
    for index in range(count):
        end = int.from_bytes(offsets[(index + 1) * 4 : (index + 2) * 4], "big")
        if end < previous or end > len(joined):
            raise ArtifactError("string table offset is out of bounds")
        try:
            items.append(joined[previous:end].decode("utf-8"))
        except UnicodeDecodeError as error:
            raise ArtifactError("string table contains invalid UTF-8") from error
        previous = end
    if previous != len(joined):
        raise ArtifactError("string table contains unexpected trailing bytes")
    return items
