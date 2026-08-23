"""Population of the frozen ``benchmark/MANIFEST.yaml`` cases list.

The manifest preamble - ``schema_version``, ``artifact_status``,
``case_schema``, ``enums``, ``field_rules``, ``hash_policy``, and
``freeze_policy`` - is frozen. This module reproduces it byte-for-byte and
refuses to write anything if it has drifted, so D7 cannot quietly amend the
schema to make generation easier. The only content this module adds is the
``cases`` list.

No third-party YAML library is used: the emitted records are a fixed,
flat, ASCII shape that is trivially serializable and trivially auditable.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re

from mandateguard.benchmark.codec import encode_timestamp
from mandateguard.benchmark.models import BenchmarkCase


FROZEN_PREAMBLE_SHA256 = (
    "d172781fd23a0096c54dab66206db1a0e1442dcdbab42e6333934e01ac0efc95"
)

MANIFEST_CASE_FIELDS = (
    "case_id",
    "evidence_tier",
    "family_id",
    "provenance",
    "split",
    "ground_truth",
    "label_source",
    "label_recorded_at",
    "case_content_sha256",
    "first_run_at",
    "expected_action",
)

_CASES_KEY_RE = re.compile(r"^cases:", re.MULTILINE)
_SAFE_SCALAR_RE = re.compile(r"^[A-Za-z0-9:_.\-]+$")


class ManifestPreambleError(RuntimeError):
    """Raised when the frozen manifest preamble does not match expectations."""


def normalized_manifest_text(raw: bytes) -> str:
    return raw.decode("utf-8").replace("\r\n", "\n")


def frozen_preamble(manifest_path: Path) -> str:
    """Return the frozen preamble, failing loudly if it has been modified."""

    text = normalized_manifest_text(manifest_path.read_bytes())
    match = _CASES_KEY_RE.search(text)
    if match is None:
        raise ManifestPreambleError(
            f"{manifest_path} has no top-level cases key to populate"
        )
    if _CASES_KEY_RE.search(text, match.end()) is not None:
        raise ManifestPreambleError(
            f"{manifest_path} declares more than one top-level cases key"
        )
    preamble = text[: match.start()]
    digest = sha256(preamble.encode("utf-8")).hexdigest()
    if digest != FROZEN_PREAMBLE_SHA256:
        raise ManifestPreambleError(
            "frozen manifest preamble mismatch: expected "
            f"{FROZEN_PREAMBLE_SHA256}, found {digest}. The schema, enums, hash "
            "policy, and freeze policy are frozen and D7 may not amend them."
        )
    return preamble


def _scalar(value: str | None) -> str:
    if value is None:
        return "null"
    if not _SAFE_SCALAR_RE.fullmatch(value):
        raise ManifestPreambleError(
            f"manifest scalar {value!r} contains characters this writer refuses "
            "to quote"
        )
    return f'"{value}"'


def manifest_record(case: BenchmarkCase, content_sha256: str) -> dict[str, str | None]:
    record = {
        "case_id": case.case_id,
        "evidence_tier": case.evidence_tier,
        "family_id": case.family_id,
        "provenance": case.provenance,
        "split": case.split,
        "ground_truth": case.ground_truth,
        "label_source": case.label_source,
        "label_recorded_at": encode_timestamp(case.label_recorded_at),
        "case_content_sha256": content_sha256,
        "first_run_at": None,
        "expected_action": case.expected_action,
    }
    if tuple(record) != MANIFEST_CASE_FIELDS:
        raise ManifestPreambleError("manifest record fields drifted from the schema")
    return record


def render_cases_block(records: list[dict[str, str | None]]) -> str:
    """Emit the ``cases`` list in the registered manifest order."""

    if not records:
        raise ManifestPreambleError("refusing to write an empty cases list")
    lines = ["cases:"]
    for record in records:
        first = True
        for field in MANIFEST_CASE_FIELDS:
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{field}: {_scalar(record[field])}")
            first = False
    return "\n".join(lines) + "\n"


def render_manifest(preamble: str, records: list[dict[str, str | None]]) -> str:
    return preamble + render_cases_block(records)
