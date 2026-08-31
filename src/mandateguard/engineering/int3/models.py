"""Typed values for the INT-3 evidence-sufficiency engineering experiment.

INT-3 asks one narrow engineering question: given a subset of the trusted
evidence a case is eligible to use, does that subset preserve the authorization
action the frozen full-evidence path already produced?  Nothing here claims
human-intent correctness, and nothing here can change a MandateGuard decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
import re


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SEMANTIC_BEHAVIORS = ("PASS", "VIOLATION", "ABSTAIN")
REFERENCE_ACTIONS = ("ALLOW", "BLOCK", "REVIEW")


class Int3ExperimentError(ValueError):
    """An INT-3 evidence-sufficiency input is invalid."""


class CaseFamily(str, Enum):
    """Constraint composition of one frozen case.

    The family is derived only from the mandate's declared semantic constraint
    kinds.  It never encodes the merchant, the query identity, the engineering
    expectation, or any observed verdict.
    """

    PURPOSE_AND_EXCLUSION = "PURPOSE_AND_EXCLUSION"
    EXCLUSION_ONLY = "EXCLUSION_ONLY"
    PURPOSE_ONLY = "PURPOSE_ONLY"
    OTHER = "OTHER"


def case_family_for_constraint_kinds(kinds: tuple[str, ...]) -> CaseFamily:
    """Map declared semantic constraint kinds onto the frozen case family."""

    if not isinstance(kinds, tuple) or not kinds:
        raise Int3ExperimentError("constraint kinds must be a non-empty tuple")
    if not all(isinstance(item, str) and item for item in kinds):
        raise Int3ExperimentError("constraint kinds must be non-empty strings")
    distinct = frozenset(kinds)
    if distinct == frozenset({"purpose", "exclusion"}):
        return CaseFamily.PURPOSE_AND_EXCLUSION
    if distinct == frozenset({"exclusion"}):
        return CaseFamily.EXCLUSION_ONLY
    if distinct == frozenset({"purpose"}):
        return CaseFamily.PURPOSE_ONLY
    return CaseFamily.OTHER


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise Int3ExperimentError(f"{name} must be a bounded identifier")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise Int3ExperimentError(f"{name} must be a SHA-256 hex digest")
    return value


def _unique_identifiers(
    value: object, name: str, *, nonempty: bool
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise Int3ExperimentError(f"{name} must be a tuple")
    if nonempty and not value:
        raise Int3ExperimentError(f"{name} must be non-empty")
    for item in value:
        _identifier(item, name)
    if len(value) != len(set(value)):
        raise Int3ExperimentError(f"{name} must contain unique values")
    return value


def _semantic_behavior(value: object, name: str) -> str:
    if value not in SEMANTIC_BEHAVIORS:
        raise Int3ExperimentError(f"{name} must be one of {SEMANTIC_BEHAVIORS}")
    return str(value)


def _reference_action(value: object, name: str) -> str:
    if value not in REFERENCE_ACTIONS:
        raise Int3ExperimentError(f"{name} must be one of {REFERENCE_ACTIONS}")
    return str(value)


def probability(value: object, name: str) -> float:
    """Validate a finite probability in the closed unit interval."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise Int3ExperimentError(f"{name} must be a finite number within [0, 1]")
    return float(value)


def positive_number(value: object, name: str) -> float:
    """Validate a finite strictly positive engineering cost or weight."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise Int3ExperimentError(f"{name} must be a finite positive number")
    return float(value)


def nonnegative_number(value: object, name: str) -> float:
    """Validate a finite non-negative engineering cost or weight."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise Int3ExperimentError(f"{name} must be a finite non-negative number")
    return float(value)


@dataclass(frozen=True, slots=True)
class FullEvidenceReference:
    """The frozen full-evidence decision one query's subsets are measured against.

    These values are read from the already-recorded INT-2 Stage-B production
    condition.  INT-3 never re-executes the reference and never edits it.
    """

    query_id: str
    source_run_id: str
    source_observation_id: str
    model_id: str
    prompt_version: str
    detector_version: str
    full_reference_semantic_behavior: str
    full_reference_action: str
    full_reference_semantic_input_sha256: str
    full_evidence_ids: tuple[str, ...]
    sku_scoped_selected_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.query_id, "query_id")
        _identifier(self.source_run_id, "source_run_id")
        _identifier(self.source_observation_id, "source_observation_id")
        for value, name in (
            (self.model_id, "model_id"),
            (self.prompt_version, "prompt_version"),
            (self.detector_version, "detector_version"),
        ):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise Int3ExperimentError(
                    f"{name} must be a bounded non-empty string"
                )
        _semantic_behavior(
            self.full_reference_semantic_behavior,
            "full_reference_semantic_behavior",
        )
        _reference_action(self.full_reference_action, "full_reference_action")
        _digest(
            self.full_reference_semantic_input_sha256,
            "full_reference_semantic_input_sha256",
        )
        _unique_identifiers(
            self.full_evidence_ids, "full_evidence_ids", nonempty=True
        )
        _unique_identifiers(
            self.sku_scoped_selected_evidence_ids,
            "sku_scoped_selected_evidence_ids",
            nonempty=True,
        )
        if not set(self.sku_scoped_selected_evidence_ids).issubset(
            self.full_evidence_ids
        ):
            raise Int3ExperimentError(
                "SKU-scoped selection must be drawn from the full evidence set"
            )


@dataclass(frozen=True, slots=True)
class SubsetObservation:
    """One planned, not-yet-executed evidence subset.

    ``observed_*`` and ``decision_stable`` are null in this milestone by
    construction: no semantic provider has been called for any subset.
    """

    observation_id: str
    query_id: str
    eligible_evidence_ids: tuple[str, ...]
    subset_evidence_ids: tuple[str, ...]
    subset_size: int
    eligible_size: int
    subset_mask: str
    case_family: CaseFamily
    full_reference_semantic_behavior: str
    full_reference_action: str
    full_reference_semantic_input_sha256: str
    subset_semantic_input_sha256: str
    sku_scoped_selected_evidence_ids: tuple[str, ...]
    matches_full_reference_semantic_input: bool
    is_full_evidence_subset: bool
    canonical_observation_id: str
    planned_semantic_call: bool
    semantic_status: str = "PLANNED"
    observed_semantic_behavior: None = None
    observed_final_action: None = None
    decision_stable: None = None

    def __post_init__(self) -> None:
        _identifier(self.observation_id, "observation_id")
        _identifier(self.query_id, "query_id")
        _identifier(self.canonical_observation_id, "canonical_observation_id")
        eligible = _unique_identifiers(
            self.eligible_evidence_ids, "eligible_evidence_ids", nonempty=True
        )
        subset = _unique_identifiers(
            self.subset_evidence_ids, "subset_evidence_ids", nonempty=True
        )
        selected = set(subset)
        if not selected.issubset(eligible):
            raise Int3ExperimentError("subset evidence must be eligible evidence")
        if subset != tuple(item for item in eligible if item in selected):
            raise Int3ExperimentError(
                "subset evidence must preserve the frozen eligible order"
            )
        if self.subset_size != len(subset) or self.eligible_size != len(eligible):
            raise Int3ExperimentError(
                "subset/eligible sizes must match their tuples"
            )
        if not 1 <= self.subset_size <= self.eligible_size:
            raise Int3ExperimentError(
                "subset size must be within [1, eligible_size]"
            )
        if (
            not isinstance(self.subset_mask, str)
            or len(self.subset_mask) != self.eligible_size
            or set(self.subset_mask) - {"0", "1"}
        ):
            raise Int3ExperimentError(
                "subset_mask must be an eligible-length bitmask"
            )
        expected_mask = "".join(
            "1" if item in selected else "0" for item in eligible
        )
        if self.subset_mask != expected_mask:
            raise Int3ExperimentError(
                "subset_mask does not describe subset_evidence_ids"
            )
        if not isinstance(self.case_family, CaseFamily):
            raise Int3ExperimentError("case_family must be a CaseFamily")
        _semantic_behavior(
            self.full_reference_semantic_behavior,
            "full_reference_semantic_behavior",
        )
        _reference_action(self.full_reference_action, "full_reference_action")
        _digest(
            self.full_reference_semantic_input_sha256,
            "full_reference_semantic_input_sha256",
        )
        _digest(
            self.subset_semantic_input_sha256, "subset_semantic_input_sha256"
        )
        _unique_identifiers(
            self.sku_scoped_selected_evidence_ids,
            "sku_scoped_selected_evidence_ids",
            nonempty=False,
        )
        if not set(self.sku_scoped_selected_evidence_ids).issubset(selected):
            raise Int3ExperimentError(
                "SKU-scoped selection must be drawn from the subset"
            )
        for value, name in (
            (
                self.matches_full_reference_semantic_input,
                "matches_full_reference_semantic_input",
            ),
            (self.is_full_evidence_subset, "is_full_evidence_subset"),
            (self.planned_semantic_call, "planned_semantic_call"),
        ):
            if not isinstance(value, bool):
                raise Int3ExperimentError(f"{name} must be boolean")
        if self.is_full_evidence_subset != (self.subset_size == self.eligible_size):
            raise Int3ExperimentError(
                "is_full_evidence_subset must match subset/eligible cardinality"
            )
        expected_match = (
            self.subset_semantic_input_sha256
            == self.full_reference_semantic_input_sha256
        )
        if self.matches_full_reference_semantic_input != expected_match:
            raise Int3ExperimentError(
                "matches_full_reference_semantic_input must follow the input hashes"
            )
        if self.semantic_status != "PLANNED":
            raise Int3ExperimentError("semantic_status must be PLANNED in INT-3A")
        if (
            self.observed_semantic_behavior is not None
            or self.observed_final_action is not None
            or self.decision_stable is not None
        ):
            raise Int3ExperimentError(
                "INT-3A observations must carry null observed results and labels"
            )


@dataclass(frozen=True, slots=True)
class SubsetEquivalenceClass:
    """Subsets that build the byte-identical semantic input."""

    semantic_input_sha256: str
    canonical_observation_id: str
    member_observation_ids: tuple[str, ...]
    matches_full_reference_semantic_input: bool

    def __post_init__(self) -> None:
        _digest(self.semantic_input_sha256, "semantic_input_sha256")
        _identifier(self.canonical_observation_id, "canonical_observation_id")
        _unique_identifiers(
            self.member_observation_ids, "member_observation_ids", nonempty=True
        )
        if self.canonical_observation_id != self.member_observation_ids[0]:
            raise Int3ExperimentError(
                "the canonical observation must be the first class member"
            )
        if not isinstance(self.matches_full_reference_semantic_input, bool):
            raise Int3ExperimentError(
                "matches_full_reference_semantic_input must be boolean"
            )


@dataclass(frozen=True, slots=True)
class SubsetPlan:
    """The complete, network-free INT-3A subset enumeration."""

    schema_version: str
    created_at: datetime
    model_id: str
    prompt_version: str
    detector_version: str
    references: tuple[FullEvidenceReference, ...]
    observations: tuple[SubsetObservation, ...]
    equivalence_classes: tuple[SubsetEquivalenceClass, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise Int3ExperimentError("schema_version must be 1.0")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise Int3ExperimentError("created_at must be timezone-aware")
        if not isinstance(self.references, tuple) or not self.references:
            raise Int3ExperimentError("references must be non-empty")
        if not isinstance(self.observations, tuple) or not self.observations:
            raise Int3ExperimentError("observations must be non-empty")
        if not isinstance(self.equivalence_classes, tuple) or not self.equivalence_classes:
            raise Int3ExperimentError("equivalence_classes must be non-empty")
        observation_ids = [item.observation_id for item in self.observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise Int3ExperimentError("observation IDs must be unique")
        subset_keys = [
            (item.query_id, item.subset_mask) for item in self.observations
        ]
        if len(subset_keys) != len(set(subset_keys)):
            raise Int3ExperimentError(
                "each query/subset pair must appear exactly once"
            )
        reference_ids = [item.query_id for item in self.references]
        if len(reference_ids) != len(set(reference_ids)):
            raise Int3ExperimentError("reference query IDs must be unique")
        if {item.query_id for item in self.observations} != set(reference_ids):
            raise Int3ExperimentError(
                "observations must cover exactly the referenced queries"
            )
        members = [
            member
            for item in self.equivalence_classes
            for member in item.member_observation_ids
        ]
        if len(members) != len(set(members)) or set(members) != set(observation_ids):
            raise Int3ExperimentError(
                "equivalence classes must partition every observation exactly once"
            )
        planned = {
            item.observation_id
            for item in self.observations
            if item.planned_semantic_call
        }
        canonical = {
            item.canonical_observation_id for item in self.equivalence_classes
        }
        if planned != canonical:
            raise Int3ExperimentError(
                "exactly one canonical observation per class may plan a semantic call"
            )

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def unique_semantic_input_count(self) -> int:
        return len(self.equivalence_classes)

    @property
    def predicted_semantic_api_calls(self) -> int:
        return len(self.equivalence_classes)

    @property
    def query_ids(self) -> tuple[str, ...]:
        return tuple(item.query_id for item in self.references)

    def observations_for_query(
        self, query_id: str
    ) -> tuple[SubsetObservation, ...]:
        return tuple(item for item in self.observations if item.query_id == query_id)

    def reference_for_query(self, query_id: str) -> FullEvidenceReference:
        for reference in self.references:
            if reference.query_id == query_id:
                return reference
        raise Int3ExperimentError(f"no full-evidence reference for {query_id!r}")


def subset_counts_by_query(plan: SubsetPlan) -> dict[str, int]:
    """Return the per-query subset observation count in frozen query order."""

    if not isinstance(plan, SubsetPlan):
        raise TypeError("plan must be SubsetPlan")
    return {
        query_id: len(plan.observations_for_query(query_id))
        for query_id in plan.query_ids
    }
