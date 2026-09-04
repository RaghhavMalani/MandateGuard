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


MAGIC = b"MGDX01\n"
_MAX_HEADER_BYTES = 4_194_304


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


def write_artifact(
    path: Path, header: Mapping[str, object], sections: Mapping[str, bytes]
) -> tuple[int, str]:
    """Serialize one artifact; return ``(bytes_written, sha256)``."""

    if not isinstance(header, Mapping):
        raise TypeError("header must be a mapping")
    order: list[dict[str, object]] = []
    offset = 0
    body = bytearray()
    for name in sorted(sections):
        payload = sections[name]
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError(f"section {name!r} must be bytes")
        blob = bytes(payload)
        order.append({"name": name, "offset": offset, "length": len(blob)})
        body += blob
        offset += len(blob)
    full_header = dict(header)
    full_header["sections"] = order
    encoded = json.dumps(
        full_header, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > _MAX_HEADER_BYTES:
        raise ArtifactError("artifact header is too large")
    payload = MAGIC + len(encoded).to_bytes(4, "big") + encoded + bytes(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return len(payload), sha256(payload).hexdigest()


def read_artifact(path: Path) -> Artifact:
    """Load one artifact fully into memory."""

    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise ArtifactError(f"artifact {path} could not be read") from error
    if not payload.startswith(MAGIC):
        raise ArtifactError(f"artifact {path} is not a MandateGuard index artifact")
    cursor = len(MAGIC)
    header_length = int.from_bytes(payload[cursor : cursor + 4], "big")
    cursor += 4
    if header_length <= 0 or header_length > _MAX_HEADER_BYTES:
        raise ArtifactError("artifact header length is invalid")
    try:
        header = json.loads(payload[cursor : cursor + header_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactError("artifact header is not valid JSON") from error
    if not isinstance(header, dict):
        raise ArtifactError("artifact header must be a JSON object")
    cursor += header_length
    body = memoryview(payload)[cursor:]
    entries = header.pop("sections", None)
    if not isinstance(entries, list):
        raise ArtifactError("artifact header does not declare its sections")
    sections: dict[str, bytes] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("offset"), int)
            or not isinstance(entry.get("length"), int)
        ):
            raise ArtifactError("artifact section table is malformed")
        start = entry["offset"]
        end = start + entry["length"]
        if start < 0 or end > len(body):
            raise ArtifactError(f"artifact section {entry['name']!r} is out of bounds")
        sections[entry["name"]] = bytes(body[start:end])
    return Artifact(header=header, sections=sections)


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
    count = len(offsets) // 4 - 1
    if count < 0:
        raise ArtifactError("string table offsets are malformed")
    items: list[str] = []
    previous = int.from_bytes(offsets[0:4], "big")
    for index in range(count):
        end = int.from_bytes(offsets[(index + 1) * 4 : (index + 2) * 4], "big")
        items.append(joined[previous:end].decode("utf-8"))
        previous = end
    return items
