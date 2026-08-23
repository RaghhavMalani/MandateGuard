"""Deterministic generation of the registered 1,008-case Tier A/B corpus.

Direction of dependency, enforced by construction::

    registered mutation recipe
        -> mechanical ground-truth label
        -> case content
        -> case content hash
        -> (later, after hostile review) detector execution

No policy, semantic, execution, or replay module is imported anywhere in the
``mandateguard.benchmark`` package, so generation cannot consult a detector
result even accidentally. ``first_run_at`` is ``null`` for every generated
case: the registered corpus has not been executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from mandateguard.benchmark.codec import (
    case_content_sha256,
    case_record_line,
    decode_case,
    encode_timestamp,
)
from mandateguard.benchmark.manifest import (
    ManifestPreambleError,
    frozen_preamble,
    manifest_record,
    render_manifest,
)
from mandateguard.benchmark.models import (
    BENCHMARK_FAMILIES,
    CASE_SCHEMA_VERSION,
    GENERATOR_VERSION,
    TIER_A_FAMILIES,
    TIER_B_FAMILIES,
    BenchmarkCase,
    GeneratorAudit,
    TargetExpectation,
)
from mandateguard.benchmark.recipes import build_inputs, build_recipe, generator_seed


CORPUS_SUBDIRECTORY = Path("benchmark") / "cases" / "tier_ab"
MANIFEST_PATH = Path("benchmark") / "MANIFEST.yaml"
SUMMARY_PATH = Path("benchmark") / "generated" / "TIER_AB_GENERATION_SUMMARY.json"

TIER_A_CLASS_PLAN = (
    ("V", 24, "violation", "BLOCK", "FAIL"),
    ("P", 24, "benign", "ALLOW", "PASS"),
    ("NE", 8, "benign", "REVIEW", "NOT_EVALUABLE"),
)
TIER_B_CLASS_PLAN = (
    ("V", 28, "violation", "BLOCK", "FAIL"),
    ("P", 28, "benign", "ALLOW", "PASS"),
)

EXPECTED_TOTAL = 1_008
EXPECTED_TIER_A_TOTAL = 448
EXPECTED_TIER_B_TOTAL = 560
EXPECTED_PER_FAMILY = 56

PROVENANCE = "developer_authored"
SPLIT = "dev"
LABEL_SOURCE = "deterministic_invariant"


class GenerationError(RuntimeError):
    """Raised when the corpus would not satisfy the registered inventory."""


def class_plan(family_id: str) -> tuple[tuple[str, int, str, str, str], ...]:
    return TIER_A_CLASS_PLAN if family_id in TIER_A_FAMILIES else TIER_B_CLASS_PLAN


def inventory_slots() -> tuple[tuple[str, str, int, str, str, str], ...]:
    """The registered inventory in manifest order.

    Manifest order is: Tier A before Tier B, then family numeric order
    (``A1``-``A8``, ``B1``-``B10``), then case-class order ``V``, ``P``, ``NE``,
    then ascending numeric index. The JSONL corpus uses a second, separately
    documented order: ascending ``case_id`` within each family file.
    """

    slots: list[tuple[str, str, int, str, str, str]] = []
    for family_id in BENCHMARK_FAMILIES:
        for case_class, count, ground_truth, action, status in class_plan(family_id):
            for index in range(count):
                slots.append(
                    (family_id, case_class, index, ground_truth, action, status)
                )
    return tuple(slots)


def case_identifier(family_id: str, case_class: str, index: int) -> str:
    return f"{family_id}-{case_class}-{index + 1:03d}"


def build_case(
    *,
    family_id: str,
    case_class: str,
    index: int,
    ground_truth: str,
    expected_action: str,
    target_status: str,
    label_recorded_at: datetime,
) -> BenchmarkCase:
    recipe = build_recipe(family_id, case_class, index)
    return BenchmarkCase(
        case_id=case_identifier(family_id, case_class, index),
        case_schema_version=CASE_SCHEMA_VERSION,
        evidence_tier=family_id[0],
        family_id=family_id,
        provenance=PROVENANCE,
        split=SPLIT,
        ground_truth=ground_truth,
        label_source=LABEL_SOURCE,
        expected_action=expected_action,
        target_expectation=TargetExpectation(
            family_id=family_id, status=target_status
        ),
        evaluation_inputs=build_inputs(recipe.scenario),
        label_recorded_at=label_recorded_at,
        generator=GeneratorAudit(
            generator_version=GENERATOR_VERSION,
            generator_seed=generator_seed(family_id, case_class, index),
            recipe_id=recipe.recipe_id,
            recipe_parameters=dict(recipe.parameters),
        ),
        first_run_at=None,
    )


def generate_cases(label_recorded_at: datetime) -> tuple[BenchmarkCase, ...]:
    """Materialize all 1,008 registered cases in manifest order."""

    if (
        not isinstance(label_recorded_at, datetime)
        or label_recorded_at.tzinfo is None
        or label_recorded_at.utcoffset() is None
    ):
        raise GenerationError(
            "label_recorded_at must be an explicit timezone-aware UTC timestamp"
        )
    cases = tuple(
        build_case(
            family_id=family_id,
            case_class=case_class,
            index=index,
            ground_truth=ground_truth,
            expected_action=action,
            target_status=status,
            label_recorded_at=label_recorded_at,
        )
        for family_id, case_class, index, ground_truth, action, status in (
            inventory_slots()
        )
    )
    validate_cases(cases)
    return cases


def validate_cases(cases: Iterable[BenchmarkCase]) -> None:
    """Fail loudly rather than commit a corpus that misses the inventory."""

    cases = tuple(cases)
    if len(cases) != EXPECTED_TOTAL:
        raise GenerationError(f"expected {EXPECTED_TOTAL} cases, built {len(cases)}")

    per_family: dict[str, int] = {}
    per_family_class: dict[tuple[str, str], int] = {}
    identifiers: set[str] = set()
    digests: dict[str, str] = {}
    for case in cases:
        case_class = case.case_id.split("-")[1]
        per_family[case.family_id] = per_family.get(case.family_id, 0) + 1
        key = (case.family_id, case_class)
        per_family_class[key] = per_family_class.get(key, 0) + 1
        if case.case_id in identifiers:
            raise GenerationError(f"duplicate case_id {case.case_id}")
        identifiers.add(case.case_id)
        if case.first_run_at is not None:
            raise GenerationError(f"case {case.case_id} must keep first_run_at null")
        if case.provenance != PROVENANCE or case.split != SPLIT:
            raise GenerationError(f"case {case.case_id} carries unregistered metadata")
        if case.label_source != LABEL_SOURCE:
            raise GenerationError(f"case {case.case_id} label_source is not registered")
        if case.evaluation_inputs.mandate.payload.constraints.semantic != ():
            raise GenerationError(
                f"case {case.case_id} carries a semantic constraint; Tier C is out "
                "of scope for D7"
            )
        digest = case_content_sha256(case)
        if digest in digests:
            raise GenerationError(
                "duplicate case content digest shared by "
                f"{digests[digest]} and {case.case_id}"
            )
        digests[digest] = case.case_id

    for family_id in BENCHMARK_FAMILIES:
        if per_family.get(family_id) != EXPECTED_PER_FAMILY:
            raise GenerationError(
                f"family {family_id} has {per_family.get(family_id)} cases, "
                f"expected {EXPECTED_PER_FAMILY}"
            )
        for case_class, count, _, _, _ in class_plan(family_id):
            actual = per_family_class.get((family_id, case_class))
            if actual != count:
                raise GenerationError(
                    f"family {family_id} class {case_class} has {actual} cases, "
                    f"expected {count}"
                )

    tier_a = sum(1 for case in cases if case.evidence_tier == "A")
    tier_b = sum(1 for case in cases if case.evidence_tier == "B")
    if tier_a != EXPECTED_TIER_A_TOTAL or tier_b != EXPECTED_TIER_B_TOTAL:
        raise GenerationError(
            f"tier totals drifted: Tier A {tier_a}, Tier B {tier_b}"
        )


def family_corpus_text(cases: Iterable[BenchmarkCase], family_id: str) -> str:
    """One family JSONL file: canonical records sorted by ``case_id``."""

    selected = sorted(
        (case for case in cases if case.family_id == family_id),
        key=lambda case: case.case_id,
    )
    if len(selected) != EXPECTED_PER_FAMILY:
        raise GenerationError(
            f"family {family_id} corpus has {len(selected)} records"
        )
    return "".join(f"{case_record_line(case)}\n" for case in selected)


def corpus_files(cases: Iterable[BenchmarkCase]) -> dict[str, str]:
    cases = tuple(cases)
    return {
        f"{family_id}.jsonl": family_corpus_text(cases, family_id)
        for family_id in BENCHMARK_FAMILIES
    }


def manifest_text(cases: Iterable[BenchmarkCase], preamble: str) -> str:
    records = [manifest_record(case, case_content_sha256(case)) for case in cases]
    return render_manifest(preamble, records)


@dataclass(frozen=True, slots=True)
class GeneratedCorpus:
    """The complete in-memory result of one generation run."""

    cases: tuple[BenchmarkCase, ...]
    corpus_files: dict[str, str]
    manifest_text: str
    summary: dict[str, Any]


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def build_summary(
    cases: tuple[BenchmarkCase, ...],
    files: dict[str, str],
    label_recorded_at: datetime,
) -> dict[str, Any]:
    """Audit metadata about generation. No detector metric belongs here."""

    class_counts: dict[str, int] = {}
    per_family_counts: dict[str, dict[str, int]] = {}
    for case in cases:
        case_class = case.case_id.split("-")[1]
        class_counts[case_class] = class_counts.get(case_class, 0) + 1
        family = per_family_counts.setdefault(case.family_id, {})
        family[case_class] = family.get(case_class, 0) + 1
        family["total"] = family.get("total", 0) + 1
    return {
        "generator_version": GENERATOR_VERSION,
        "case_schema_version": CASE_SCHEMA_VERSION,
        "label_recorded_at": encode_timestamp(label_recorded_at),
        "total_cases": len(cases),
        "tier_a_total": sum(1 for case in cases if case.evidence_tier == "A"),
        "tier_b_total": sum(1 for case in cases if case.evidence_tier == "B"),
        "per_family_counts": {
            family_id: per_family_counts[family_id]
            for family_id in BENCHMARK_FAMILIES
        },
        "class_counts": {
            key: class_counts[key] for key in sorted(class_counts)
        },
        "ground_truth_counts": {
            "violation": sum(1 for case in cases if case.ground_truth == "violation"),
            "benign": sum(1 for case in cases if case.ground_truth == "benign"),
        },
        "expected_action_counts": {
            action: sum(1 for case in cases if case.expected_action == action)
            for action in ("ALLOW", "REVIEW", "BLOCK")
        },
        "unique_case_ids": len({case.case_id for case in cases}),
        "unique_content_hashes": len({case_content_sha256(case) for case in cases}),
        "first_run_null_count": sum(1 for case in cases if case.first_run_at is None),
        "semantic_constraint_case_count": sum(
            1
            for case in cases
            if case.evaluation_inputs.mandate.payload.constraints.semantic
        ),
        "tier_c_case_count": 0,
        "corpus_file_sha256": {
            name: _sha256_text(files[name]) for name in sorted(files)
        },
        "registered_corpus_executed": False,
    }


def build_corpus(label_recorded_at: datetime, manifest_path: Path) -> GeneratedCorpus:
    """Generate, validate, and round-trip the whole corpus before any write."""

    preamble = frozen_preamble(manifest_path)
    cases = generate_cases(label_recorded_at)
    files = corpus_files(cases)
    _verify_round_trip(files, cases)
    return GeneratedCorpus(
        cases=cases,
        corpus_files=files,
        manifest_text=manifest_text(cases, preamble),
        summary=build_summary(cases, files, label_recorded_at),
    )


def _verify_round_trip(
    files: dict[str, str], cases: tuple[BenchmarkCase, ...]
) -> None:
    reparsed: dict[str, str] = {}
    for name, text in files.items():
        for line in text.splitlines():
            record = json.loads(line)
            case = decode_case(record)
            reparsed[case.case_id] = case_record_line(case)
            if reparsed[case.case_id] != line:
                raise GenerationError(
                    f"case {case.case_id} does not round-trip to identical bytes"
                )
    if len(reparsed) != len(cases):
        raise GenerationError("corpus round-trip lost or duplicated a case")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(text)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def write_corpus(root: Path, label_recorded_at: datetime) -> GeneratedCorpus:
    """Validate everything in memory first, then replace files atomically."""

    root = Path(root)
    manifest_path = root / MANIFEST_PATH
    corpus = build_corpus(label_recorded_at, manifest_path)
    corpus_directory = root / CORPUS_SUBDIRECTORY
    for name, text in sorted(corpus.corpus_files.items()):
        _atomic_write_text(corpus_directory / name, text)
    _atomic_write_text(manifest_path, corpus.manifest_text)
    _atomic_write_text(
        root / SUMMARY_PATH,
        json.dumps(corpus.summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return corpus


__all__ = [
    "CORPUS_SUBDIRECTORY",
    "EXPECTED_PER_FAMILY",
    "EXPECTED_TIER_A_TOTAL",
    "EXPECTED_TIER_B_TOTAL",
    "EXPECTED_TOTAL",
    "GeneratedCorpus",
    "GenerationError",
    "MANIFEST_PATH",
    "ManifestPreambleError",
    "SUMMARY_PATH",
    "TIER_A_CLASS_PLAN",
    "TIER_B_CLASS_PLAN",
    "TIER_A_FAMILIES",
    "TIER_B_FAMILIES",
    "build_corpus",
    "build_summary",
    "corpus_files",
    "generate_cases",
    "inventory_slots",
    "manifest_text",
    "validate_cases",
    "write_corpus",
]
