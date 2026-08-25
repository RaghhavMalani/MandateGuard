"""Regression tests for the hardened Tier C held-out audit guards.

Two hostile-review findings are pinned here:

1. **Provenance-origin audit timestamp immutability.** ``authored_at`` and
   ``source_selected_at`` are deliberately excluded from
   ``case_content_sha256`` - they are audit metadata, not benchmark content -
   but they are the mechanical evidence the held-out isolation guard reads.
   Once a case has executed they must not be rewritable, or a held-out result
   could be followed by backdating the isolation evidence.

2. **The held-out checkpoint as a complete standalone gate.** A 220-record set
   containing a duplicate ``case_id`` (or any other final-corpus defect) must
   not pass merely because the caller invoked the checkpoint directly.

Every fixture is synthetic and is not Tier C benchmark content. No test calls a
detector or a model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from mandateguard.benchmark.tier_c.codec import (
    case_content_projection,
    case_content_sha256,
    case_record_line,
    decode_case,
    encode_provenance_origin_audit,
    encode_provenance_origin_content,
    provenance_origin_audit_fields,
)
from mandateguard.benchmark.tier_c.models import (
    DEVELOPMENT_TOTAL,
    DEV_FAMILIES,
    HELD_OUT_FAMILIES,
    HELD_OUT_TOTAL,
    IMMUTABLE_AFTER_FIRST_RUN,
    ExternalCorpusOrigin,
    GroundTruth,
    Provenance,
    Split,
    TierCCase,
    TierCCaseError,
    allocation_for_split,
)
from mandateguard.benchmark.tier_c.validation import (
    HeldOutFinalizationCheckpoint,
    ValidationMode,
    assert_immutable_after_first_run,
    immutability_violations,
    validate_held_out_checkpoint,
    validate_tier_c_corpus,
)
from tests.test_benchmark_tier_c_infrastructure import (
    REPOSITORY_ROOT,
    TIER_C_PACKAGE,
)
from tests.tier_c_factories import (
    AUTHORED_AT,
    FREEZE_AT,
    make_adjudication,
    make_allocated_corpus,
    make_case,
    make_evaluation_inputs,
    make_origin,
)


RAN_AT = datetime(2026, 10, 1, tzinfo=timezone.utc)

ALL_PROVENANCES = (
    Provenance.DEVELOPER_AUTHORED,
    Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
    Provenance.SEPARATE_MODEL_ADVERSARIAL,
)


def _external_case(
    *, source_selected_at: datetime, source_name: str = "synthetic-source"
) -> TierCCase:
    return TierCCase(
        case_id="CHOLD-BUN-001",
        case_schema_version="1.2",
        evidence_tier="C",
        family_id="C-HOLD-BUNDLE",
        provenance=Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
        provenance_origin=ExternalCorpusOrigin(
            authored_at=FREEZE_AT + timedelta(days=2),
            source_selected_at=source_selected_at,
            source_name=source_name,
            source_reference="https://example.invalid/synthetic",
            source_version=None,
            adaptation_description="synthetic adaptation",
        ),
        split=Split.HELD_OUT,
        label_source="human_adjudication",
        evaluation_inputs=make_evaluation_inputs("C-HOLD-BUNDLE", "x"),
        adjudication=make_adjudication(),
        first_run_at=RAN_AT,
    )


# ---------------------------------------------------------------------------
# Finding 1: provenance-origin audit timestamp immutability
# ---------------------------------------------------------------------------


def test_authored_at_change_after_first_run_is_rejected():
    before = make_case(family_id="C-HOLD-BUNDLE", first_run_at=RAN_AT)
    after = make_case(
        family_id="C-HOLD-BUNDLE",
        authored_at=AUTHORED_AT + timedelta(days=400),
        first_run_at=RAN_AT,
    )
    assert case_content_sha256(before) == case_content_sha256(after)
    assert "provenance_origin_audit" in immutability_violations(before, after)
    with pytest.raises(TierCCaseError, match="provenance_origin_audit"):
        assert_immutable_after_first_run(before, after)


def test_source_selected_at_change_after_first_run_is_rejected():
    before = _external_case(source_selected_at=FREEZE_AT + timedelta(days=1))
    # Backdating source selection would retro-fit the isolation guard while
    # leaving the content digest untouched.
    after = _external_case(source_selected_at=FREEZE_AT - timedelta(days=1))
    assert case_content_sha256(before) == case_content_sha256(after)
    assert immutability_violations(before, after) == ("provenance_origin_audit",)
    with pytest.raises(TierCCaseError, match="provenance_origin_audit"):
        assert_immutable_after_first_run(before, after)


@pytest.mark.parametrize("provenance", ALL_PROVENANCES)
def test_identical_origin_timestamps_are_accepted_after_first_run(provenance):
    before = make_case(provenance=provenance, first_run_at=RAN_AT)
    after = make_case(provenance=provenance, first_run_at=RAN_AT)
    assert immutability_violations(before, after) == ()
    assert_immutable_after_first_run(before, after)


def test_origin_timestamp_change_before_first_run_is_permitted():
    """Pre-execution correction stays an ordinary authoring act."""

    before = make_case(family_id="C-HOLD-BUNDLE")
    after = make_case(
        family_id="C-HOLD-BUNDLE", authored_at=AUTHORED_AT + timedelta(days=400)
    )
    assert before.first_run_at is None
    assert immutability_violations(before, after) == ()
    assert_immutable_after_first_run(before, after)


def test_lifecycle_validation_unchanged_before_first_execution():
    """The audit-immutability addition must not disturb normal validation."""

    report = validate_tier_c_corpus(
        make_allocated_corpus(DEV_FAMILIES), ValidationMode.FINAL_DEVELOPMENT
    )
    assert report.ok, report.render()
    assert validate_tier_c_corpus([], ValidationMode.PARTIAL_DEVELOPMENT).ok


def test_hashed_origin_field_change_is_still_caught_by_content_immutability():
    """A hashed origin field routes through content immutability, not audit."""

    before = _external_case(source_selected_at=FREEZE_AT + timedelta(days=1))
    after = _external_case(
        source_selected_at=FREEZE_AT + timedelta(days=1),
        source_name="other-synthetic-source",
    )
    changed = immutability_violations(before, after)
    assert "case_content_sha256" in changed
    assert "provenance_origin_audit" not in changed


def test_origin_audit_timestamps_remain_outside_the_content_hash():
    """The fix must not smuggle audit timestamps into the digest."""

    baseline = case_content_sha256(make_case(family_id="C-HOLD-BUNDLE"))
    moved = case_content_sha256(
        make_case(
            family_id="C-HOLD-BUNDLE", authored_at=AUTHORED_AT + timedelta(days=9)
        )
    )
    assert baseline == moved
    projection = case_content_projection(
        make_case(provenance=Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED)
    )
    assert "authored_at" not in projection["provenance_origin"]
    assert "source_selected_at" not in projection["provenance_origin"]


@pytest.mark.parametrize(
    ("provenance", "expected_audit"),
    [
        (Provenance.DEVELOPER_AUTHORED, ("authored_at",)),
        (
            Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
            ("authored_at", "source_selected_at"),
        ),
        (Provenance.SEPARATE_MODEL_ADVERSARIAL, ("authored_at",)),
    ],
)
def test_origin_audit_projection_covers_the_expected_timestamps(
    provenance, expected_audit
):
    origin = make_origin(provenance)
    assert provenance_origin_audit_fields(origin) == expected_audit
    assert set(encode_provenance_origin_audit(origin)) == set(expected_audit)


@pytest.mark.parametrize("provenance", ALL_PROVENANCES)
def test_every_origin_field_is_hashed_or_audit_protected(provenance):
    """No origin field can escape both projections, now or in future."""

    import dataclasses

    origin = make_origin(provenance)
    declared = {field.name for field in dataclasses.fields(origin)}
    content = set(encode_provenance_origin_content(origin))
    audit = set(provenance_origin_audit_fields(origin))
    assert content | audit == declared
    assert content & audit == set()


def test_immutable_field_list_includes_the_audit_projection():
    assert set(IMMUTABLE_AFTER_FIRST_RUN) == {
        "ground_truth",
        "family_id",
        "split",
        "provenance",
        "evaluation_inputs",
        "semantic_evidence",
        "case_content_sha256",
        "provenance_origin_audit",
    }


@pytest.mark.parametrize("provenance", ALL_PROVENANCES)
def test_full_origin_record_round_trips(provenance):
    case = make_case(provenance=provenance, family_id="C-HOLD-BUNDLE")
    decoded = decode_case(json.loads(case_record_line(case)))
    assert decoded.provenance_origin == case.provenance_origin


# ---------------------------------------------------------------------------
# Finding 2: the held-out checkpoint as a complete standalone gate
# ---------------------------------------------------------------------------


def _checkpoint(**overrides) -> HeldOutFinalizationCheckpoint:
    values = {
        "detector_freeze_commit_sha": "0" * 40,
        "protocol_commit_sha": "1" * 40,
        "detector_version": "synthetic-detector",
        "prompt_version": "synthetic-prompt",
        "model_id": "synthetic-model",
        "total_held_out_cases": 220,
        "ground_truth_recorded_count": 220,
        "content_hash_recorded_count": 220,
        "first_run_null_count": 220,
        "finalized_at": FREEZE_AT + timedelta(days=10),
    }
    values.update(overrides)
    return HeldOutFinalizationCheckpoint(**values)


def _held_out_220() -> list[TierCCase]:
    return make_allocated_corpus(
        HELD_OUT_FAMILIES, authored_at=FREEZE_AT + timedelta(days=1)
    )


def _gate(cases) -> set[str]:
    return {
        issue.code
        for issue in validate_held_out_checkpoint(
            cases, _checkpoint(), detector_freeze_at=FREEZE_AT
        )
    }


def _index_of(case: TierCCase) -> int:
    return int(case.case_id.split("-")[-1])


def _held_out_case(
    family_id: str,
    index: int,
    ground_truth: GroundTruth,
    provenance: Provenance,
    *,
    marker: str | None = None,
    first_run_at: datetime | None = None,
    adjudication=None,
) -> TierCCase:
    return make_case(
        family_id=family_id,
        index=index,
        provenance=provenance,
        marker=marker,
        authored_at=FREEZE_AT + timedelta(days=1),
        first_run_at=first_run_at,
        adjudication=(
            adjudication
            if adjudication is not None
            else make_adjudication(ground_truth, second_label=ground_truth)
        ),
    )


def test_valid_synthetic_220_held_out_corpus_is_accepted():
    cases = _held_out_220()
    assert len(cases) == HELD_OUT_TOTAL
    assert _gate(cases) == set()


def test_gate_rejects_220_with_a_duplicate_case_id():
    """The exact hostile-review scenario: 220 records, one case_id reused."""

    cases = _held_out_220()
    first, second = cases[0], cases[1]
    assert first.family_id == second.family_id
    assert first.provenance is second.provenance
    # Reuse cases[0]'s ID while keeping distinct content, so only the ID
    # collides and nothing else about the record looks wrong.
    cases[1] = _held_out_case(
        second.family_id,
        _index_of(first),
        GroundTruth.VIOLATION,
        second.provenance,
        marker="distinct-content-for-duplicate-id",
    )
    assert cases[0].case_id == cases[1].case_id
    assert len(cases) == HELD_OUT_TOTAL
    codes = _gate(cases)
    assert "DUPLICATE_CASE_ID" in codes
    assert "CHECKPOINT_COUNT_MISMATCH" in codes


def test_gate_rejects_220_with_a_duplicate_content_hash():
    cases = _held_out_220()
    first, second = cases[0], cases[1]
    cases[1] = _held_out_case(
        second.family_id,
        _index_of(second),
        GroundTruth.VIOLATION,
        second.provenance,
        marker=f"{first.family_id}-{_index_of(first)}",
    )
    assert cases[0].case_id != cases[1].case_id
    assert case_content_sha256(cases[0]) == case_content_sha256(cases[1])
    assert len(cases) == HELD_OUT_TOTAL
    codes = _gate(cases)
    assert "DUPLICATE_CONTENT_HASH" in codes
    assert "EXACT_DUPLICATE" in codes


def test_gate_rejects_220_with_a_wrong_family_quota():
    cases = _held_out_220()
    bundle = next(case for case in cases if case.family_id == "C-HOLD-BUNDLE")
    cases.remove(bundle)
    cases.append(
        _held_out_case(
            "C-HOLD-COMPATIBILITY",
            900,
            GroundTruth.VIOLATION,
            Provenance.DEVELOPER_AUTHORED,
            marker="extra-compatibility-case",
        )
    )
    assert len(cases) == HELD_OUT_TOTAL
    codes = _gate(cases)
    assert {"PROVENANCE_STRATA_MISMATCH", "QUOTA_EXCESS"} & codes


def test_gate_rejects_220_with_a_wrong_provenance_quota():
    cases = _held_out_220()
    external = next(
        case
        for case in cases
        if case.provenance is Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED
        and case.family_id == "C-HOLD-BUNDLE"
        and case.ground_truth is GroundTruth.VIOLATION
    )
    position = cases.index(external)
    cases[position] = _held_out_case(
        external.family_id,
        _index_of(external),
        GroundTruth.VIOLATION,
        Provenance.DEVELOPER_AUTHORED,
        marker="provenance-swapped-case",
    )
    assert len(cases) == HELD_OUT_TOTAL
    codes = _gate(cases)
    assert {"PROVENANCE_STRATA_MISMATCH", "QUOTA_EXCESS"} & codes


def test_gate_rejects_219_cases():
    cases = _held_out_220()[:-1]
    assert len(cases) == HELD_OUT_TOTAL - 1
    codes = _gate(cases)
    assert "CHECKPOINT_COUNT_MISMATCH" in codes
    assert "QUOTA_INCOMPLETE" in codes


def test_gate_rejects_221_cases():
    cases = _held_out_220()
    cases.append(
        _held_out_case(
            "C-HOLD-BUNDLE",
            901,
            GroundTruth.VIOLATION,
            Provenance.DEVELOPER_AUTHORED,
            marker="one-case-too-many",
        )
    )
    assert len(cases) == HELD_OUT_TOTAL + 1
    codes = _gate(cases)
    assert "CHECKPOINT_COUNT_MISMATCH" in codes
    assert "QUOTA_EXCESS" in codes


def test_gate_rejects_an_unresolved_disagreement():
    cases = _held_out_220()
    target = cases[0]
    cases[0] = _held_out_case(
        target.family_id,
        _index_of(target),
        GroundTruth.VIOLATION,
        target.provenance,
        adjudication=make_adjudication(
            GroundTruth.VIOLATION, second_label=GroundTruth.BENIGN
        ),
    )
    assert cases[0].ground_truth is None
    codes = _gate(cases)
    assert "UNRESOLVED_DISAGREEMENT" in codes
    assert "CHECKPOINT_COUNT_MISMATCH" in codes


def test_gate_rejects_a_non_null_first_run_at():
    cases = _held_out_220()
    target = cases[0]
    cases[0] = _held_out_case(
        target.family_id,
        _index_of(target),
        GroundTruth.VIOLATION,
        target.provenance,
        first_run_at=FREEZE_AT + timedelta(days=20),
    )
    codes = _gate(cases)
    assert "HELD_OUT_ALREADY_EXECUTED" in codes
    assert "UNEXPECTED_FIRST_RUN" in codes


def test_gate_rejects_a_wrong_split_case():
    cases = _held_out_220()[:-1]
    cases.append(make_case(marker="development-case-in-held-out-batch"))
    codes = _gate(cases)
    assert "NON_HELD_OUT_CASE" in codes
    assert "WRONG_SPLIT_FOR_MODE" in codes


def test_gate_rejects_a_missing_second_review():
    cases = make_allocated_corpus(
        HELD_OUT_FAMILIES,
        authored_at=FREEZE_AT + timedelta(days=1),
        second_review_everything=False,
    )
    assert "SECOND_REVIEW_INCOMPLETE" in _gate(cases)


def test_gate_rejects_held_out_content_authored_before_freeze():
    cases = make_allocated_corpus(
        HELD_OUT_FAMILIES, authored_at=FREEZE_AT - timedelta(days=1)
    )
    assert "HELD_OUT_AUTHORED_BEFORE_FREEZE" in _gate(cases)


def test_gate_requires_detector_freeze_evidence():
    codes = {
        issue.code
        for issue in validate_held_out_checkpoint(_held_out_220(), _checkpoint())
    }
    assert "MISSING_DETECTOR_FREEZE" in codes


def test_gate_is_not_recursive():
    """The corpus validator must never call back into checkpoint logic."""

    import ast

    tree = ast.parse((TIER_C_PACKAGE / "validation.py").read_text(encoding="utf-8"))
    corpus_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "validate_tier_c_corpus"
    )
    called = {
        node.func.id
        for node in ast.walk(corpus_fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "validate_held_out_checkpoint" not in called


# ---------------------------------------------------------------------------
# Quota pin: development provenance totals are 88 / 66 / 66
# ---------------------------------------------------------------------------


def _provenance_total(split: Split, ground_truth, provenance) -> int:
    return sum(
        count
        for key, count in allocation_for_split(split).items()
        if (ground_truth is None or key[1] is ground_truth) and key[2] is provenance
    )


def test_development_provenance_totals_are_88_66_66():
    developer = Provenance.DEVELOPER_AUTHORED
    external = Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED
    model = Provenance.SEPARATE_MODEL_ADVERSARIAL

    assert _provenance_total(Split.DEV, GroundTruth.VIOLATION, developer) == 48
    assert _provenance_total(Split.DEV, GroundTruth.VIOLATION, external) == 36
    assert _provenance_total(Split.DEV, GroundTruth.VIOLATION, model) == 36
    assert _provenance_total(Split.DEV, GroundTruth.BENIGN, developer) == 40
    assert _provenance_total(Split.DEV, GroundTruth.BENIGN, external) == 30
    assert _provenance_total(Split.DEV, GroundTruth.BENIGN, model) == 30

    assert _provenance_total(Split.DEV, None, developer) == 88
    assert _provenance_total(Split.DEV, None, external) == 66
    assert _provenance_total(Split.DEV, None, model) == 66
    assert sum(allocation_for_split(Split.DEV).values()) == DEVELOPMENT_TOTAL == 220


def test_held_out_provenance_totals_match_development():
    for provenance, expected in (
        (Provenance.DEVELOPER_AUTHORED, 88),
        (Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED, 66),
        (Provenance.SEPARATE_MODEL_ADVERSARIAL, 66),
    ):
        assert _provenance_total(Split.HELD_OUT, None, provenance) == expected
    assert sum(allocation_for_split(Split.HELD_OUT).values()) == HELD_OUT_TOTAL == 220


def test_readme_states_the_correct_development_provenance_totals():
    readme = (REPOSITORY_ROOT / "benchmark" / "tier_c" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "88 / 66 / 66" in readme
    assert "80 / 60 / 60" not in readme
    assert "176 / 132 / 132" in readme
