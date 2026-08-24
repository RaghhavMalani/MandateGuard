"""Duplicate and near-duplicate review tooling (protocol section 4.1).

Protocol 4.1 requires, before the first detector execution on a split, a
documented combination of:

1. exact canonical-content duplicate detection over canonical case content and
   ``case_content_sha256``;
2. normalized-text comparison under a documented normalization and similarity
   criterion; and
3. human or manual near-duplicate review within each stratum.

V1 explicitly does not require an embedding-based system, and this module
implements none. There is no model call, no embedding, and no learned
similarity anywhere in it.

What this module does *not* do is decide that two cases are semantic
duplicates. Layer 3 is human. The similarity criterion here only produces
**candidate pairs for a person to look at**; a candidate is not a finding, and
nothing here removes or rewrites a case.

Normalization criterion (documented, deterministic, and fixed)
--------------------------------------------------------------

``normalize_text`` applies, in order:

1. Unicode NFKC normalization;
2. casefolding;
3. replacement of every character that is not a Unicode letter or digit with a
   single space; and
4. whitespace collapsing and stripping.

Similarity criterion: Jaccard overlap of the normalized token *sets*. A pair is
a manual-review candidate when it lies in the same
``family_id`` x ``ground_truth`` stratum and scores at or above
:data:`MANUAL_REVIEW_JACCARD_THRESHOLD`. The score is computed in exact integer
arithmetic and compared as a rational, so no float enters the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from mandateguard.benchmark.tier_c.models import (
    GroundTruth,
    TierCCase,
    TierCCaseError,
)


#: Candidate threshold, as an exact rational 9/10. Pairs scoring at or above
#: this are shown to a human reviewer; they are never auto-declared duplicates.
MANUAL_REVIEW_JACCARD_THRESHOLD = (9, 10)


def normalize_text(text: str) -> str:
    """Deterministic normalization for near-duplicate comparison."""

    if not isinstance(text, str):
        raise TierCCaseError("text must be a string")
    folded = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(
        character if character.isalnum() else " " for character in folded
    )
    return " ".join(cleaned.split())


def case_comparison_text(case: TierCCase) -> str:
    """The authored natural-language surface of one case.

    Deliberately scoped to the text a human actually authors and adjudicates -
    the mandate semantic constraints and the trusted semantic evidence - rather
    than the whole record, so that structural boilerplate shared by every case
    does not inflate every similarity score toward 1.
    """

    constraints = case.evaluation_inputs.mandate.payload.constraints.semantic
    parts = [constraint.text for constraint in sorted(
        constraints, key=lambda item: item.constraint_id
    )]
    parts.extend(entry.text for entry in case.evaluation_inputs.semantic_evidence.entries)
    return " ".join(parts)


def normalized_fingerprint(case: TierCCase) -> str:
    return normalize_text(case_comparison_text(case))


def _tokens(case: TierCCase) -> frozenset[str]:
    return frozenset(normalized_fingerprint(case).split())


def jaccard_ratio(left: frozenset[str], right: frozenset[str]) -> tuple[int, int]:
    """Exact ``(intersection, union)`` token overlap. No float is produced."""

    union = left | right
    if not union:
        return (0, 0)
    return (len(left & right), len(union))


def _meets_threshold(ratio: tuple[int, int]) -> bool:
    intersection, union = ratio
    if union == 0:
        return False
    numerator, denominator = MANUAL_REVIEW_JACCARD_THRESHOLD
    return intersection * denominator >= numerator * union


@dataclass(frozen=True, slots=True)
class DuplicatePair:
    """Two cases a human must compare. Not a finding."""

    left_case_id: str
    right_case_id: str
    family_id: str
    ground_truth: GroundTruth
    shared_tokens: int
    union_tokens: int


@dataclass(frozen=True, slots=True)
class DuplicateReport:
    """The recorded outcome of the protocol 4.1 review for one corpus."""

    exact_duplicate_groups: tuple[tuple[str, ...], ...]
    normalized_text_duplicate_groups: tuple[tuple[str, ...], ...]
    manual_review_candidates: tuple[DuplicatePair, ...]

    @property
    def has_exact_duplicates(self) -> bool:
        return bool(self.exact_duplicate_groups)

    @property
    def has_normalized_duplicates(self) -> bool:
        return bool(self.normalized_text_duplicate_groups)


def _grouped_by(
    keyed: list[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    grouped: dict[str, list[str]] = {}
    for key, case_id in keyed:
        grouped.setdefault(key, []).append(case_id)
    return tuple(
        tuple(sorted(case_ids))
        for _, case_ids in sorted(grouped.items())
        if len(case_ids) > 1
    )


def review_duplicates(
    cases: tuple[TierCCase, ...] | list[TierCCase],
    content_hashes: dict[str, str],
) -> DuplicateReport:
    """Run layers 1 and 2, and produce layer 3's candidate list.

    ``content_hashes`` maps ``case_id`` to that case's recorded
    ``case_content_sha256``.
    """

    case_list = list(cases)
    for case in case_list:
        if case.case_id not in content_hashes:
            raise TierCCaseError(
                f"case {case.case_id} has no recorded content digest to compare"
            )

    exact = _grouped_by([(content_hashes[case.case_id], case.case_id) for case in case_list])
    normalized = _grouped_by(
        [(normalized_fingerprint(case), case.case_id) for case in case_list]
    )

    already_grouped: set[frozenset[str]] = set()
    for group in exact + normalized:
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                already_grouped.add(frozenset({left, right}))

    token_sets = {case.case_id: _tokens(case) for case in case_list}
    candidates: list[DuplicatePair] = []
    for index, left in enumerate(case_list):
        for right in case_list[index + 1 :]:
            if left.ground_truth is None or right.ground_truth is None:
                continue
            if left.family_id != right.family_id:
                continue
            if left.ground_truth is not right.ground_truth:
                continue
            if frozenset({left.case_id, right.case_id}) in already_grouped:
                continue
            ratio = jaccard_ratio(token_sets[left.case_id], token_sets[right.case_id])
            if not _meets_threshold(ratio):
                continue
            pair = sorted((left.case_id, right.case_id))
            candidates.append(
                DuplicatePair(
                    left_case_id=pair[0],
                    right_case_id=pair[1],
                    family_id=left.family_id,
                    ground_truth=left.ground_truth,
                    shared_tokens=ratio[0],
                    union_tokens=ratio[1],
                )
            )
    candidates.sort(key=lambda item: (item.left_case_id, item.right_case_id))
    return DuplicateReport(
        exact_duplicate_groups=exact,
        normalized_text_duplicate_groups=normalized,
        manual_review_candidates=tuple(candidates),
    )
