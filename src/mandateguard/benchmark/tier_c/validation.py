"""Tier C corpus validation, held-out guards, and the finalization checkpoint.

One high-level validator answers, for a collection of Tier C cases: may this
corpus proceed, and if not, exactly why. It never calls a detector, never
assigns a label, and never repairs a case.

Modes
-----

``PARTIAL_DEVELOPMENT``
    Valid while authoring. Quotas may be incomplete but may never be exceeded,
    and no stratum may hold more than its registered allocation. An empty
    corpus is a valid partial-development state, which is exactly the state
    D8-A commits.

``FINAL_DEVELOPMENT``
    Exactly 220 development cases with every registered family, ground-truth,
    and provenance quota exact, every label recorded, every disagreement
    resolved, and the required second reviews complete.

``HELD_OUT_FINAL``
    Exactly 220 held-out cases under the same rules, plus the protocol 7.1
    batch requirements and the held-out source-isolation audit. Used at or
    before D10; D8-A implements the guard, not the execution.

Held-out execution additionally requires the protocol 7.2 checkpoint, validated
by :func:`validate_held_out_checkpoint`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from mandateguard.benchmark.tier_c.codec import (
    case_content_sha256,
    encode_provenance_origin_audit,
)
from mandateguard.benchmark.tier_c.dedup import DuplicateReport, review_duplicates
from mandateguard.benchmark.tier_c.models import (
    AdjudicationStatus,
    DEVELOPMENT_TOTAL,
    ExternalCorpusOrigin,
    FAMILY_SPLIT,
    GroundTruth,
    HELD_OUT_TOTAL,
    IMMUTABLE_AFTER_FIRST_RUN,
    Provenance,
    Split,
    StratumKey,
    TierCCase,
    TierCCaseError,
    allocation_for_split,
    structural_issues,
)
from mandateguard.benchmark.tier_c.second_review import (
    SecondReviewSelection,
    candidate_from_case,
    select_second_review,
)


class ValidationMode(str, Enum):
    PARTIAL_DEVELOPMENT = "partial_development"
    FINAL_DEVELOPMENT = "final_development"
    HELD_OUT_FINAL = "held_out_final"


MODE_SPLIT = {
    ValidationMode.PARTIAL_DEVELOPMENT: Split.DEV,
    ValidationMode.FINAL_DEVELOPMENT: Split.DEV,
    ValidationMode.HELD_OUT_FINAL: Split.HELD_OUT,
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    case_id: str | None = None

    def render(self) -> str:
        scope = self.case_id or "corpus"
        return f"[{self.code}] {scope}: {self.message}"


@dataclass(frozen=True, slots=True)
class TierCValidationReport:
    mode: ValidationMode
    issues: tuple[ValidationIssue, ...]
    case_count: int
    excluded_count: int
    stratum_counts: dict[StratumKey, int] = field(default_factory=dict)
    second_review: SecondReviewSelection | None = None
    duplicates: DuplicateReport | None = None

    @property
    def ok(self) -> bool:
        return not self.issues

    def render(self) -> str:
        header = (
            f"mode={self.mode.value} cases={self.case_count} "
            f"excluded={self.excluded_count} issues={len(self.issues)}"
        )
        if not self.issues:
            return f"{header}\nOK"
        return "\n".join([header, *(issue.render() for issue in self.issues)])


def _second_review_complete(case: TierCCase) -> bool:
    return case.adjudication.second is not None


def validate_tier_c_corpus(
    cases: tuple[TierCCase, ...] | list[TierCCase],
    mode: ValidationMode,
    *,
    detector_freeze_at: datetime | None = None,
    retired_case_ids: frozenset[str] = frozenset(),
    require_second_review: bool = True,
) -> TierCValidationReport:
    """Validate a Tier C corpus against the frozen rules for ``mode``."""

    if not isinstance(mode, ValidationMode):
        raise TierCCaseError("mode must be a ValidationMode")
    case_list = list(cases)
    if not all(isinstance(case, TierCCase) for case in case_list):
        raise TierCCaseError("cases must be TierCCase values")
    expected_split = MODE_SPLIT[mode]
    is_final = mode is not ValidationMode.PARTIAL_DEVELOPMENT

    issues: list[ValidationIssue] = []

    def add(code: str, message: str, case_id: str | None = None) -> None:
        issues.append(ValidationIssue(code=code, message=message, case_id=case_id))

    # --- per-case structure -------------------------------------------------
    content_hashes: dict[str, str] = {}
    for case in case_list:
        for issue in structural_issues(case):
            add(issue.code, issue.message, case.case_id)
        if FAMILY_SPLIT.get(case.family_id) is not expected_split:
            add(
                "WRONG_SPLIT_FOR_MODE",
                f"{case.family_id} does not belong to the {expected_split.value} split",
                case.case_id,
            )
        if case.case_id in retired_case_ids:
            add(
                "RETIRED_CASE_ID_REUSED",
                "a retired case ID may never be reused (protocol 5.2)",
                case.case_id,
            )
        if case.first_run_at is not None:
            add(
                "UNEXPECTED_FIRST_RUN",
                "first_run_at must be null before the first detector execution",
                case.case_id,
            )
        if case.ground_truth is None:
            if case.adjudication.status is AdjudicationStatus.DISAGREEMENT:
                add(
                    "UNRESOLVED_DISAGREEMENT",
                    "an unresolved disagreement must be resolved or excluded "
                    "before execution (protocol 5.2)",
                    case.case_id,
                )
            elif case.exclusion is None:
                add(
                    "MISSING_PRIMARY_LABEL",
                    "no human ground truth has been recorded (protocol 5)",
                    case.case_id,
                )
        else:
            try:
                content_hashes[case.case_id] = case_content_sha256(case)
            except TierCCaseError as error:
                add("CONTENT_HASH_ERROR", str(error), case.case_id)

    # --- identity uniqueness ------------------------------------------------
    seen_ids: set[str] = set()
    duplicate_ids = False
    for case in case_list:
        if case.case_id in seen_ids:
            duplicate_ids = True
            add("DUPLICATE_CASE_ID", "case_id appears more than once", case.case_id)
        seen_ids.add(case.case_id)

    hash_owners: dict[str, list[str]] = {}
    for case_id, digest in content_hashes.items():
        hash_owners.setdefault(digest, []).append(case_id)
    for digest, owners in sorted(hash_owners.items()):
        if len(owners) > 1:
            add(
                "DUPLICATE_CONTENT_HASH",
                f"identical case content shared by {', '.join(sorted(owners))} "
                f"(digest {digest})",
            )

    # --- quotas -------------------------------------------------------------
    countable = [
        case
        for case in case_list
        if case.exclusion is None and case.ground_truth is not None
    ]
    excluded = [case for case in case_list if case.exclusion is not None]
    stratum_counts: dict[StratumKey, int] = {}
    for case in countable:
        stratum = case.stratum
        if stratum is not None:
            stratum_counts[stratum] = stratum_counts.get(stratum, 0) + 1

    expected = allocation_for_split(expected_split)
    for stratum, actual in sorted(
        stratum_counts.items(), key=lambda item: (item[0][0], item[0][1].value, item[0][2].value)
    ):
        allowed = expected.get(stratum)
        if allowed is None:
            add(
                "UNREGISTERED_STRATUM",
                f"stratum {stratum[0]}/{stratum[1].value}/{stratum[2].value} "
                "is not part of the registered allocation",
            )
        elif actual > allowed:
            add(
                "QUOTA_EXCESS",
                f"stratum {stratum[0]}/{stratum[1].value}/{stratum[2].value} "
                f"holds {actual} cases, registered allocation is {allowed}",
            )
    if is_final:
        for stratum, allowed in sorted(
            expected.items(), key=lambda item: (item[0][0], item[0][1].value, item[0][2].value)
        ):
            actual = stratum_counts.get(stratum, 0)
            if actual != allowed:
                add(
                    "PROVENANCE_STRATA_MISMATCH",
                    f"stratum {stratum[0]}/{stratum[1].value}/{stratum[2].value} "
                    f"holds {actual} cases, registered allocation is {allowed}",
                )
        registered_total = (
            DEVELOPMENT_TOTAL if expected_split is Split.DEV else HELD_OUT_TOTAL
        )
        if len(countable) != registered_total:
            add(
                "QUOTA_INCOMPLETE",
                f"{expected_split.value} split holds {len(countable)} executable "
                f"cases, registered total is {registered_total}",
            )

    # --- second review ------------------------------------------------------
    selection: SecondReviewSelection | None = None
    if content_hashes and not duplicate_ids:
        candidates = tuple(
            candidate_from_case(case, content_hashes[case.case_id])
            for case in countable
            if case.case_id in content_hashes
        )
        selection = select_second_review(candidates)
        if require_second_review and is_final:
            by_id = {case.case_id: case for case in countable}
            for case_id in sorted(selection.required_case_ids):
                case = by_id.get(case_id)
                if case is not None and not _second_review_complete(case):
                    add(
                        "SECOND_REVIEW_INCOMPLETE",
                        "deterministic selection requires an independent second "
                        "label (protocol 5.1)",
                        case_id,
                    )

    # --- duplicate review (protocol 4.1) ------------------------------------
    duplicates: DuplicateReport | None = None
    if content_hashes and not duplicate_ids:
        reviewable = [case for case in countable if case.case_id in content_hashes]
        duplicates = review_duplicates(reviewable, content_hashes)
        for group in duplicates.exact_duplicate_groups:
            add(
                "EXACT_DUPLICATE",
                f"exact duplicate case content: {', '.join(group)} (protocol 4.1)",
            )
        for group in duplicates.normalized_text_duplicate_groups:
            add(
                "NORMALIZED_TEXT_DUPLICATE",
                f"identical normalized case text: {', '.join(group)} (protocol 4.1)",
            )

    # --- held-out source isolation (protocol 3.1, 12) -----------------------
    if expected_split is Split.HELD_OUT and detector_freeze_at is not None:
        for case in case_list:
            issues.extend(
                _held_out_isolation_issues(case, detector_freeze_at)
            )
    elif expected_split is Split.HELD_OUT and is_final:
        add(
            "MISSING_DETECTOR_FREEZE",
            "held-out validation requires the detector freeze timestamp to audit "
            "source isolation (protocol 3.1)",
        )

    return TierCValidationReport(
        mode=mode,
        issues=tuple(issues),
        case_count=len(case_list),
        excluded_count=len(excluded),
        stratum_counts=stratum_counts,
        second_review=selection,
        duplicates=duplicates,
    )


def _held_out_isolation_issues(
    case: TierCCase, detector_freeze_at: datetime
) -> list[ValidationIssue]:
    """Audit that held-out content post-dates detector freeze.

    This is an audit check over recorded timestamps. It is not, and is not
    presented as, proof that no one read held-out source material early.
    """

    if (
        not isinstance(detector_freeze_at, datetime)
        or detector_freeze_at.tzinfo is None
        or detector_freeze_at.utcoffset() is None
    ):
        raise TierCCaseError("detector_freeze_at must be a timezone-aware datetime")
    if case.split is not Split.HELD_OUT:
        return []
    found: list[ValidationIssue] = []
    origin = case.provenance_origin
    if origin.authored_at < detector_freeze_at:
        found.append(
            ValidationIssue(
                code="HELD_OUT_AUTHORED_BEFORE_FREEZE",
                message=(
                    f"held-out content authored at {origin.authored_at.isoformat()} "
                    f"predates detector freeze {detector_freeze_at.isoformat()} "
                    "(protocol 3.1, 7.1)"
                ),
                case_id=case.case_id,
            )
        )
    if isinstance(origin, ExternalCorpusOrigin) and (
        origin.source_selected_at < detector_freeze_at
    ):
        found.append(
            ValidationIssue(
                code="HELD_OUT_SOURCE_SELECTED_BEFORE_FREEZE",
                message=(
                    "held-out source material was selected at "
                    f"{origin.source_selected_at.isoformat()}, before detector "
                    f"freeze {detector_freeze_at.isoformat()} (protocol 3.1)"
                ),
                case_id=case.case_id,
            )
        )
    return found


def validate_held_out_isolation(
    case: TierCCase, detector_freeze_at: datetime
) -> tuple[ValidationIssue, ...]:
    """Public single-case held-out isolation audit."""

    return tuple(_held_out_isolation_issues(case, detector_freeze_at))


# ---------------------------------------------------------------------------
# Held-out batch-finalization checkpoint (protocol 7.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeldOutFinalizationCheckpoint:
    """The immutable record written once, immediately before first held-out run.

    Every field listed in protocol 7.2 is required. The four counts must each
    be exactly 220 or held-out execution does not begin.
    """

    detector_freeze_commit_sha: str
    protocol_commit_sha: str
    detector_version: str
    prompt_version: str
    model_id: str
    total_held_out_cases: int
    ground_truth_recorded_count: int
    content_hash_recorded_count: int
    first_run_null_count: int
    finalized_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.detector_freeze_commit_sha, "detector_freeze_commit_sha"),
            (self.protocol_commit_sha, "protocol_commit_sha"),
            (self.detector_version, "detector_version"),
            (self.prompt_version, "prompt_version"),
            (self.model_id, "model_id"),
        ):
            if not isinstance(value, str) or not value:
                raise TierCCaseError(f"{name} must be a non-empty string")
        for value, name in (
            (self.total_held_out_cases, "total_held_out_cases"),
            (self.ground_truth_recorded_count, "ground_truth_recorded_count"),
            (self.content_hash_recorded_count, "content_hash_recorded_count"),
            (self.first_run_null_count, "first_run_null_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TierCCaseError(f"{name} must be a non-negative integer")
        if (
            not isinstance(self.finalized_at, datetime)
            or self.finalized_at.tzinfo is None
            or self.finalized_at.utcoffset() is None
        ):
            raise TierCCaseError("finalized_at must be a timezone-aware datetime")


CHECKPOINT_REQUIRED_COUNT = HELD_OUT_TOTAL


def validate_held_out_checkpoint(
    cases: tuple[TierCCase, ...] | list[TierCCase],
    checkpoint: HeldOutFinalizationCheckpoint,
    *,
    detector_freeze_at: datetime | None = None,
    retired_case_ids: frozenset[str] = frozenset(),
) -> tuple[ValidationIssue, ...]:
    """Gate the first held-out execution (protocol 7.1 step 10-12, 7.2, 17).

    This is a **complete standalone gate**. It does not assume the caller
    already ran the corpus validator: it runs the full ``HELD_OUT_FINAL``
    validation itself, then adds the checkpoint-specific declared-count checks.
    A caller therefore cannot slip a malformed 220-record set past the gate
    merely by invoking the checkpoint directly, and correctness does not depend
    on undocumented call ordering.

    Delegating this way is safe and non-recursive: ``validate_tier_c_corpus``
    never invokes checkpoint logic, so the dependency runs strictly one way.

    Inherited from ``HELD_OUT_FINAL`` validation: duplicate ``case_id``,
    duplicate ``case_content_sha256``, wrong split, invalid family, incorrect
    family / ground-truth / provenance allocation, unresolved adjudication,
    missing final label or hash, incomplete second review, exact and normalized
    duplicates, non-null ``first_run_at``, and the held-out source-isolation
    audit.

    ``detector_freeze_at`` is optional only so the signature stays additive;
    omitting it makes the gate report ``MISSING_DETECTOR_FREEZE``, because
    protocol 17 requires held-out isolation evidence before execution.

    No partial, pilot, smoke, or calibration held-out run is reachable through
    this function: it either reports that all 220 held-out cases are finalized
    and unexecuted, or it reports issues, and there is no per-case variant.
    """

    if not isinstance(checkpoint, HeldOutFinalizationCheckpoint):
        raise TierCCaseError("checkpoint must be HeldOutFinalizationCheckpoint")
    case_list = list(cases)
    issues: list[ValidationIssue] = []

    def add(code: str, message: str, case_id: str | None = None) -> None:
        issues.append(ValidationIssue(code=code, message=message, case_id=case_id))

    corpus_report = validate_tier_c_corpus(
        case_list,
        ValidationMode.HELD_OUT_FINAL,
        detector_freeze_at=detector_freeze_at,
        retired_case_ids=retired_case_ids,
    )
    issues.extend(corpus_report.issues)

    held_out = [case for case in case_list if case.split is Split.HELD_OUT]
    for case in case_list:
        if case.split is not Split.HELD_OUT:
            add(
                "NON_HELD_OUT_CASE",
                "the held-out checkpoint covers held-out cases only",
                case.case_id,
            )

    executable = [
        case
        for case in held_out
        if case.exclusion is None and case.ground_truth is not None
    ]
    labelled = [case for case in executable if case.label_recorded_at is not None]
    hashed_ids: set[str] = set()
    for case in executable:
        try:
            case_content_sha256(case)
        except TierCCaseError:
            continue
        hashed_ids.add(case.case_id)
    hashed = len(hashed_ids)
    unexecuted = [case for case in held_out if case.first_run_at is None]

    # Protocol 7.2 counts distinct finalized cases, so a duplicated case_id
    # cannot pad a stratum to 220. The corpus validation above already reports
    # the duplicate; counting distinct IDs here makes the declared total
    # disagree as well, so the gate fails on two independent grounds.
    observed = {
        "total_held_out_cases": len({case.case_id for case in executable}),
        "ground_truth_recorded_count": len({case.case_id for case in labelled}),
        "content_hash_recorded_count": hashed,
        "first_run_null_count": len({case.case_id for case in unexecuted}),
    }
    declared = {
        "total_held_out_cases": checkpoint.total_held_out_cases,
        "ground_truth_recorded_count": checkpoint.ground_truth_recorded_count,
        "content_hash_recorded_count": checkpoint.content_hash_recorded_count,
        "first_run_null_count": checkpoint.first_run_null_count,
    }
    for name in sorted(observed):
        if declared[name] != CHECKPOINT_REQUIRED_COUNT:
            add(
                "CHECKPOINT_COUNT_NOT_220",
                f"checkpoint declares {name}={declared[name]}, protocol 7.2 "
                f"requires exactly {CHECKPOINT_REQUIRED_COUNT}",
            )
        if observed[name] != declared[name]:
            add(
                "CHECKPOINT_COUNT_MISMATCH",
                f"checkpoint declares {name}={declared[name]} but the corpus "
                f"shows {observed[name]}",
            )
    if len(held_out) != len(unexecuted):
        add(
            "HELD_OUT_ALREADY_EXECUTED",
            "a held-out case already carries first_run_at; the held-out set is "
            "closed and no further execution may be gated (protocol 7.1)",
        )
    return tuple(issues)


# ---------------------------------------------------------------------------
# Label and content immutability after first execution (protocol 6, 7.1, 17)
# ---------------------------------------------------------------------------


def immutability_violations(before: TierCCase, after: TierCCase) -> tuple[str, ...]:
    """Fields that changed illegally after ``before`` was first executed.

    Two conceptually distinct protections are enforced here:

    *Content immutability* covers everything bound by ``case_content_sha256``.
    A change there is already detectable as a digest change; it is reported
    field by field for a usable message.

    *Audit immutability* covers ``provenance_origin`` fields that are
    deliberately **not** hashed - the authoring and source-selection timestamps.
    They stay out of the digest because they are audit metadata, not benchmark
    content, but they are the mechanical evidence the held-out isolation audit
    reads (protocol 3.1, 7.1). Without this check, an already-executed held-out
    case could have its authoring timestamp rewritten after results were seen,
    retro-fitting the isolation guard while the content digest stayed valid.

    Returns an empty tuple when ``before`` has never run, since pre-execution
    correction is an ordinary, permitted part of authoring.
    """

    if not isinstance(before, TierCCase) or not isinstance(after, TierCCase):
        raise TierCCaseError("both arguments must be TierCCase values")
    if before.case_id != after.case_id:
        raise TierCCaseError("immutability compares two versions of one case")
    if before.first_run_at is None:
        return ()

    changed: list[str] = []
    if encode_provenance_origin_audit(before.provenance_origin) != (
        encode_provenance_origin_audit(after.provenance_origin)
    ):
        changed.append("provenance_origin_audit")
    if before.ground_truth is not after.ground_truth:
        changed.append("ground_truth")
    if before.family_id != after.family_id:
        changed.append("family_id")
    if before.split is not after.split:
        changed.append("split")
    if before.provenance is not after.provenance:
        changed.append("provenance")
    if before.evaluation_inputs != after.evaluation_inputs:
        changed.append("evaluation_inputs")
    if (
        before.evaluation_inputs.semantic_evidence
        != after.evaluation_inputs.semantic_evidence
    ):
        changed.append("semantic_evidence")
    try:
        if case_content_sha256(before) != case_content_sha256(after):
            changed.append("case_content_sha256")
    except TierCCaseError:
        changed.append("case_content_sha256")

    ordered = [field_name for field_name in IMMUTABLE_AFTER_FIRST_RUN if field_name in changed]
    return tuple(ordered)


def assert_immutable_after_first_run(before: TierCCase, after: TierCCase) -> None:
    """Raise if hashed content or the label changed after first execution."""

    changed = immutability_violations(before, after)
    if changed:
        raise TierCCaseError(
            f"case {before.case_id} changed after first execution: "
            f"{', '.join(changed)} (protocol 6, 7.1, 17)"
        )
