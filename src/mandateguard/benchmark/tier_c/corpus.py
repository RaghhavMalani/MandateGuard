"""Reading and importing Tier C corpus files.

The layout is defined in ``benchmark/tier_c/README.md``. At D8-A no corpus file
exists and none is created: an absent directory is a valid, complete
description of the current state - zero Tier C cases - and :func:`load_corpus`
returns an empty corpus for it rather than failing.

Empty JSONL files are deliberately **not** created as placeholders, because an
empty ``recurrence.jsonl`` is indistinguishable from a finalized corpus that
happens to hold no cases.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from mandateguard.benchmark.tier_c.codec import case_content_sha256, decode_case
from mandateguard.benchmark.tier_c.models import (
    DEV_FAMILIES,
    HELD_OUT_FAMILIES,
    Split,
    TierCCase,
    TierCCaseError,
)


CORPUS_ROOT = Path("benchmark/cases/tier_c")

#: Family to corpus file, relative to :data:`CORPUS_ROOT`.
FAMILY_FILES: dict[str, str] = {
    "C-DEV-RECURRENCE": "dev/recurrence.jsonl",
    "C-DEV-EXCLUSION": "dev/exclusion.jsonl",
    "C-DEV-PURPOSE": "dev/purpose.jsonl",
    "C-HOLD-BUNDLE": "held_out/bundle.jsonl",
    "C-HOLD-COMPATIBILITY": "held_out/compatibility.jsonl",
    "C-HOLD-FULFILLMENT": "held_out/fulfillment.jsonl",
}

SPLIT_FAMILIES: dict[Split, tuple[str, ...]] = {
    Split.DEV: DEV_FAMILIES,
    Split.HELD_OUT: HELD_OUT_FAMILIES,
}


@dataclass(frozen=True, slots=True)
class TierCCorpus:
    """Every Tier C case currently committed for one split, plus its digests."""

    split: Split
    cases: tuple[TierCCase, ...]
    content_hashes: dict[str, str]

    @property
    def is_empty(self) -> bool:
        return not self.cases

    @property
    def case_ids(self) -> frozenset[str]:
        return frozenset(case.case_id for case in self.cases)


def _decode_line(line: str, source: Path, number: int) -> TierCCase:
    try:
        record = json.loads(line)
    except ValueError as error:
        raise TierCCaseError(f"{source}:{number} is not valid JSON") from error
    if not isinstance(record, dict):
        raise TierCCaseError(f"{source}:{number} must be a JSON object")
    return decode_case(record)


def load_family(root: Path, family_id: str) -> tuple[TierCCase, ...]:
    """Load one family file. A missing file means zero authored cases."""

    if family_id not in FAMILY_FILES:
        raise TierCCaseError(f"{family_id!r} is not a Tier C family")
    path = root / FAMILY_FILES[family_id]
    if not path.exists():
        return ()
    cases: list[TierCCase] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        case = _decode_line(line, path, number)
        if case.family_id != family_id:
            raise TierCCaseError(
                f"{path}:{number} holds {case.family_id}, expected {family_id}"
            )
        cases.append(case)
    return tuple(cases)


def load_corpus(root: Path, split: Split) -> TierCCorpus:
    """Load every committed case for one split."""

    if not isinstance(split, Split) or split not in SPLIT_FAMILIES:
        raise TierCCaseError("split must be dev or held_out")
    cases: list[TierCCase] = []
    for family_id in SPLIT_FAMILIES[split]:
        cases.extend(load_family(root, family_id))
    ordered = tuple(sorted(cases, key=lambda case: case.case_id))
    hashes = {
        case.case_id: case_content_sha256(case)
        for case in ordered
        if case.ground_truth is not None
    }
    return TierCCorpus(split=split, cases=ordered, content_hashes=hashes)


def import_case(
    record: dict, existing: TierCCorpus
) -> tuple[TierCCase, str]:
    """Validate one proposed case against a corpus and return it with its digest.

    This is the whole authoring import path, and it is data validation only. It
    never calls a detector, never calls a model, and never assigns a ground
    truth: a record whose adjudication block carries no human label is rejected,
    not labelled.
    """

    if not isinstance(existing, TierCCorpus):
        raise TierCCaseError("existing must be a TierCCorpus")
    case = decode_case(record)
    if case.ground_truth is None:
        raise TierCCaseError(
            f"case {case.case_id} carries no human ground truth; a case must be "
            "adjudicated by a person before import (protocol 5)"
        )
    if case.first_run_at is not None:
        raise TierCCaseError(
            f"case {case.case_id} declares first_run_at; an imported case has "
            "never been executed"
        )
    if case.case_id in existing.case_ids:
        raise TierCCaseError(f"case_id {case.case_id} is already used in this corpus")
    digest = case_content_sha256(case)
    for existing_id, existing_digest in sorted(existing.content_hashes.items()):
        if existing_digest == digest:
            raise TierCCaseError(
                f"case {case.case_id} duplicates the content of {existing_id} "
                f"(digest {digest}); exact duplicates are forbidden (protocol 4.1)"
            )
    return case, digest
