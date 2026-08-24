"""Tier C records for the frozen ``benchmark/MANIFEST.yaml``.

These are **pure helpers**. Nothing here writes a file. D8-A adds no Tier C
record to the manifest: the committed manifest keeps exactly the 1,008 executed
Tier A/B records, and a later step appends the 220 development records only
after the cases are authored, adjudicated, and hashed.

Schema conformance
------------------

A Tier C record carries exactly the ten ``case_schema.required_fields`` and
omits the optional ``expected_action``, because the frozen
``field_rules.expected_action`` says *"Tier C cases omit expected_action"*. That
is the only shape difference from the Tier A/B records, which do carry it.

The manifest schema is not extended. Any Tier C metadata beyond these ten
fields - provenance origin, adjudication, second review, exclusions - lives in
the Tier C corpus records and audit files, never in ``MANIFEST.yaml``.
"""

from __future__ import annotations

import re

from mandateguard.benchmark.codec import encode_timestamp
from mandateguard.benchmark.tier_c.codec import case_content_sha256
from mandateguard.benchmark.tier_c.models import TierCCase, TierCCaseError


#: The ten ``required_fields`` of the frozen manifest ``case_schema``, in the
#: order the manifest lists them. ``expected_action`` is deliberately absent.
TIER_C_MANIFEST_FIELDS = (
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
)

_SAFE_SCALAR_RE = re.compile(r"^[A-Za-z0-9:_.\-]+$")


def manifest_record(case: TierCCase) -> dict[str, str | None]:
    """One manifest record for an adjudicated, hashed Tier C case."""

    ground_truth = case.ground_truth
    label_recorded_at = case.label_recorded_at
    if ground_truth is None or label_recorded_at is None:
        raise TierCCaseError(
            f"case {case.case_id} needs a recorded human label before it can "
            "enter the manifest (protocol 5)"
        )
    if case.exclusion is not None:
        raise TierCCaseError(
            f"case {case.case_id} is excluded and never enters the executable "
            "benchmark (protocol 5.2)"
        )
    record = {
        "case_id": case.case_id,
        "evidence_tier": case.evidence_tier,
        "family_id": case.family_id,
        "provenance": case.provenance.value,
        "split": case.split.value,
        "ground_truth": ground_truth.value,
        "label_source": case.label_source,
        "label_recorded_at": encode_timestamp(label_recorded_at),
        "case_content_sha256": case_content_sha256(case),
        "first_run_at": (
            None if case.first_run_at is None else encode_timestamp(case.first_run_at)
        ),
    }
    if tuple(record) != TIER_C_MANIFEST_FIELDS:
        raise TierCCaseError("manifest record fields drifted from the schema")
    return record


def _scalar(value: str | None) -> str:
    if value is None:
        return "null"
    if not _SAFE_SCALAR_RE.fullmatch(value):
        raise TierCCaseError(
            f"manifest scalar {value!r} contains characters this writer refuses "
            "to quote"
        )
    return f'"{value}"'


def render_cases_block(records: list[dict[str, str | None]]) -> str:
    """Render Tier C records in the registered manifest layout.

    The output matches the byte layout the frozen Tier A/B writer produces, so
    appended Tier C records are indistinguishable in style from the existing
    1,008. This returns text; it writes nothing.
    """

    if not records:
        raise TierCCaseError("refusing to render an empty Tier C cases block")
    lines: list[str] = []
    for record in records:
        if tuple(record) != TIER_C_MANIFEST_FIELDS:
            raise TierCCaseError("manifest record fields drifted from the schema")
        first = True
        for field_name in TIER_C_MANIFEST_FIELDS:
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{field_name}: {_scalar(record[field_name])}")
            first = False
    return "\n".join(lines) + "\n"
