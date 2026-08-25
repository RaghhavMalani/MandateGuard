"""D8-A Tier C infrastructure tests.

Every fixture here is synthetic and explicitly not benchmark content. No test
calls a detector, a semantic verifier, or a model provider, and no test creates
Tier C benchmark case content in the repository.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from mandateguard.benchmark.tier_c.codec import (
    AUDIT_ONLY_FIELDS,
    CONTENT_HASH_FIELDS,
    case_content_projection,
    case_content_sha256,
    case_record_line,
    decode_case,
    encode_case,
)
from mandateguard.benchmark.tier_c.corpus import (
    FAMILY_FILES,
    TierCCorpus,
    import_case,
    load_corpus,
)
from mandateguard.benchmark.tier_c.dedup import (
    normalize_text,
    normalized_fingerprint,
    review_duplicates,
)
from mandateguard.benchmark.tier_c.manifest import (
    TIER_C_MANIFEST_FIELDS,
    manifest_record,
    render_cases_block,
)
from mandateguard.benchmark.tier_c.models import (
    DEVELOPMENT_TOTAL,
    DEV_FAMILIES,
    FAMILY_SPLIT,
    HELD_OUT_FAMILIES,
    HELD_OUT_TOTAL,
    TIER_C_ALLOCATION,
    TIER_C_FAMILIES,
    TIER_C_TOTAL,
    AdjudicationRecord,
    AdjudicationStatus,
    DeveloperAuthoredOrigin,
    ExclusionRecord,
    ExternalCorpusOrigin,
    GroundTruth,
    Provenance,
    SemanticEvidenceBundleRecord,
    SemanticEvidenceEntryRecord,
    SeparateModelOrigin,
    Split,
    TierCAdjudication,
    TierCCase,
    TierCCaseError,
    TierCEvaluationInputs,
    allocation_for_split,
)
from mandateguard.benchmark.tier_c.second_review import (
    SECOND_REVIEW_DOMAIN,
    SecondReviewCandidate,
    candidate_from_case,
    required_second_review_count,
    second_review_rank,
    select_second_review,
)
from mandateguard.benchmark.tier_c.validation import (
    HeldOutFinalizationCheckpoint,
    ValidationMode,
    assert_immutable_after_first_run,
    immutability_violations,
    validate_held_out_checkpoint,
    validate_held_out_isolation,
    validate_tier_c_corpus,
)
from mandateguard.core.canonical import FloatNotAllowedError, canonical_json_bytes
from mandateguard.models.mandate import SemanticConstraint
from tests.factories import SERVER_TIME
from tests.tier_c_factories import (
    ADJUDICATED_AT,
    AUTHORED_AT,
    FREEZE_AT,
    SYNTHETIC_PROMPT_SHA256,
    make_adjudication,
    make_allocated_corpus,
    make_case,
    make_evaluation_inputs,
    make_origin,
    make_semantic_evidence,
    make_semantic_mandate,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "benchmark" / "MANIFEST.yaml"
TIER_C_PACKAGE = REPOSITORY_ROOT / "src" / "mandateguard" / "benchmark" / "tier_c"

FORBIDDEN_MODULE_PREFIXES = (
    "mandateguard.policy",
    "mandateguard.semantic",
    "mandateguard.execution",
    "mandateguard.replay",
)


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


# ---------------------------------------------------------------------------
# 1-7: families, splits, tiers, semantic constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family_id", TIER_C_FAMILIES)
def test_six_tier_c_families_are_accepted(family_id):
    case = make_case(family_id=family_id)
    assert case.family_id == family_id
    assert case.split is FAMILY_SPLIT[family_id]


def test_registered_families_are_exactly_the_frozen_six():
    assert DEV_FAMILIES == ("C-DEV-RECURRENCE", "C-DEV-EXCLUSION", "C-DEV-PURPOSE")
    assert HELD_OUT_FAMILIES == (
        "C-HOLD-BUNDLE",
        "C-HOLD-COMPATIBILITY",
        "C-HOLD-FULFILLMENT",
    )
    assert len(TIER_C_FAMILIES) == 6


@pytest.mark.parametrize(
    "family_id", ["C-DEV-INVENTED", "C-HOLD-EXTRA", "A1", "B3", "C-DEV-RECURRENCE-2", ""]
)
def test_unknown_family_is_rejected(family_id):
    with pytest.raises(TierCCaseError, match="not a Tier C family"):
        TierCCase(
            case_id="CDEV-EXC-001",
            case_schema_version="1.2",
            evidence_tier="C",
            family_id=family_id,
            provenance=Provenance.DEVELOPER_AUTHORED,
            provenance_origin=make_origin(Provenance.DEVELOPER_AUTHORED),
            split=Split.DEV,
            label_source="human_adjudication",
            evaluation_inputs=make_evaluation_inputs("C-DEV-EXCLUSION", "x"),
            adjudication=make_adjudication(),
        )


def test_dev_family_requires_dev_split():
    with pytest.raises(TierCCaseError, match="requires split=dev"):
        make_case(family_id="C-DEV-EXCLUSION", split=Split.HELD_OUT)


def test_held_out_family_requires_held_out_split():
    with pytest.raises(TierCCaseError, match="requires split=held_out"):
        make_case(family_id="C-HOLD-BUNDLE", split=Split.DEV)


def test_ground_truth_does_not_affect_split():
    for ground_truth in (GroundTruth.VIOLATION, GroundTruth.BENIGN):
        dev_case = make_case(family_id="C-DEV-PURPOSE", ground_truth=ground_truth)
        held_case = make_case(family_id="C-HOLD-BUNDLE", ground_truth=ground_truth)
        assert dev_case.split is Split.DEV
        assert held_case.split is Split.HELD_OUT


@pytest.mark.parametrize("family_id", TIER_C_FAMILIES)
def test_benign_control_split_is_rejected_for_tier_c(family_id):
    with pytest.raises(TierCCaseError, match="benign_control is not used for Tier C"):
        make_case(family_id=family_id, split=Split.BENIGN_CONTROL)


def test_benign_control_remains_a_manifest_enum_value():
    assert Split.BENIGN_CONTROL.value == "benign_control"
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "benign_control" in text


def test_evidence_tier_must_be_c():
    with pytest.raises(TierCCaseError, match="evidence_tier must be C"):
        TierCCase(
            case_id="CDEV-EXC-001",
            case_schema_version="1.2",
            evidence_tier="B",
            family_id="C-DEV-EXCLUSION",
            provenance=Provenance.DEVELOPER_AUTHORED,
            provenance_origin=make_origin(Provenance.DEVELOPER_AUTHORED),
            split=Split.DEV,
            label_source="human_adjudication",
            evaluation_inputs=make_evaluation_inputs("C-DEV-EXCLUSION", "x"),
            adjudication=make_adjudication(),
        )


def test_semantic_constraint_is_required():
    mandate = make_semantic_mandate("C-DEV-EXCLUSION", "x")
    constraints = replace(mandate.payload.constraints, semantic=())
    empty = replace(mandate, payload=replace(mandate.payload, constraints=constraints))
    with pytest.raises(TierCCaseError, match="at least one semantic constraint"):
        make_evaluation_inputs("C-DEV-EXCLUSION", "x", mandate=empty)


def test_family_constraint_kind_must_match_the_family():
    mandate = make_semantic_mandate("C-DEV-EXCLUSION", "x")
    mismatched = replace(
        mandate,
        payload=replace(
            mandate.payload,
            constraints=replace(
                mandate.payload.constraints,
                semantic=(
                    SemanticConstraint(
                        constraint_id="synthetic-1",
                        kind="compatibility",
                        text="synthetic placeholder constraint text",
                    ),
                ),
            ),
        ),
    )
    inputs = make_evaluation_inputs("C-DEV-EXCLUSION", "x", mandate=mismatched)
    with pytest.raises(TierCCaseError, match="needs a constraint of kind"):
        make_case(family_id="C-DEV-EXCLUSION", evaluation_inputs=inputs)


def test_label_source_must_be_human_adjudication():
    case = make_case()
    assert case.label_source == "human_adjudication"


# ---------------------------------------------------------------------------
# 8-9: provenance metadata and origin immutability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provenance",
    [
        Provenance.DEVELOPER_AUTHORED,
        Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
        Provenance.SEPARATE_MODEL_ADVERSARIAL,
    ],
)
def test_each_provenance_accepts_its_own_metadata(provenance):
    case = make_case(provenance=provenance)
    assert case.provenance is provenance


@pytest.mark.parametrize(
    ("provenance", "wrong"),
    [
        (Provenance.DEVELOPER_AUTHORED, Provenance.SEPARATE_MODEL_ADVERSARIAL),
        (Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED, Provenance.DEVELOPER_AUTHORED),
        (
            Provenance.SEPARATE_MODEL_ADVERSARIAL,
            Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
        ),
    ],
)
def test_mismatched_provenance_metadata_is_rejected(provenance, wrong):
    with pytest.raises(TierCCaseError, match="requires"):
        TierCCase(
            case_id="CDEV-EXC-001",
            case_schema_version="1.2",
            evidence_tier="C",
            family_id="C-DEV-EXCLUSION",
            provenance=provenance,
            provenance_origin=make_origin(wrong),
            split=Split.DEV,
            label_source="human_adjudication",
            evaluation_inputs=make_evaluation_inputs("C-DEV-EXCLUSION", "x"),
            adjudication=make_adjudication(),
        )


def test_external_origin_requires_full_source_metadata():
    with pytest.raises(TierCCaseError, match="source_name"):
        ExternalCorpusOrigin(
            authored_at=AUTHORED_AT,
            source_selected_at=AUTHORED_AT,
            source_name="",
            source_reference="https://example.invalid/x",
            source_version=None,
            adaptation_description="synthetic",
        )


def test_separate_model_origin_requires_model_id_and_prompt_digest():
    with pytest.raises(TierCCaseError, match="authoring_prompt_sha256"):
        SeparateModelOrigin(
            authored_at=AUTHORED_AT,
            authoring_model_id="synthetic-authoring-model",
            authoring_prompt_sha256="not-a-digest",
        )


def test_separate_model_origin_cannot_store_a_raw_prompt_or_credentials():
    """Only the prompt digest is retained; there is nowhere to put the rest."""

    fields = set(SeparateModelOrigin.__dataclass_fields__)
    assert fields == {"authored_at", "authoring_model_id", "authoring_prompt_sha256"}
    with pytest.raises(TypeError):
        SeparateModelOrigin(
            authored_at=AUTHORED_AT,
            authoring_model_id="synthetic-authoring-model",
            authoring_prompt_sha256=SYNTHETIC_PROMPT_SHA256,
            authoring_prompt="the raw prompt text",
        )


def test_provenance_origin_is_immutable_and_typed_per_provenance():
    case = make_case(provenance=Provenance.SEPARATE_MODEL_ADVERSARIAL)
    assert isinstance(case.provenance_origin, SeparateModelOrigin)
    with pytest.raises(Exception):
        case.provenance_origin.authoring_model_id = "relabelled"
    with pytest.raises(Exception):
        case.provenance = Provenance.DEVELOPER_AUTHORED


def test_relabelling_provenance_produces_a_different_content_digest():
    """Protocol 3.1.1: provenance laundering must change the hash, not hide."""

    model_case = make_case(provenance=Provenance.SEPARATE_MODEL_ADVERSARIAL)
    developer_case = make_case(provenance=Provenance.DEVELOPER_AUTHORED)
    assert case_content_sha256(model_case) != case_content_sha256(developer_case)


# ---------------------------------------------------------------------------
# 10-13: hashing
# ---------------------------------------------------------------------------


def test_content_projection_holds_exactly_the_registered_fields():
    case = make_case()
    projection = case_content_projection(case)
    assert tuple(sorted(projection)) == tuple(sorted(CONTENT_HASH_FIELDS))
    for audit_field in AUDIT_ONLY_FIELDS:
        assert audit_field not in projection


def test_manifest_required_hash_fields_are_all_covered():
    """The frozen manifest field_rules enumeration is a floor, not a ceiling."""

    required = {
        "evaluation_inputs",
        "family_id",
        "evidence_tier",
        "provenance",
        "split",
        "ground_truth",
        "label_source",
    }
    assert required <= set(CONTENT_HASH_FIELDS)
    assert "first_run_at" not in CONTENT_HASH_FIELDS


def test_content_hash_is_canonical_sha256_and_stable():
    case = make_case()
    expected = sha256(canonical_json_bytes(case_content_projection(case))).hexdigest()
    assert case_content_sha256(case) == expected
    assert case_content_sha256(make_case()) == expected


def test_semantic_evidence_is_bound_by_the_content_hash():
    base = make_case()
    other_inputs = replace(
        base.evaluation_inputs, semantic_evidence=make_semantic_evidence("different")
    )
    changed = make_case(evaluation_inputs=other_inputs)
    assert case_content_sha256(changed) != case_content_sha256(base)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda case: make_case(family_id="C-DEV-PURPOSE"), id="family"),
        pytest.param(
            lambda case: make_case(ground_truth=GroundTruth.BENIGN), id="ground_truth"
        ),
        pytest.param(
            lambda case: make_case(provenance=Provenance.SEPARATE_MODEL_ADVERSARIAL),
            id="provenance",
        ),
        pytest.param(
            lambda case: make_case(marker="different-content"), id="evaluation_inputs"
        ),
    ],
)
def test_content_mutation_changes_the_hash(mutate):
    base = make_case()
    assert case_content_sha256(mutate(base)) != case_content_sha256(base)


def test_audit_metadata_mutation_does_not_change_the_hash():
    base = make_case()
    baseline = case_content_sha256(base)

    different_id = make_case(index=99, marker="C-DEV-EXCLUSION-1")
    assert different_id.case_id != base.case_id
    assert case_content_sha256(different_id) == baseline

    later_adjudication = make_case(
        adjudication=TierCAdjudication(
            primary=AdjudicationRecord(
                adjudicator_id="synthetic-adjudicator-z",
                label=GroundTruth.VIOLATION,
                ambiguous=True,
                adjudicated_at=ADJUDICATED_AT + timedelta(days=5),
            )
        )
    )
    assert case_content_sha256(later_adjudication) == baseline

    executed = make_case(first_run_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert case_content_sha256(executed) == baseline

    later_authoring = make_case(authored_at=AUTHORED_AT + timedelta(days=3))
    assert case_content_sha256(later_authoring) == baseline


def test_external_source_identity_is_bound_by_the_hash():
    base = make_case(provenance=Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED)
    swapped = TierCCase(
        case_id=base.case_id,
        case_schema_version=base.case_schema_version,
        evidence_tier=base.evidence_tier,
        family_id=base.family_id,
        provenance=base.provenance,
        provenance_origin=replace(
            base.provenance_origin, source_name="other-synthetic-source"
        ),
        split=base.split,
        label_source=base.label_source,
        evaluation_inputs=base.evaluation_inputs,
        adjudication=base.adjudication,
    )
    assert case_content_sha256(swapped) != case_content_sha256(base)


def test_hash_requires_an_adjudicated_label():
    unlabelled = make_case(adjudication=TierCAdjudication())
    with pytest.raises(TierCCaseError, match="no adjudicated ground truth"):
        case_content_sha256(unlabelled)


def test_no_floats_are_accepted_anywhere_in_canonical_content():
    case = make_case()
    projection = case_content_projection(case)
    projection["evaluation_inputs"]["replay_seed"] = 1.5
    with pytest.raises(FloatNotAllowedError):
        canonical_json_bytes(projection)


def test_replay_seed_must_be_an_integer():
    with pytest.raises(TierCCaseError, match="replay_seed must be an integer"):
        TierCEvaluationInputs(
            **{
                **{
                    name: getattr(make_evaluation_inputs("C-DEV-EXCLUSION", "x"), name)
                    for name in (
                        "mandate",
                        "transaction",
                        "catalog_snapshot",
                        "server_time",
                        "nonce_state",
                        "psp_committed_hashes",
                        "evaluated_at",
                        "semantic_evidence",
                    )
                },
                "replay_seed": 1.0,
            }
        )


def test_case_round_trips_through_the_codec():
    case = make_case()
    decoded = decode_case(json.loads(case_record_line(case)))
    assert decoded == case
    assert case_content_sha256(decoded) == case_content_sha256(case)


def test_decode_rejects_a_tampered_digest():
    record = encode_case(make_case())
    record["case_content_sha256"] = "f" * 64
    with pytest.raises(TierCCaseError, match="content digest mismatch"):
        decode_case(record)


def test_decode_rejects_a_ground_truth_not_matching_adjudication():
    record = encode_case(make_case(ground_truth=GroundTruth.VIOLATION))
    record["ground_truth"] = "benign"
    with pytest.raises(TierCCaseError, match="does not match its adjudication"):
        decode_case(record)


# ---------------------------------------------------------------------------
# 14-16: second-review selection
# ---------------------------------------------------------------------------


def test_second_review_rank_matches_the_protocol_formula():
    digest = "a" * 64
    expected = sha256(
        b"mandateguard-second-review-v1" + digest.encode("utf-8")
    ).hexdigest()
    assert second_review_rank(digest) == expected
    assert SECOND_REVIEW_DOMAIN == b"mandateguard-second-review-v1"


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        (
            "0" * 64,
            "f5916a5543d0e9914ae246e371801627647e92cc9026de8a6bc404fbf508dd44",
        ),
        (
            "a" * 64,
            sha256(
                b"mandateguard-second-review-v1" + (b"a" * 64)
            ).hexdigest(),
        ),
    ],
)
def test_second_review_rank_known_vectors(digest, expected):
    assert second_review_rank(digest) == expected


def test_second_review_rank_hashes_the_digest_as_utf8_text_not_bytes():
    digest = "ab" * 32
    assert second_review_rank(digest) != sha256(
        b"mandateguard-second-review-v1" + bytes.fromhex(digest)
    ).hexdigest()


def test_second_review_rank_rejects_a_non_digest():
    for bad in ("", "xyz", "A" * 64, "0" * 63):
        with pytest.raises(TierCCaseError):
            second_review_rank(bad)


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, 0), (1, 1), (4, 1), (5, 2), (10, 3), (12, 3), (13, 4), (16, 4), (33, 9), (40, 10)],
)
def test_required_count_is_ceil_of_a_quarter(size, expected):
    assert required_second_review_count(size) == expected


def _candidate(index: int, *, ambiguous: bool = False, ground_truth=GroundTruth.VIOLATION):
    return SecondReviewCandidate(
        case_id=f"CDEV-EXC-{index:03d}",
        family_id="C-DEV-EXCLUSION",
        ground_truth=ground_truth,
        provenance=Provenance.DEVELOPER_AUTHORED,
        case_content_sha256=sha256(str(index).encode()).hexdigest(),
        ambiguous=ambiguous,
    )


def test_selection_takes_the_lowest_ranked_quarter_of_each_stratum():
    candidates = tuple(_candidate(index) for index in range(1, 17))
    selection = select_second_review(candidates)
    stratum = selection.strata[0]
    assert stratum.stratum_size == 16
    assert stratum.required_count == 4
    ordered = sorted(candidates, key=lambda item: (item.rank, item.case_id))
    assert stratum.deterministic_selection == tuple(
        item.case_id for item in ordered[:4]
    )


def test_selection_is_deterministic_and_order_independent():
    candidates = [_candidate(index) for index in range(1, 13)]
    first = select_second_review(tuple(candidates))
    second = select_second_review(tuple(reversed(candidates)))
    assert first == second


def test_selection_is_independent_per_stratum():
    candidates = tuple(
        SecondReviewCandidate(
            case_id=f"CDEV-EXC-{index:03d}",
            family_id="C-DEV-EXCLUSION",
            ground_truth=(
                GroundTruth.VIOLATION if index <= 16 else GroundTruth.BENIGN
            ),
            provenance=Provenance.DEVELOPER_AUTHORED,
            case_content_sha256=sha256(str(index).encode()).hexdigest(),
            ambiguous=False,
        )
        for index in range(1, 30)
    )
    selection = select_second_review(candidates)
    assert len(selection.strata) == 2
    for stratum in selection.strata:
        assert stratum.required_count == required_second_review_count(
            stratum.stratum_size
        )


def test_ambiguous_cases_are_added_to_the_selection():
    candidates = tuple(_candidate(index) for index in range(1, 17))
    baseline = select_second_review(candidates).strata[0]
    outside = next(
        candidate
        for candidate in candidates
        if candidate.case_id not in baseline.deterministic_selection
    )
    with_ambiguous = tuple(
        _candidate(int(candidate.case_id[-3:]), ambiguous=candidate is outside)
        for candidate in candidates
    )
    stratum = select_second_review(with_ambiguous).strata[0]
    assert outside.case_id in stratum.ambiguous_additions
    assert outside.case_id in stratum.required_second_review
    assert len(stratum.required_second_review) == baseline.required_count + 1


def test_ambiguous_case_already_selected_is_counted_once():
    candidates = tuple(_candidate(index) for index in range(1, 17))
    baseline = select_second_review(candidates).strata[0]
    inside_id = baseline.deterministic_selection[0]
    with_ambiguous = tuple(
        _candidate(
            int(candidate.case_id[-3:]), ambiguous=candidate.case_id == inside_id
        )
        for candidate in candidates
    )
    stratum = select_second_review(with_ambiguous).strata[0]
    required = stratum.required_second_review
    assert len(required) == len(set(required)) == baseline.required_count


def test_selection_rejects_duplicate_case_ids():
    with pytest.raises(TierCCaseError, match="unique case IDs"):
        select_second_review((_candidate(1), _candidate(1)))


def test_selection_candidate_carries_no_detector_field():
    fields = set(SecondReviewCandidate.__dataclass_fields__)
    assert fields == {
        "case_id",
        "family_id",
        "ground_truth",
        "provenance",
        "case_content_sha256",
        "ambiguous",
    }


def test_candidate_requires_a_primary_label():
    unlabelled = make_case(adjudication=TierCAdjudication())
    with pytest.raises(TierCCaseError, match="no primary label"):
        candidate_from_case(unlabelled, "0" * 64)


def test_resolution_changing_the_label_moves_the_rank():
    """Selection is recomputed against final corpus state, never cached."""

    primary_only = make_case(ground_truth=GroundTruth.VIOLATION)
    resolved = make_case(
        adjudication=make_adjudication(
            GroundTruth.VIOLATION,
            second_label=GroundTruth.BENIGN,
            resolution_label=GroundTruth.BENIGN,
        )
    )
    assert resolved.ground_truth is GroundTruth.BENIGN
    before = case_content_sha256(primary_only)
    after = case_content_sha256(resolved)
    assert before != after
    assert second_review_rank(before) != second_review_rank(after)
    assert primary_only.stratum != resolved.stratum


# ---------------------------------------------------------------------------
# 17-21: adjudication lifecycle, exclusion, identity
# ---------------------------------------------------------------------------


def test_adjudication_status_lifecycle():
    assert make_case(adjudication=TierCAdjudication()).status is (
        AdjudicationStatus.UNADJUDICATED
    )
    assert make_case().status is AdjudicationStatus.PRIMARY_LABELLED
    agreed = make_case(
        adjudication=make_adjudication(
            GroundTruth.VIOLATION, second_label=GroundTruth.VIOLATION
        )
    )
    assert agreed.status is AdjudicationStatus.DOUBLE_LABELLED
    disputed = make_case(
        adjudication=make_adjudication(
            GroundTruth.VIOLATION, second_label=GroundTruth.BENIGN
        )
    )
    assert disputed.status is AdjudicationStatus.DISAGREEMENT
    resolved = make_case(
        adjudication=make_adjudication(
            GroundTruth.VIOLATION,
            second_label=GroundTruth.BENIGN,
            resolution_label=GroundTruth.VIOLATION,
        )
    )
    assert resolved.status is AdjudicationStatus.RESOLVED
    excluded = make_case(
        exclusion=ExclusionRecord(
            reason="synthetic exclusion", excluded_at=ADJUDICATED_AT
        )
    )
    assert excluded.status is AdjudicationStatus.EXCLUDED


def test_ground_truth_is_derived_from_human_adjudication():
    case = make_case(ground_truth=GroundTruth.BENIGN)
    assert case.ground_truth is GroundTruth.BENIGN
    assert case.label_recorded_at == ADJUDICATED_AT
    # ground_truth is a derived property, never a stored field, so a case
    # cannot carry a label no human assigned.
    assert "ground_truth" not in TierCCase.__dataclass_fields__
    assert isinstance(type(case).ground_truth, property)


def test_unresolved_disagreement_has_no_ground_truth_and_blocks_validation():
    disputed = make_case(
        adjudication=make_adjudication(
            GroundTruth.VIOLATION, second_label=GroundTruth.BENIGN
        )
    )
    assert disputed.ground_truth is None
    report = validate_tier_c_corpus(
        [disputed], ValidationMode.PARTIAL_DEVELOPMENT
    )
    assert "UNRESOLVED_DISAGREEMENT" in _codes(report)
    assert not report.ok


def test_ambiguous_is_a_flag_not_a_ground_truth_value():
    assert {value.value for value in GroundTruth} == {"violation", "benign"}
    ambiguous = make_case(adjudication=make_adjudication(ambiguous=True))
    assert ambiguous.adjudication.marked_ambiguous
    assert ambiguous.ground_truth is GroundTruth.VIOLATION


def test_adjudication_record_cannot_hold_detector_output():
    fields = set(AdjudicationRecord.__dataclass_fields__)
    assert fields == {"adjudicator_id", "label", "ambiguous", "adjudicated_at"}
    for banned in (
        "actual_action",
        "semantic_result",
        "model_response",
        "detector_score",
    ):
        assert banned not in fields


def test_second_review_must_be_independent():
    with pytest.raises(TierCCaseError, match="independent"):
        TierCAdjudication(
            primary=AdjudicationRecord(
                adjudicator_id="same-person",
                label=GroundTruth.VIOLATION,
                ambiguous=False,
                adjudicated_at=ADJUDICATED_AT,
            ),
            second=AdjudicationRecord(
                adjudicator_id="same-person",
                label=GroundTruth.VIOLATION,
                ambiguous=False,
                adjudicated_at=ADJUDICATED_AT,
            ),
        )


def test_resolution_requires_an_actual_disagreement():
    with pytest.raises(TierCCaseError, match="actual disagreement"):
        make_adjudication(
            GroundTruth.VIOLATION,
            second_label=GroundTruth.VIOLATION,
            resolution_label=GroundTruth.VIOLATION,
        )


def test_excluded_case_cannot_execute():
    with pytest.raises(TierCCaseError, match="excluded case may never be executed"):
        make_case(
            exclusion=ExclusionRecord(
                reason="synthetic exclusion", excluded_at=ADJUDICATED_AT
            ),
            first_run_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )


def test_excluded_case_never_enters_the_manifest():
    excluded = make_case(
        exclusion=ExclusionRecord(
            reason="synthetic exclusion", excluded_at=ADJUDICATED_AT
        )
    )
    with pytest.raises(TierCCaseError, match="excluded"):
        manifest_record(excluded)


def test_excluded_case_does_not_count_toward_quota():
    excluded = make_case(
        exclusion=ExclusionRecord(
            reason="synthetic exclusion", excluded_at=ADJUDICATED_AT
        )
    )
    report = validate_tier_c_corpus([excluded], ValidationMode.PARTIAL_DEVELOPMENT)
    assert report.excluded_count == 1
    assert report.stratum_counts == {}


def test_retired_case_id_cannot_be_reused():
    replacement = make_case(index=7)
    report = validate_tier_c_corpus(
        [replacement],
        ValidationMode.PARTIAL_DEVELOPMENT,
        retired_case_ids=frozenset({replacement.case_id}),
    )
    assert "RETIRED_CASE_ID_REUSED" in _codes(report)


def test_duplicate_case_id_is_rejected():
    report = validate_tier_c_corpus(
        [make_case(index=1), make_case(index=1, marker="other")],
        ValidationMode.PARTIAL_DEVELOPMENT,
    )
    assert "DUPLICATE_CASE_ID" in _codes(report)


def test_duplicate_content_hash_is_rejected():
    report = validate_tier_c_corpus(
        [make_case(index=1, marker="same"), make_case(index=2, marker="same")],
        ValidationMode.PARTIAL_DEVELOPMENT,
    )
    assert "DUPLICATE_CONTENT_HASH" in _codes(report)
    assert "EXACT_DUPLICATE" in _codes(report)


def test_case_id_must_match_its_family():
    with pytest.raises(TierCCaseError, match="does not use the"):
        TierCCase(
            case_id="CDEV-PUR-001",
            case_schema_version="1.2",
            evidence_tier="C",
            family_id="C-DEV-EXCLUSION",
            provenance=Provenance.DEVELOPER_AUTHORED,
            provenance_origin=make_origin(Provenance.DEVELOPER_AUTHORED),
            split=Split.DEV,
            label_source="human_adjudication",
            evaluation_inputs=make_evaluation_inputs("C-DEV-EXCLUSION", "x"),
            adjudication=make_adjudication(),
        )


def test_case_id_does_not_encode_ground_truth():
    violation = make_case(index=5, ground_truth=GroundTruth.VIOLATION)
    benign = make_case(index=5, ground_truth=GroundTruth.BENIGN)
    assert violation.case_id == benign.case_id == "CDEV-EXC-005"


# ---------------------------------------------------------------------------
# 22-23: duplicate tooling
# ---------------------------------------------------------------------------


def test_exact_duplicate_detection():
    left = make_case(index=1, marker="identical")
    right = make_case(index=2, marker="identical")
    hashes = {
        case.case_id: case_content_sha256(case) for case in (left, right)
    }
    report = review_duplicates([left, right], hashes)
    assert report.has_exact_duplicates
    assert report.exact_duplicate_groups == (("CDEV-EXC-001", "CDEV-EXC-002"),)


def test_distinct_cases_are_not_duplicates():
    left = make_case(index=1, marker="alpha content one")
    right = make_case(index=2, marker="beta different two")
    hashes = {case.case_id: case_content_sha256(case) for case in (left, right)}
    report = review_duplicates([left, right], hashes)
    assert not report.has_exact_duplicates
    assert not report.has_normalized_duplicates


def test_normalized_text_comparison_is_deterministic():
    assert normalize_text("Hello,   World!") == "hello world"
    assert normalize_text("HELLO world") == normalize_text("hello   WORLD")
    assert normalize_text("a-b_c") == "a b c"
    assert normalize_text("  ") == ""
    assert normalize_text("café") == normalize_text("café")
    for _ in range(3):
        assert normalize_text("Repeat/Me 42") == "repeat me 42"


def test_normalized_duplicate_group_detects_punctuation_only_differences():
    base = make_case(index=1, marker="alpha")
    other_mandate = make_semantic_mandate("C-DEV-EXCLUSION", "alpha")
    punctuated = replace(
        other_mandate,
        payload=replace(
            other_mandate.payload,
            constraints=replace(
                other_mandate.payload.constraints,
                semantic=(
                    SemanticConstraint(
                        constraint_id="synthetic-alpha",
                        kind="exclusion",
                        text="SYNTHETIC, placeholder!  constraint  text -- alpha",
                    ),
                ),
            ),
        ),
    )
    variant = make_case(
        index=2,
        evaluation_inputs=make_evaluation_inputs(
            "C-DEV-EXCLUSION", "alpha", mandate=punctuated
        ),
    )
    assert normalized_fingerprint(base) == normalized_fingerprint(variant)
    hashes = {case.case_id: case_content_sha256(case) for case in (base, variant)}
    report = review_duplicates([base, variant], hashes)
    assert report.has_normalized_duplicates
    assert not report.has_exact_duplicates


def test_manual_review_candidates_are_reported_not_declared():
    report = review_duplicates([], {})
    assert report.manual_review_candidates == ()
    assert report.exact_duplicate_groups == ()
    assert report.normalized_text_duplicate_groups == ()


def test_duplicate_review_uses_no_model_or_embedding_library():
    import ast

    tree = ast.parse((TIER_C_PACKAGE / "dedup.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("openai", "anthropic", "numpy", "torch", "sklearn", "transformers"):
        assert banned not in imported


# ---------------------------------------------------------------------------
# 24-27: development quotas
# ---------------------------------------------------------------------------


def test_registered_allocation_matches_the_protocol_tables():
    assert sum(TIER_C_ALLOCATION.values()) == TIER_C_TOTAL == 440
    dev = allocation_for_split(Split.DEV)
    held = allocation_for_split(Split.HELD_OUT)
    assert sum(dev.values()) == DEVELOPMENT_TOTAL == 220
    assert sum(held.values()) == HELD_OUT_TOTAL == 220
    violations = sum(
        count for key, count in TIER_C_ALLOCATION.items()
        if key[1] is GroundTruth.VIOLATION
    )
    assert violations == 240
    by_provenance = {
        provenance: sum(
            count for key, count in TIER_C_ALLOCATION.items() if key[2] is provenance
        )
        for provenance in Provenance
    }
    assert by_provenance == {
        Provenance.DEVELOPER_AUTHORED: 176,
        Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED: 132,
        Provenance.SEPARATE_MODEL_ADVERSARIAL: 132,
    }
    for family in TIER_C_FAMILIES:
        assert TIER_C_ALLOCATION[
            (family, GroundTruth.VIOLATION, Provenance.DEVELOPER_AUTHORED)
        ] == 16
        assert TIER_C_ALLOCATION[
            (family, GroundTruth.VIOLATION, Provenance.SEPARATE_MODEL_ADVERSARIAL)
        ] == 12


def test_empty_corpus_is_valid_partial_development_state():
    report = validate_tier_c_corpus([], ValidationMode.PARTIAL_DEVELOPMENT)
    assert report.ok
    assert report.case_count == 0


def test_partial_development_accepts_incomplete_quotas():
    cases = [make_case(index=index) for index in range(1, 4)]
    report = validate_tier_c_corpus(cases, ValidationMode.PARTIAL_DEVELOPMENT)
    assert report.ok, report.render()


def test_quota_excess_is_rejected_even_in_partial_mode():
    allocated = TIER_C_ALLOCATION[
        ("C-DEV-EXCLUSION", GroundTruth.VIOLATION, Provenance.DEVELOPER_AUTHORED)
    ]
    cases = [make_case(index=index) for index in range(1, allocated + 2)]
    report = validate_tier_c_corpus(cases, ValidationMode.PARTIAL_DEVELOPMENT)
    assert "QUOTA_EXCESS" in _codes(report)


def test_exact_220_development_allocation_is_accepted():
    cases = make_allocated_corpus(DEV_FAMILIES)
    assert len(cases) == DEVELOPMENT_TOTAL
    report = validate_tier_c_corpus(cases, ValidationMode.FINAL_DEVELOPMENT)
    assert report.ok, report.render()
    assert sum(report.stratum_counts.values()) == 220


def test_final_development_rejects_an_incomplete_corpus():
    cases = make_allocated_corpus(DEV_FAMILIES)[:-1]
    report = validate_tier_c_corpus(cases, ValidationMode.FINAL_DEVELOPMENT)
    codes = _codes(report)
    assert "QUOTA_INCOMPLETE" in codes
    assert "PROVENANCE_STRATA_MISMATCH" in codes


def test_wrong_provenance_allocation_is_rejected():
    cases = make_allocated_corpus(DEV_FAMILIES)
    swapped: list[TierCCase] = []
    replaced = False
    for case in cases:
        if (
            not replaced
            and case.family_id == "C-DEV-PURPOSE"
            and case.provenance is Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED
            and case.ground_truth is GroundTruth.VIOLATION
        ):
            replaced = True
            swapped.append(
                make_case(
                    family_id=case.family_id,
                    index=int(case.case_id[-3:]),
                    ground_truth=GroundTruth.VIOLATION,
                    provenance=Provenance.DEVELOPER_AUTHORED,
                    adjudication=case.adjudication,
                )
            )
            continue
        swapped.append(case)
    assert replaced
    report = validate_tier_c_corpus(swapped, ValidationMode.FINAL_DEVELOPMENT)
    codes = _codes(report)
    assert "PROVENANCE_STRATA_MISMATCH" in codes or "QUOTA_EXCESS" in codes


def test_final_development_requires_completed_second_reviews():
    cases = make_allocated_corpus(DEV_FAMILIES, second_review_everything=False)
    report = validate_tier_c_corpus(cases, ValidationMode.FINAL_DEVELOPMENT)
    assert "SECOND_REVIEW_INCOMPLETE" in _codes(report)


def test_first_run_at_must_be_null_before_first_execution():
    executed = make_case(first_run_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    report = validate_tier_c_corpus([executed], ValidationMode.PARTIAL_DEVELOPMENT)
    assert "UNEXPECTED_FIRST_RUN" in _codes(report)


def test_missing_primary_label_is_reported():
    unlabelled = make_case(adjudication=TierCAdjudication())
    report = validate_tier_c_corpus([unlabelled], ValidationMode.PARTIAL_DEVELOPMENT)
    assert "MISSING_PRIMARY_LABEL" in _codes(report)


def test_held_out_case_is_rejected_by_development_mode():
    report = validate_tier_c_corpus(
        [make_case(family_id="C-HOLD-BUNDLE")], ValidationMode.PARTIAL_DEVELOPMENT
    )
    assert "WRONG_SPLIT_FOR_MODE" in _codes(report)


# ---------------------------------------------------------------------------
# 28-30: held-out guards
# ---------------------------------------------------------------------------


def test_held_out_content_authored_before_freeze_is_rejected():
    early = make_case(
        family_id="C-HOLD-BUNDLE",
        authored_at=FREEZE_AT - timedelta(days=1),
    )
    issues = validate_held_out_isolation(early, FREEZE_AT)
    assert {issue.code for issue in issues} == {"HELD_OUT_AUTHORED_BEFORE_FREEZE"}


def test_held_out_external_source_selected_before_freeze_is_rejected():
    early_source = TierCCase(
        case_id="CHOLD-BUN-001",
        case_schema_version="1.2",
        evidence_tier="C",
        family_id="C-HOLD-BUNDLE",
        provenance=Provenance.EXTERNAL_DEFENSIVE_CORPUS_ADAPTED,
        provenance_origin=ExternalCorpusOrigin(
            authored_at=FREEZE_AT + timedelta(days=1),
            source_selected_at=FREEZE_AT - timedelta(days=1),
            source_name="synthetic-source",
            source_reference="https://example.invalid/synthetic",
            source_version=None,
            adaptation_description="synthetic adaptation",
        ),
        split=Split.HELD_OUT,
        label_source="human_adjudication",
        evaluation_inputs=make_evaluation_inputs("C-HOLD-BUNDLE", "x"),
        adjudication=make_adjudication(),
    )
    issues = validate_held_out_isolation(early_source, FREEZE_AT)
    assert {issue.code for issue in issues} == {
        "HELD_OUT_SOURCE_SELECTED_BEFORE_FREEZE"
    }


def test_held_out_content_after_freeze_passes_isolation():
    late = make_case(
        family_id="C-HOLD-BUNDLE", authored_at=FREEZE_AT + timedelta(days=1)
    )
    assert validate_held_out_isolation(late, FREEZE_AT) == ()


def test_development_case_is_not_subject_to_the_freeze_audit():
    early = make_case(authored_at=FREEZE_AT - timedelta(days=30))
    assert validate_held_out_isolation(early, FREEZE_AT) == ()


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


def test_held_out_checkpoint_requires_all_220_cases():
    cases = make_allocated_corpus(
        HELD_OUT_FAMILIES, authored_at=FREEZE_AT + timedelta(days=1)
    )
    assert len(cases) == HELD_OUT_TOTAL
    assert validate_held_out_checkpoint(
        cases, _checkpoint(), detector_freeze_at=FREEZE_AT
    ) == ()

    short = cases[:-1]
    issues = validate_held_out_checkpoint(
        short, _checkpoint(), detector_freeze_at=FREEZE_AT
    )
    assert "CHECKPOINT_COUNT_MISMATCH" in {issue.code for issue in issues}


def test_held_out_checkpoint_rejects_a_declared_count_below_220():
    cases = make_allocated_corpus(
        HELD_OUT_FAMILIES, authored_at=FREEZE_AT + timedelta(days=1)
    )[:100]
    issues = validate_held_out_checkpoint(
        cases,
        _checkpoint(
            total_held_out_cases=100,
            ground_truth_recorded_count=100,
            content_hash_recorded_count=100,
            first_run_null_count=100,
        ),
        detector_freeze_at=FREEZE_AT,
    )
    codes = {issue.code for issue in issues}
    assert "CHECKPOINT_COUNT_NOT_220" in codes


def test_held_out_checkpoint_requires_first_run_null_for_all():
    cases = make_allocated_corpus(
        HELD_OUT_FAMILIES, authored_at=FREEZE_AT + timedelta(days=1)
    )
    executed = list(cases)
    first = executed[0]
    executed[0] = make_case(
        family_id=first.family_id,
        index=int(first.case_id[-3:]),
        ground_truth=first.ground_truth,
        provenance=first.provenance,
        authored_at=FREEZE_AT + timedelta(days=1),
        adjudication=first.adjudication,
        first_run_at=FREEZE_AT + timedelta(days=20),
    )
    issues = validate_held_out_checkpoint(
        executed, _checkpoint(), detector_freeze_at=FREEZE_AT
    )
    codes = {issue.code for issue in issues}
    assert "HELD_OUT_ALREADY_EXECUTED" in codes
    assert "CHECKPOINT_COUNT_MISMATCH" in codes


def test_held_out_checkpoint_rejects_non_held_out_cases():
    issues = validate_held_out_checkpoint(
        [make_case()], _checkpoint(), detector_freeze_at=FREEZE_AT
    )
    assert "NON_HELD_OUT_CASE" in {issue.code for issue in issues}


def test_held_out_checkpoint_has_no_per_case_variant():
    """No pilot, smoke, or calibration subset is reachable (protocol 7.1)."""

    import mandateguard.benchmark.tier_c.validation as validation

    exported = [name for name in dir(validation) if "held_out" in name]
    assert "validate_held_out_checkpoint" in exported
    assert not any(
        name for name in exported if "pilot" in name or "subset" in name or "partial" in name
    )


def test_held_out_final_mode_requires_the_freeze_timestamp():
    cases = make_allocated_corpus(
        HELD_OUT_FAMILIES, authored_at=FREEZE_AT + timedelta(days=1)
    )
    report = validate_tier_c_corpus(cases, ValidationMode.HELD_OUT_FINAL)
    assert "MISSING_DETECTOR_FREEZE" in _codes(report)
    ok = validate_tier_c_corpus(
        cases, ValidationMode.HELD_OUT_FINAL, detector_freeze_at=FREEZE_AT
    )
    assert ok.ok, ok.render()


# ---------------------------------------------------------------------------
# Label immutability after first execution
# ---------------------------------------------------------------------------


def test_label_and_content_are_immutable_after_first_run():
    executed = make_case(first_run_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    relabelled = make_case(
        ground_truth=GroundTruth.BENIGN,
        first_run_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    changed = immutability_violations(executed, relabelled)
    assert "ground_truth" in changed
    assert "case_content_sha256" in changed
    with pytest.raises(TierCCaseError, match="changed after first execution"):
        assert_immutable_after_first_run(executed, relabelled)


def test_pre_execution_correction_is_permitted():
    before = make_case(marker="first")
    after = make_case(marker="corrected")
    assert immutability_violations(before, after) == ()
    assert_immutable_after_first_run(before, after)


def test_immutability_covers_every_registered_field():
    ran_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    executed = make_case(first_run_at=ran_at)
    other = make_case(
        provenance=Provenance.SEPARATE_MODEL_ADVERSARIAL,
        marker="rewritten",
        first_run_at=ran_at,
    )
    changed = set(immutability_violations(executed, other))
    assert {
        "provenance",
        "evaluation_inputs",
        "semantic_evidence",
        "case_content_sha256",
    } <= changed
    from mandateguard.benchmark.tier_c.models import IMMUTABLE_AFTER_FIRST_RUN

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


def test_immutability_compares_one_case_not_two():
    ran_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(TierCCaseError, match="two versions of one case"):
        immutability_violations(
            make_case(first_run_at=ran_at),
            make_case(family_id="C-DEV-PURPOSE", first_run_at=ran_at),
        )


# ---------------------------------------------------------------------------
# 31: manifest integration
# ---------------------------------------------------------------------------


def test_manifest_record_uses_the_frozen_required_fields():
    record = manifest_record(make_case())
    assert tuple(record) == TIER_C_MANIFEST_FIELDS
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    required_block = manifest_text.split("required_fields:")[1].split("optional_fields:")[0]
    required = tuple(
        line.strip().lstrip("- ").strip()
        for line in required_block.strip().splitlines()
    )
    assert tuple(record) == required


def test_tier_c_manifest_record_omits_expected_action():
    record = manifest_record(make_case())
    assert "expected_action" not in record
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "Tier C cases omit expected_action" in manifest_text


def test_manifest_record_values_match_the_frozen_enums():
    record = manifest_record(make_case(family_id="C-HOLD-BUNDLE"))
    assert record["evidence_tier"] == "C"
    assert record["family_id"] == "C-HOLD-BUNDLE"
    assert record["split"] == "held_out"
    assert record["label_source"] == "human_adjudication"
    assert record["ground_truth"] in {"violation", "benign"}
    assert record["first_run_at"] is None
    assert re.fullmatch(r"[0-9a-f]{64}", record["case_content_sha256"])


def test_manifest_block_renders_in_the_registered_layout():
    block = render_cases_block([manifest_record(make_case())])
    lines = block.rstrip("\n").split("\n")
    assert lines[0].startswith("  - case_id: ")
    assert all(line.startswith("    ") for line in lines[1:])
    assert len(lines) == len(TIER_C_MANIFEST_FIELDS)


def test_manifest_record_requires_a_recorded_label():
    with pytest.raises(TierCCaseError, match="recorded human label"):
        manifest_record(make_case(adjudication=TierCAdjudication()))


# ---------------------------------------------------------------------------
# 32-34: committed repository state
# ---------------------------------------------------------------------------


def test_manifest_still_contains_exactly_1008_records():
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert len(re.findall(r"^  - case_id:", text, flags=re.MULTILINE)) == 1008


def test_manifest_contains_no_tier_c_record():
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    cases_block = text.split("\ncases:", 1)[1]
    for family in TIER_C_FAMILIES:
        assert f'family_id: "{family}"' not in cases_block
    assert 'label_source: "human_adjudication"' not in cases_block
    assert 'split: "held_out"' not in cases_block


def test_no_tier_c_case_content_is_committed():
    corpus_root = REPOSITORY_ROOT / "benchmark" / "cases" / "tier_c"
    if corpus_root.exists():
        assert list(corpus_root.rglob("*.jsonl")) == []
    for split in (Split.DEV, Split.HELD_OUT):
        corpus = load_corpus(REPOSITORY_ROOT / "benchmark" / "cases" / "tier_c", split)
        assert corpus.is_empty
        assert corpus.cases == ()


def test_empty_real_corpus_state_validates():
    root = REPOSITORY_ROOT / "benchmark" / "cases" / "tier_c"
    for split, mode in (
        (Split.DEV, ValidationMode.PARTIAL_DEVELOPMENT),
        (Split.HELD_OUT, ValidationMode.PARTIAL_DEVELOPMENT),
    ):
        corpus = load_corpus(root, split)
        report = validate_tier_c_corpus(corpus.cases, mode)
        assert report.ok
        assert report.case_count == 0


def test_no_tier_c_results_directory_exists():
    results = REPOSITORY_ROOT / "benchmark" / "results"
    tier_c_results = [path for path in results.rglob("*") if "tier_c" in path.name]
    assert tier_c_results == []


def test_corpus_import_refuses_duplicate_id_and_content():
    case = make_case()
    digest = case_content_sha256(case)
    corpus = TierCCorpus(
        split=Split.DEV, cases=(case,), content_hashes={case.case_id: digest}
    )
    record = encode_case(case)
    with pytest.raises(TierCCaseError, match="already used"):
        import_case(record, corpus)

    other = encode_case(make_case(index=2))
    other_case = decode_case(other)
    corpus_by_content = TierCCorpus(
        split=Split.DEV,
        cases=(case,),
        content_hashes={case.case_id: case_content_sha256(other_case)},
    )
    with pytest.raises(TierCCaseError, match="duplicates the content"):
        import_case(other, corpus_by_content)


def test_corpus_import_refuses_an_unadjudicated_case():
    record = encode_case(make_case())
    record["adjudication"] = {
        "primary": None,
        "second": None,
        "resolution": None,
        "status": "UNADJUDICATED",
    }
    empty = TierCCorpus(split=Split.DEV, cases=(), content_hashes={})
    with pytest.raises(TierCCaseError):
        import_case(record, empty)


def test_family_file_layout_is_declared_for_all_six_families():
    assert set(FAMILY_FILES) == set(TIER_C_FAMILIES)
    assert FAMILY_FILES["C-DEV-RECURRENCE"] == "dev/recurrence.jsonl"
    assert FAMILY_FILES["C-HOLD-FULFILLMENT"] == "held_out/fulfillment.jsonl"


# ---------------------------------------------------------------------------
# 35: detector isolation
# ---------------------------------------------------------------------------


DETECTOR_SYMBOLS = frozenset(
    {
        "authorize_transaction",
        "finalize_authorization",
        "SemanticVerifier",
        "OpenAIResponsesSemanticModel",
        "evaluate_tier_a",
        "evaluate_tier_b",
    }
)


def test_tier_c_sources_import_no_detector_module():
    """Static check over parsed imports, so prose in docstrings cannot mask it."""

    import ast

    modules = sorted(TIER_C_PACKAGE.glob("*.py"))
    assert modules, "expected the Tier C package to contain modules"
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(FORBIDDEN_MODULE_PREFIXES), (
                    f"{path.name} imports {name}"
                )


def test_tier_c_sources_reference_no_detector_symbol():
    """No detector symbol is *used* in Tier C code (docstrings may name them)."""

    import ast

    for path in sorted(TIER_C_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in DETECTOR_SYMBOLS, f"{path.name} uses {node.id}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in DETECTOR_SYMBOLS, f"{path.name} uses {node.attr}"
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in DETECTOR_SYMBOLS, (
                        f"{path.name} imports {alias.name}"
                    )


def test_tier_c_infrastructure_loads_no_detector_module():
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT / 'src')!r})\n"
        "import mandateguard.benchmark.tier_c as tier_c\n"
        "from mandateguard.benchmark.tier_c import codec, corpus, dedup, manifest\n"
        "from mandateguard.benchmark.tier_c import models, second_review, validation\n"
        "validation.validate_tier_c_corpus([], validation.ValidationMode."
        "PARTIAL_DEVELOPMENT)\n"
        "second_review.select_second_review(())\n"
        "loaded = sorted(n for n in sys.modules if n.startswith("
        f"{FORBIDDEN_MODULE_PREFIXES!r}))\n"
        "print('|'.join(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == ""


def test_building_and_hashing_a_case_loads_no_detector_module():
    """Constructing and hashing a case must stay detector-free too."""

    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r})\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT / 'src')!r})\n"
        "from tests.tier_c_factories import make_case\n"
        "from mandateguard.benchmark.tier_c.codec import case_record_line\n"
        "case_record_line(make_case())\n"
        "loaded = sorted(n for n in sys.modules if n.startswith("
        f"{FORBIDDEN_MODULE_PREFIXES!r}))\n"
        "print('|'.join(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == ""


def test_semantic_evidence_mirror_matches_the_frozen_d5_types():
    """Pin the mirror against the frozen D5 classes so it cannot drift.

    The frozen classes are inspected in a subprocess precisely because
    importing them loads the detector, which this package must never do.
    """

    program = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT / 'src')!r})\n"
        "from mandateguard.semantic.evidence import ("
        "SemanticEvidenceBundle, SemanticEvidenceEntry)\n"
        "print(json.dumps({\n"
        "  'entry': [f.name for f in SemanticEvidenceEntry.__dataclass_fields__"
        ".values()],\n"
        "  'bundle': [f.name for f in SemanticEvidenceBundle.__dataclass_fields__"
        ".values()],\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    frozen = json.loads(completed.stdout)
    assert frozen["entry"] == [
        field.name
        for field in SemanticEvidenceEntryRecord.__dataclass_fields__.values()
    ]
    assert frozen["bundle"] == [
        field.name
        for field in SemanticEvidenceBundleRecord.__dataclass_fields__.values()
    ]


def test_semantic_evidence_mirror_produces_the_frozen_canonical_bytes():
    """The mirror must hash identically to the frozen D5 bundle."""

    bundle = make_semantic_evidence("pinned")
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT / 'src')!r})\n"
        "from mandateguard.semantic.evidence import ("
        "SemanticEvidenceBundle, SemanticEvidenceEntry, semantic_evidence_sha256)\n"
        "bundle = SemanticEvidenceBundle(merchant_id='merchant-1', entries=(\n"
        "  SemanticEvidenceEntry(evidence_id='synthetic-evidence-pinned',\n"
        "    merchant_id='merchant-1', sku='sku-1',\n"
        "    source_kind='product_description',\n"
        "    text='synthetic placeholder evidence text pinned'),))\n"
        "print(semantic_evidence_sha256(bundle))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    from mandateguard.core.hashing import sha256_canonical

    assert sha256_canonical(bundle) == completed.stdout.strip()


def test_tier_c_package_declares_it_holds_no_case_content():
    readme = (REPOSITORY_ROOT / "benchmark" / "tier_c" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "infrastructure only" in readme.lower()
    assert "**0**" in readme
    assert "0 / 220" in readme
