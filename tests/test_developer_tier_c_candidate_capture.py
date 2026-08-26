"""D8-B1A developer-authored Tier C candidate-capture tests.

All populated text used below is synthetic placeholder input supplied by the
test itself.  The committed worksheet contains no semantic scenario text, and
these tests call neither an authoring model nor an authorization path.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pytest

from mandateguard.benchmark.tier_c.developer_capture import (
    DEVELOPER_FAMILY_COUNTS,
    DEVELOPER_INTENT_QUOTAS,
    WORKSHEET_FIELDS,
    AuthoringIntent,
    AuthoringMode,
    DeveloperCaptureError,
    candidate_record,
    candidate_record_line,
    capture_candidates,
    expected_case_ids,
    load_worksheet,
    parse_worksheet_text,
    validate_clean_envelope,
    validate_worksheet,
)
from mandateguard.benchmark.tier_c.models import (
    Provenance,
    Split,
    structural_issues,
)
from mandateguard.core.hashing import (
    catalog_snapshot_sha256,
    transaction_body_sha256,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKSHEET_PATH = (
    REPOSITORY_ROOT
    / "benchmark"
    / "tier_c"
    / "authoring"
    / "dev"
    / "developer_candidates.tsv"
)
MANIFEST_PATH = REPOSITORY_ROOT / "benchmark" / "MANIFEST.yaml"
CORPUS_ROOT = REPOSITORY_ROOT / "benchmark" / "cases" / "tier_c"
CAPTURE_MODULE = (
    REPOSITORY_ROOT
    / "src"
    / "mandateguard"
    / "benchmark"
    / "tier_c"
    / "developer_capture.py"
)
CAPTURE_SCRIPT = REPOSITORY_ROOT / "scripts" / "capture_developer_tier_c_candidates.py"


def _completed(row, marker: str = "one"):
    return replace(
        row,
        semantic_constraint_text=f"synthetic constraint placeholder {marker}",
        semantic_evidence_text=f"synthetic evidence placeholder {marker}",
    )


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def test_blank_worksheet_has_exactly_88_neutral_ids_and_family_counts():
    rows = load_worksheet(WORKSHEET_PATH)

    assert len(rows) == 88
    assert tuple(row.case_id for row in rows) == expected_case_ids()
    assert len(set(expected_case_ids())) == 88
    assert Counter(row.family_id for row in rows) == Counter(DEVELOPER_FAMILY_COUNTS)
    assert all(re.fullmatch(r"CDEV-(REC|EXC|PUR)-\d{3}", row.case_id) for row in rows)
    assert all("violation" not in row.case_id.lower() for row in rows)
    assert all("benign" not in row.case_id.lower() for row in rows)


def test_committed_worksheet_semantic_fields_and_notes_are_all_blank():
    rows = load_worksheet(WORKSHEET_PATH)

    assert all(row.is_blank for row in rows)
    assert all(row.semantic_constraint_text == "" for row in rows)
    assert all(row.semantic_evidence_text == "" for row in rows)
    assert all(row.optional_author_note == "" for row in rows)
    assert capture_candidates(rows, AuthoringMode.PARTIAL) == ()


def test_authoring_intent_allocation_matches_frozen_quotas():
    rows = load_worksheet(WORKSHEET_PATH)
    counts = Counter((row.family_id, row.authoring_intent.value) for row in rows)

    assert counts == Counter(DEVELOPER_INTENT_QUOTAS)
    assert sum(counts.values()) == 88
    assert sum(
        count for (_, intent), count in counts.items() if intent == "violation_intended"
    ) == 48
    assert sum(
        count for (_, intent), count in counts.items() if intent == "benign_intended"
    ) == 40
    assert validate_worksheet(rows, AuthoringMode.PARTIAL) == rows


def test_partial_mode_allows_missing_rows_and_fully_blank_semantic_pairs():
    rows = load_worksheet(WORKSHEET_PATH)

    assert validate_worksheet(rows[:3], AuthoringMode.PARTIAL) == rows[:3]
    assert capture_candidates(rows[:3], AuthoringMode.PARTIAL) == ()


def test_final_mode_requires_all_88_populated_semantic_pairs():
    rows = load_worksheet(WORKSHEET_PATH)
    with pytest.raises(DeveloperCaptureError, match="blank semantic fields"):
        capture_candidates(rows, AuthoringMode.FINAL_CANDIDATES)

    completed = tuple(_completed(row, str(index)) for index, row in enumerate(rows))
    candidates = capture_candidates(completed, AuthoringMode.FINAL_CANDIDATES)
    assert len(candidates) == 88


def test_authoring_intent_quota_cannot_be_exceeded():
    rows = list(load_worksheet(WORKSHEET_PATH))
    benign_index = next(
        index
        for index, row in enumerate(rows)
        if row.family_id == "C-DEV-RECURRENCE"
        and row.authoring_intent is AuthoringIntent.BENIGN_INTENDED
    )
    rows[benign_index] = replace(
        rows[benign_index], authoring_intent=AuthoringIntent.VIOLATION_INTENDED
    )

    with pytest.raises(DeveloperCaptureError, match="quota exceeded"):
        validate_worksheet(rows, AuthoringMode.PARTIAL)


@pytest.mark.parametrize(
    ("constraint", "evidence", "missing"),
    [
        ("synthetic constraint placeholder", "", "semantic_evidence_text"),
        ("", "synthetic evidence placeholder", "semantic_constraint_text"),
    ],
)
def test_missing_semantic_input_is_rejected_and_never_auto_filled(
    constraint: str, evidence: str, missing: str
):
    row = replace(
        load_worksheet(WORKSHEET_PATH)[0],
        semantic_constraint_text=constraint,
        semantic_evidence_text=evidence,
    )

    with pytest.raises(DeveloperCaptureError, match=missing):
        capture_candidates((row,), AuthoringMode.PARTIAL)


def test_capture_preserves_both_human_semantic_fields_verbatim():
    row = replace(
        load_worksheet(WORKSHEET_PATH)[0],
        semantic_constraint_text="Synthetic.Constraint: Alpha/Beta?",
        semantic_evidence_text="Synthetic Evidence — Alpha != Beta.",
    )
    candidate = capture_candidates((row,), AuthoringMode.PARTIAL)[0]
    case = candidate.tier_c_case

    assert (
        case.evaluation_inputs.mandate.payload.constraints.semantic[0].text
        == row.semantic_constraint_text
    )
    assert (
        case.evaluation_inputs.semantic_evidence.entries[0].text
        == row.semantic_evidence_text
    )


def test_capture_rejects_instead_of_trimming_semantic_whitespace():
    row = replace(
        load_worksheet(WORKSHEET_PATH)[0],
        semantic_constraint_text=" synthetic constraint placeholder ",
        semantic_evidence_text="synthetic evidence placeholder",
    )

    with pytest.raises(DeveloperCaptureError, match="does not transform"):
        capture_candidates((row,), AuthoringMode.PARTIAL)


def test_provenance_is_forced_and_authored_at_is_capture_time():
    before = datetime.now(timezone.utc)
    candidate = capture_candidates(
        (_completed(load_worksheet(WORKSHEET_PATH)[0]),), AuthoringMode.PARTIAL
    )[0]
    after = datetime.now(timezone.utc)
    case = candidate.tier_c_case

    assert case.provenance is Provenance.DEVELOPER_AUTHORED
    assert before <= case.authored_at <= after
    assert case.split is Split.DEV


def test_worksheet_schema_does_not_accept_provenance_override():
    header = "\t".join((*WORKSHEET_FIELDS, "provenance"))
    values = "\t".join(
        (
            "CDEV-REC-001",
            "C-DEV-RECURRENCE",
            "violation_intended",
            "",
            "",
            "",
            "separate_model_adversarial",
        )
    )

    with pytest.raises(DeveloperCaptureError, match="header must be exactly"):
        parse_worksheet_text(f"{header}\n{values}\n")


@pytest.mark.parametrize(
    "intent",
    [AuthoringIntent.VIOLATION_INTENDED, AuthoringIntent.BENIGN_INTENDED],
)
def test_authoring_intent_never_populates_ground_truth(intent: AuthoringIntent):
    row = replace(
        _completed(load_worksheet(WORKSHEET_PATH)[0]), authoring_intent=intent
    )
    candidate = capture_candidates((row,), AuthoringMode.PARTIAL)[0]
    case = candidate.tier_c_case

    assert case.ground_truth is None
    assert case.adjudication.primary is None
    assert case.adjudication.second is None
    assert case.adjudication.resolution is None


def test_fixed_envelope_is_structurally_clean_without_authorization_execution():
    candidate = capture_candidates(
        (_completed(load_worksheet(WORKSHEET_PATH)[0]),), AuthoringMode.PARTIAL
    )[0]
    case = candidate.tier_c_case
    inputs = case.evaluation_inputs
    transaction = inputs.transaction
    catalog = inputs.catalog_snapshot
    commitments = inputs.psp_committed_hashes

    validate_clean_envelope(candidate)
    assert structural_issues(case) == []
    assert catalog is not None
    assert commitments is not None
    assert transaction.declared_transaction_hash == transaction_body_sha256(transaction)
    assert commitments.transaction_sha256 == transaction_body_sha256(transaction)
    assert commitments.catalog_snapshot_sha256 == catalog_snapshot_sha256(catalog)


def test_candidate_serialization_is_unadjudicated_and_has_no_final_digest():
    row = replace(
        _completed(load_worksheet(WORKSHEET_PATH)[0]),
        optional_author_note="synthetic author note",
    )
    candidate = capture_candidates((row,), AuthoringMode.PARTIAL)[0]
    record = candidate_record(candidate)
    decoded_line = json.loads(candidate_record_line(candidate))

    assert record == decoded_line
    assert record["authoring_intent"] == "violation_intended"
    assert record["optional_author_note"] == "synthetic author note"
    assert record["tier_c_case"]["provenance"] == "developer_authored"
    assert record["tier_c_case"]["adjudication"]["status"] == "UNADJUDICATED"
    assert "ground_truth" not in _all_keys(record)
    assert "case_content_sha256" not in _all_keys(record)


def test_capture_sources_import_no_model_or_authorization_module():
    forbidden_prefixes = (
        "mandateguard.policy",
        "mandateguard.semantic",
        "mandateguard.execution",
        "mandateguard.replay",
        "openai",
    )
    for path in (CAPTURE_MODULE, CAPTURE_SCRIPT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not any(
            name.startswith(forbidden_prefixes) for name in imported
        ), f"{path.name} imports a model or authorization module"


def test_manifest_and_finalized_tier_c_corpus_remain_unchanged():
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    finalized_files = tuple(CORPUS_ROOT.rglob("*.jsonl")) if CORPUS_ROOT.exists() else ()

    assert len(re.findall(r"^  - case_id:", manifest, flags=re.MULTILINE)) == 1008
    assert finalized_files == ()
