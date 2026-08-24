"""Deterministic stratified second-review selection (protocol section 5.1).

The rule is fixed and admits no discretion::

    second_review_rank =
    SHA256(
        UTF8("mandateguard-second-review-v1")
        ||
        UTF8(case_content_sha256)
    )

Within every ``family_id`` x ``ground_truth`` x ``provenance`` stratum, cases
sort ascending by rank and the lowest ``ceil(0.25 * stratum_size)`` are
selected. Every case the primary adjudicator marked ambiguous is second
reviewed *in addition*, and a case in both sets is counted once.

Selection is a pure function of the corpus snapshot it is given. It consults no
randomness, no detector output, no benchmark result, and no manual choice.

Lifecycle ordering
------------------

Protocol 5.1 computes selection only after the primary label and
``case_content_sha256`` exist. Since ``ground_truth`` is itself hashed content,
a disagreement resolution that changes the final label changes the digest, and
under protocol 6 that produces a new case record and a new digest. The rank, and
possibly the stratum, therefore move.

This module resolves that by never caching a selection: the selection is
recomputed from whatever corpus snapshot it is handed, and
``validation`` requires the selection computed over the **final pre-execution
corpus state** to be fully covered before any case executes (protocol 17). That
is the conservative reading. It can only ever *add* a required second review
relative to the earlier snapshot, never drop one, which is exactly what
protocol 5.1 demands when it says the stratified minimum "is a lower bound and
never a target: no required second review is skipped because some total has
already been reached". Reviews already performed against an earlier snapshot
remain valid and are retained.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import re

from mandateguard.benchmark.tier_c.models import (
    GroundTruth,
    Provenance,
    StratumKey,
    TierCCase,
    TierCCaseError,
)


#: The exact domain-separation prefix fixed by protocol 5.1. It is a frozen
#: constant of the benchmark and may not be changed or versioned away.
SECOND_REVIEW_DOMAIN = b"mandateguard-second-review-v1"

#: Protocol 5.1: at least 25% of every stratum, independently.
SECOND_REVIEW_FRACTION_NUMERATOR = 1
SECOND_REVIEW_FRACTION_DENOMINATOR = 4

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def second_review_rank(case_content_sha256: str) -> str:
    """Rank one case by its recorded content digest.

    The digest string is hashed as UTF-8 text, exactly as protocol 5.1 states,
    not as decoded hex bytes. The returned lowercase hex string orders
    identically to the unsigned big-endian 256-bit integer the protocol
    describes, so ascending lexicographic comparison is the registered order.
    """

    if not isinstance(case_content_sha256, str) or not _SHA256_RE.fullmatch(
        case_content_sha256
    ):
        raise TierCCaseError(
            "case_content_sha256 must be a lowercase SHA-256 hex digest"
        )
    return sha256(
        SECOND_REVIEW_DOMAIN + case_content_sha256.encode("utf-8")
    ).hexdigest()


def required_second_review_count(stratum_size: int) -> int:
    """``ceil(0.25 * stratum_size)``, computed in exact integer arithmetic."""

    if isinstance(stratum_size, bool) or not isinstance(stratum_size, int):
        raise TierCCaseError("stratum_size must be an integer")
    if stratum_size < 0:
        raise TierCCaseError("stratum_size must not be negative")
    return math.ceil(
        stratum_size
        * SECOND_REVIEW_FRACTION_NUMERATOR
        / SECOND_REVIEW_FRACTION_DENOMINATOR
    )


@dataclass(frozen=True, slots=True)
class SecondReviewCandidate:
    """The only inputs selection is allowed to see.

    There is deliberately no detector field of any kind: selection cannot be
    influenced by detector behavior, output, or any benchmark result
    (protocol 5.1, "Prohibition").
    """

    case_id: str
    family_id: str
    ground_truth: GroundTruth
    provenance: Provenance
    case_content_sha256: str
    ambiguous: bool

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise TierCCaseError("case_id must be a non-empty string")
        if not isinstance(self.ground_truth, GroundTruth):
            raise TierCCaseError("ground_truth must be a GroundTruth")
        if not isinstance(self.provenance, Provenance):
            raise TierCCaseError("provenance must be a Provenance")
        if not isinstance(self.ambiguous, bool):
            raise TierCCaseError("ambiguous must be a boolean")
        second_review_rank(self.case_content_sha256)

    @property
    def stratum(self) -> StratumKey:
        return (self.family_id, self.ground_truth, self.provenance)

    @property
    def rank(self) -> str:
        return second_review_rank(self.case_content_sha256)


def candidate_from_case(case: TierCCase, case_content_sha256: str) -> SecondReviewCandidate:
    """Build a candidate from an adjudicated, hashed case."""

    ground_truth = case.ground_truth
    if ground_truth is None:
        raise TierCCaseError(
            f"case {case.case_id} has no primary label; second-review selection "
            "runs only after primary adjudication (protocol 5.1)"
        )
    return SecondReviewCandidate(
        case_id=case.case_id,
        family_id=case.family_id,
        ground_truth=ground_truth,
        provenance=case.provenance,
        case_content_sha256=case_content_sha256,
        ambiguous=case.adjudication.marked_ambiguous,
    )


@dataclass(frozen=True, slots=True)
class StratumSelection:
    """The selection outcome for one stratum."""

    family_id: str
    ground_truth: GroundTruth
    provenance: Provenance
    stratum_size: int
    required_count: int
    deterministic_selection: tuple[str, ...]
    ambiguous_additions: tuple[str, ...]

    @property
    def stratum(self) -> StratumKey:
        return (self.family_id, self.ground_truth, self.provenance)

    @property
    def required_second_review(self) -> tuple[str, ...]:
        """Deterministic selection plus ambiguous additions, counted once."""

        combined = set(self.deterministic_selection) | set(self.ambiguous_additions)
        return tuple(sorted(combined))


@dataclass(frozen=True, slots=True)
class SecondReviewSelection:
    """The complete deterministic selection over one corpus snapshot."""

    strata: tuple[StratumSelection, ...]

    @property
    def required_case_ids(self) -> frozenset[str]:
        return frozenset(
            case_id
            for stratum in self.strata
            for case_id in stratum.required_second_review
        )

    @property
    def total_required(self) -> int:
        return len(self.required_case_ids)

    def for_stratum(self, stratum: StratumKey) -> StratumSelection | None:
        for selection in self.strata:
            if selection.stratum == stratum:
                return selection
        return None


def select_second_review(
    candidates: tuple[SecondReviewCandidate, ...] | list[SecondReviewCandidate],
) -> SecondReviewSelection:
    """Compute the registered selection. Pure, deterministic, and total.

    Ties on ``second_review_rank`` are broken by ascending ``case_id``. A tie
    requires a SHA-256 collision, so the tie-break carries no quality claim; it
    exists only so the function is total and reproducible.
    """

    candidate_list = list(candidates)
    if not all(
        isinstance(candidate, SecondReviewCandidate) for candidate in candidate_list
    ):
        raise TierCCaseError("candidates must be SecondReviewCandidate values")
    case_ids = [candidate.case_id for candidate in candidate_list]
    if len(case_ids) != len(set(case_ids)):
        raise TierCCaseError("second-review selection requires unique case IDs")

    grouped: dict[StratumKey, list[SecondReviewCandidate]] = {}
    for candidate in candidate_list:
        grouped.setdefault(candidate.stratum, []).append(candidate)

    strata: list[StratumSelection] = []
    for stratum in sorted(
        grouped, key=lambda key: (key[0], key[1].value, key[2].value)
    ):
        members = sorted(grouped[stratum], key=lambda item: (item.rank, item.case_id))
        required = required_second_review_count(len(members))
        deterministic = tuple(member.case_id for member in members[:required])
        ambiguous = tuple(
            sorted(member.case_id for member in members if member.ambiguous)
        )
        strata.append(
            StratumSelection(
                family_id=stratum[0],
                ground_truth=stratum[1],
                provenance=stratum[2],
                stratum_size=len(members),
                required_count=required,
                deterministic_selection=deterministic,
                ambiguous_additions=ambiguous,
            )
        )
    return SecondReviewSelection(strata=tuple(strata))
