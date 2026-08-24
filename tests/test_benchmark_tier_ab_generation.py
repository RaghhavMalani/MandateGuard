"""D7 tests for the registered deterministic Tier A/B benchmark corpus.

These tests never execute the registered corpus through ``evaluate_tier_a``,
``evaluate_tier_b``, ``authorize_transaction``, ``finalize_authorization``, or
the semantic verifier. The first registered detector execution is the next
step, after hostile review of this corpus.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from mandateguard.benchmark.codec import (
    case_content_projection,
    case_content_sha256,
    case_record_line,
    decode_case,
    decode_evaluation_inputs,
    decode_timestamp,
    encode_evaluation_inputs,
)
from mandateguard.benchmark.deterministic_generator import (
    CORPUS_SUBDIRECTORY,
    EXPECTED_PER_FAMILY,
    EXPECTED_TIER_A_TOTAL,
    EXPECTED_TIER_B_TOTAL,
    EXPECTED_TOTAL,
    MANIFEST_PATH,
    SUMMARY_PATH,
    build_corpus,
    generate_cases,
    inventory_slots,
    write_corpus,
)
from mandateguard.benchmark.manifest import (
    MANIFEST_CASE_FIELDS,
    ManifestPreambleError,
    frozen_preamble,
    normalized_manifest_text,
)
from mandateguard.benchmark.models import (
    BENCHMARK_FAMILIES,
    CASE_SCHEMA_VERSION,
    GENERATOR_VERSION,
    TIER_A_FAMILIES,
    TIER_B_FAMILIES,
    TargetExpectation,
)
from mandateguard.benchmark.recipes import build_inputs, default_scenario
from mandateguard.core.canonical import canonical_json_text
from mandateguard.core.hashing import sha256_canonical


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPOSITORY_ROOT / CORPUS_SUBDIRECTORY
# The label timestamp is audit metadata recorded at the moment the corpus was
# materialized, so it is read back from the committed generation summary rather
# than hard-coded here: a stale constant would silently misstate when the labels
# were actually recorded.
LABEL_RECORDED_AT = decode_timestamp(
    json.loads((REPOSITORY_ROOT / SUMMARY_PATH).read_text(encoding="utf-8"))[
        "label_recorded_at"
    ]
)

FORBIDDEN_MODULE_PREFIXES = (
    "mandateguard.policy",
    "mandateguard.semantic",
    "mandateguard.execution",
    "mandateguard.replay",
)
TIER_C_MARKERS = ("C-DEV", "C-HOLD", "held_out", "benign_control", "human_adjudication")


def _committed_records() -> list[dict]:
    records: list[dict] = []
    for family_id in BENCHMARK_FAMILIES:
        path = CORPUS_ROOT / f"{family_id}.jsonl"
        text = path.read_text(encoding="utf-8")
        records.extend(json.loads(line) for line in text.splitlines())
    return records


def _manifest_records() -> list[dict[str, str | None]]:
    text = normalized_manifest_text((REPOSITORY_ROOT / MANIFEST_PATH).read_bytes())
    body = text[text.index("\ncases:\n") + len("\ncases:\n") :]
    records: list[dict[str, str | None]] = []
    current: dict[str, str | None] = {}
    for line in body.splitlines():
        if line.startswith("  - "):
            if current:
                records.append(current)
            current = {}
            line = "    " + line[4:]
        key, _, raw = line.strip().partition(": ")
        current[key] = None if raw == "null" else raw.strip('"')
    if current:
        records.append(current)
    return records


@pytest.fixture(scope="module")
def committed_records() -> list[dict]:
    return _committed_records()


@pytest.fixture(scope="module")
def violation_record(committed_records) -> dict:
    """A BLOCK/violation/FAIL base, so every mutation below is a real change."""

    return next(
        record for record in committed_records if record["case_id"] == "A1-V-001"
    )


@pytest.fixture(scope="module")
def manifest_records() -> list[dict[str, str | None]]:
    return _manifest_records()


# A/B: registered inventory totals ----------------------------------------


def test_corpus_holds_exactly_the_registered_total(committed_records):
    assert len(committed_records) == EXPECTED_TOTAL


def test_corpus_layout_is_eighteen_family_files():
    files = sorted(path.name for path in CORPUS_ROOT.glob("*.jsonl"))
    assert files == sorted(f"{family}.jsonl" for family in BENCHMARK_FAMILIES)
    assert len(files) == 18


def test_each_family_holds_fifty_six_cases(committed_records):
    counts: dict[str, int] = {}
    for record in committed_records:
        counts[record["family_id"]] = counts.get(record["family_id"], 0) + 1
    assert counts == {family: EXPECTED_PER_FAMILY for family in BENCHMARK_FAMILIES}


def test_tier_totals(committed_records):
    tier_a = [r for r in committed_records if r["evidence_tier"] == "A"]
    tier_b = [r for r in committed_records if r["evidence_tier"] == "B"]
    assert len(tier_a) == EXPECTED_TIER_A_TOTAL
    assert len(tier_b) == EXPECTED_TIER_B_TOTAL


# C/D: per-family case-class counts ---------------------------------------


def _class_counts(records, family_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record["family_id"] != family_id:
            continue
        case_class = record["case_id"].split("-")[1]
        counts[case_class] = counts.get(case_class, 0) + 1
    return counts


@pytest.mark.parametrize("family_id", TIER_A_FAMILIES)
def test_tier_a_class_counts(committed_records, family_id):
    assert _class_counts(committed_records, family_id) == {"V": 24, "P": 24, "NE": 8}


@pytest.mark.parametrize("family_id", TIER_B_FAMILIES)
def test_tier_b_class_counts(committed_records, family_id):
    assert _class_counts(committed_records, family_id) == {"V": 28, "P": 28}


# E-I: registered metadata -------------------------------------------------


def test_registered_metadata_values(committed_records):
    expected_by_class = {
        "V": ("violation", "BLOCK", "FAIL"),
        "P": ("benign", "ALLOW", "PASS"),
        "NE": ("benign", "REVIEW", "NOT_EVALUABLE"),
    }
    for record in committed_records:
        case_class = record["case_id"].split("-")[1]
        ground_truth, action, status = expected_by_class[case_class]
        assert record["case_schema_version"] == CASE_SCHEMA_VERSION
        assert record["provenance"] == "developer_authored"
        assert record["split"] == "dev"
        assert record["label_source"] == "deterministic_invariant"
        assert record["ground_truth"] == ground_truth
        assert record["expected_action"] == action
        assert record["target_expectation"] == {
            "family_id": record["family_id"],
            "status": status,
        }
        assert record["evidence_tier"] == record["family_id"][0]
        assert record["generator"]["generator_version"] == GENERATOR_VERSION


def test_every_case_records_exactly_one_first_run(committed_records, manifest_records):
    """The registered corpus has now been executed once, on 2026-08-24.

    ``benchmark/MANIFEST.yaml`` registers ``first_run_at`` as "Null until first
    detector execution, then immutable". Both mirrors carry the same recorded
    value for all 1,008 cases, and the digests are unmoved (see
    ``test_recomputing_every_case_content_digest_matches``).
    """

    assert all(record["first_run_at"] is not None for record in committed_records)
    assert all(record["first_run_at"] is not None for record in manifest_records)
    assert sum(1 for r in committed_records if r["first_run_at"]) == EXPECTED_TOTAL

    corpus = {r["case_id"]: r["first_run_at"] for r in committed_records}
    manifest = {r["case_id"]: r["first_run_at"] for r in manifest_records}
    assert corpus == manifest
    for value in corpus.values():
        assert decode_timestamp(value).tzinfo is timezone.utc


def test_only_evidence_unavailable_cases_request_review(committed_records):
    review = {
        record["case_id"]
        for record in committed_records
        if record["expected_action"] == "REVIEW"
    }
    assert len(review) == 8 * len(TIER_A_FAMILIES)
    assert all(case_id.split("-")[1] == "NE" for case_id in review)


# J/K: no semantic evidence, no Tier C content -----------------------------


def test_no_case_carries_a_semantic_constraint(committed_records):
    for record in committed_records:
        constraints = record["evaluation_inputs"]["mandate"]["payload"]["constraints"]
        assert constraints["semantic"] == []


def test_no_tier_c_family_or_marker_appears(committed_records):
    for family_id in BENCHMARK_FAMILIES:
        text = (CORPUS_ROOT / f"{family_id}.jsonl").read_text(encoding="utf-8")
        for marker in TIER_C_MARKERS:
            assert marker not in text
    assert all(
        record["family_id"] in BENCHMARK_FAMILIES for record in committed_records
    )


def test_manifest_holds_no_tier_c_record(manifest_records):
    assert len(manifest_records) == EXPECTED_TOTAL
    assert all(record["family_id"] in BENCHMARK_FAMILIES for record in manifest_records)
    assert all(record["split"] == "dev" for record in manifest_records)


# L/M/N: identity and digests ----------------------------------------------


def test_case_identifiers_are_unique_and_stable(committed_records):
    identifiers = [record["case_id"] for record in committed_records]
    assert len(set(identifiers)) == EXPECTED_TOTAL
    expected = {
        f"{family}-{case_class}-{index + 1:03d}"
        for family, case_class, index, *_ in inventory_slots()
    }
    assert set(identifiers) == expected


def test_case_content_hashes_are_unique(committed_records):
    digests = [record["case_content_sha256"] for record in committed_records]
    assert len(set(digests)) == EXPECTED_TOTAL


def test_recomputing_every_case_content_digest_matches(committed_records):
    for record in committed_records:
        case = decode_case(record)
        assert case_content_sha256(case) == record["case_content_sha256"]


def test_corpus_records_are_sorted_by_case_id():
    for family_id in BENCHMARK_FAMILIES:
        text = (CORPUS_ROOT / f"{family_id}.jsonl").read_text(encoding="utf-8")
        identifiers = [json.loads(line)["case_id"] for line in text.splitlines()]
        assert identifiers == sorted(identifiers)
        assert len(identifiers) == EXPECTED_PER_FAMILY


def test_corpus_files_end_with_exactly_one_newline():
    for family_id in BENCHMARK_FAMILIES:
        raw = (CORPUS_ROOT / f"{family_id}.jsonl").read_bytes()
        assert raw.endswith(b"\n")
        assert not raw.endswith(b"\n\n")
        assert b"\r" not in raw


# O: manifest <-> corpus one-to-one ---------------------------------------


def test_manifest_and_corpus_map_one_to_one(committed_records, manifest_records):
    corpus_by_id = {record["case_id"]: record for record in committed_records}
    manifest_by_id = {record["case_id"]: record for record in manifest_records}
    assert len(manifest_by_id) == len(manifest_records) == EXPECTED_TOTAL
    assert set(corpus_by_id) == set(manifest_by_id)
    for case_id, entry in manifest_by_id.items():
        record = corpus_by_id[case_id]
        assert tuple(entry) == MANIFEST_CASE_FIELDS
        for field in MANIFEST_CASE_FIELDS:
            assert entry[field] == record[field], (case_id, field)


def test_manifest_ordering_is_the_registered_order(manifest_records):
    expected = [
        f"{family}-{case_class}-{index + 1:03d}"
        for family, case_class, index, *_ in inventory_slots()
    ]
    assert [record["case_id"] for record in manifest_records] == expected


def test_manifest_preamble_is_unchanged():
    preamble = frozen_preamble(REPOSITORY_ROOT / MANIFEST_PATH)
    assert preamble.startswith('schema_version: "1.2"')
    assert "cases:" not in preamble


def test_generator_refuses_a_modified_manifest_preamble(tmp_path):
    manifest = tmp_path / MANIFEST_PATH
    manifest.parent.mkdir(parents=True)
    text = normalized_manifest_text((REPOSITORY_ROOT / MANIFEST_PATH).read_bytes())
    manifest.write_text(
        text.replace('artifact_status: "pre_registered_structure"', 'artifact_status: "x"'),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ManifestPreambleError):
        frozen_preamble(manifest)


# P: typed round-trip ------------------------------------------------------


def test_every_case_round_trips_to_identical_canonical_bytes(committed_records):
    for record in committed_records:
        case = decode_case(record)
        assert json.loads(case_record_line(case)) == record


def test_typed_evaluation_inputs_round_trip(committed_records):
    for record in committed_records:
        case = decode_case(record)
        inputs = case.evaluation_inputs
        reencoded = encode_evaluation_inputs(inputs)
        assert reencoded == record["evaluation_inputs"]
        rebuilt = decode_evaluation_inputs(reencoded)
        assert rebuilt == inputs
        assert encode_evaluation_inputs(rebuilt) == reencoded


def test_round_trip_reconstructs_the_typed_objects(committed_records):
    from mandateguard.core.hashing import CommittedHashes
    from mandateguard.core.nonce_ledger import NonceLedgerState
    from mandateguard.models.catalog import CatalogSnapshot
    from mandateguard.models.mandate import Mandate
    from mandateguard.models.transaction import Transaction

    seen_absent = {"catalog": False, "server_time": False, "nonce": False, "commit": False}
    for record in committed_records:
        inputs = decode_case(record).evaluation_inputs
        assert isinstance(inputs.mandate, Mandate)
        assert isinstance(inputs.transaction, Transaction)
        assert isinstance(inputs.replay_seed, int)
        assert inputs.evaluated_at.tzinfo is timezone.utc
        for value, kind, expected_type in (
            (inputs.catalog_snapshot, "catalog", CatalogSnapshot),
            (inputs.server_time, "server_time", datetime),
            (inputs.nonce_state, "nonce", NonceLedgerState),
            (inputs.psp_committed_hashes, "commit", CommittedHashes),
        ):
            if value is None:
                seen_absent[kind] = True
            else:
                assert isinstance(value, expected_type)
    assert all(seen_absent.values())


def test_generator_primitives_round_trip_on_synthetic_non_registered_input():
    # Index 500 is outside every registered inventory range, so this exercises
    # the primitives without touching a registered case.
    scenario = default_scenario("A1", "P", 500)
    inputs = build_inputs(scenario)
    encoded = encode_evaluation_inputs(inputs)
    assert decode_evaluation_inputs(encoded) == inputs
    assert sha256_canonical(encoded) == sha256_canonical(
        encode_evaluation_inputs(decode_evaluation_inputs(encoded))
    )


# Q: byte-identical regeneration ------------------------------------------


def _seed_root(root: Path) -> Path:
    (root / "benchmark").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPOSITORY_ROOT / MANIFEST_PATH, root / MANIFEST_PATH)
    return root


def test_two_generation_runs_are_byte_identical(tmp_path):
    first = write_corpus(_seed_root(tmp_path / "first"), LABEL_RECORDED_AT)
    second = write_corpus(_seed_root(tmp_path / "second"), LABEL_RECORDED_AT)
    assert first.corpus_files == second.corpus_files
    assert first.manifest_text == second.manifest_text
    assert first.summary == second.summary
    for family_id in BENCHMARK_FAMILIES:
        name = f"{family_id}.jsonl"
        left = (tmp_path / "first" / CORPUS_SUBDIRECTORY / name).read_bytes()
        right = (tmp_path / "second" / CORPUS_SUBDIRECTORY / name).read_bytes()
        assert left == right
    assert (tmp_path / "first" / MANIFEST_PATH).read_bytes() == (
        tmp_path / "second" / MANIFEST_PATH
    ).read_bytes()


def _without_first_run(line: str) -> str:
    record = json.loads(line)
    record["first_run_at"] = None
    return canonical_json_text(record)


def test_committed_corpus_matches_a_fresh_generation(tmp_path):
    """Content reproduction still holds, with the lifecycle field set aside.

    The generator remains a pure function of its version, the registered
    recipes, and the explicit label timestamp. Since the first registered
    execution, the committed artifacts additionally carry ``first_run_at``,
    which generation never writes, so it is the one field lifted out before the
    comparison. Every other byte must still match, which is what makes this a
    tamper guard rather than a formality.
    """

    generated = build_corpus(LABEL_RECORDED_AT, REPOSITORY_ROOT / MANIFEST_PATH)
    for family_id in BENCHMARK_FAMILIES:
        name = f"{family_id}.jsonl"
        committed = (CORPUS_ROOT / name).read_bytes().decode("utf-8")
        stripped = [_without_first_run(line) for line in committed.splitlines()]
        assert generated.corpus_files[name] == "\n".join(stripped) + "\n"
        assert all(
            json.loads(line)["first_run_at"] is not None
            for line in committed.splitlines()
        )

    committed_manifest = normalized_manifest_text(
        (REPOSITORY_ROOT / MANIFEST_PATH).read_bytes()
    )
    # Four spaces matches a case record only; the two-space ``field_rules``
    # entry in the frozen preamble must not be rewritten.
    stripped_manifest = re.sub(
        r'^    first_run_at: "[^"]+"$',
        "    first_run_at: null",
        committed_manifest,
        flags=re.MULTILINE,
    )
    assert generated.manifest_text == stripped_manifest
    assert 'first_run_at: null' not in committed_manifest


def test_generation_requires_an_explicit_aware_timestamp():
    with pytest.raises(Exception):
        generate_cases(datetime(2026, 8, 23))


def test_summary_records_generation_audit_only():
    """A generation-time snapshot, deliberately not rewritten after execution.

    ``benchmark/deterministic/README.md`` documents this file as generation
    audit metadata. Its ``corpus_file_sha256`` values are the pre-execution
    digests and its lifecycle counts describe the moment of generation, so it
    stays byte-immutable; the post-execution facts that supersede it live in
    ``benchmark/results/tier_ab/FIRST_RUN_SUMMARY.json``.
    """

    summary = json.loads((REPOSITORY_ROOT / SUMMARY_PATH).read_text(encoding="utf-8"))
    assert summary["total_cases"] == EXPECTED_TOTAL
    assert summary["tier_a_total"] == EXPECTED_TIER_A_TOTAL
    assert summary["tier_b_total"] == EXPECTED_TIER_B_TOTAL
    assert summary["unique_case_ids"] == EXPECTED_TOTAL
    assert summary["unique_content_hashes"] == EXPECTED_TOTAL
    assert summary["first_run_null_count"] == EXPECTED_TOTAL
    assert summary["semantic_constraint_case_count"] == 0
    assert summary["tier_c_case_count"] == 0
    assert summary["registered_corpus_executed"] is False
    assert summary["generator_version"] == GENERATOR_VERSION
    assert len(summary["corpus_file_sha256"]) == 18
    forbidden = {"accuracy", "precision", "recall", "passed", "failed", "score"}
    assert forbidden.isdisjoint(summary)


# R/S/T: hash projection boundaries ---------------------------------------


def test_changing_a_hashed_evaluation_input_changes_the_digest(violation_record):
    case = decode_case(violation_record)
    baseline = case_content_sha256(case)
    mutated = replace(
        case,
        evaluation_inputs=replace(
            case.evaluation_inputs,
            replay_seed=case.evaluation_inputs.replay_seed + 1,
        ),
    )
    assert case_content_sha256(mutated) != baseline


@pytest.mark.parametrize(
    "field, value",
    [
        ("evidence_tier", "B"),
        ("family_id", "B4"),
        ("provenance", "separate_model_adversarial"),
        ("split", "held_out"),
        ("ground_truth", "benign"),
        ("label_source", "human_adjudication"),
        ("expected_action", "ALLOW"),
        ("case_schema_version", "9.9"),
    ],
)
def test_changing_any_hashed_metadata_field_changes_the_digest(
    violation_record, field, value
):
    case = decode_case(violation_record)
    projection = case_content_projection(case)
    baseline = sha256_canonical(projection)
    projection[field] = value
    assert sha256_canonical(projection) != baseline


def test_changing_the_target_expectation_changes_the_digest(violation_record):
    case = decode_case(violation_record)
    baseline = case_content_sha256(case)
    mutated = replace(
        case,
        ground_truth="benign",
        expected_action="REVIEW",
        target_expectation=TargetExpectation(
            family_id=case.family_id, status="NOT_EVALUABLE"
        ),
    )
    assert case_content_sha256(mutated) != baseline


def test_audit_only_label_timestamp_does_not_change_the_digest(violation_record):
    case = decode_case(violation_record)
    shifted = replace(
        case, label_recorded_at=case.label_recorded_at + timedelta(days=365)
    )
    assert case_content_sha256(shifted) == case_content_sha256(case)


def test_case_id_is_excluded_from_the_content_digest(violation_record):
    case = decode_case(violation_record)
    renamed = replace(case, case_id="RENAMED-V-999")
    assert case_content_sha256(renamed) == case_content_sha256(case)


def test_generator_audit_metadata_is_excluded_from_the_content_digest(
    violation_record,
):
    case = decode_case(violation_record)
    projection = case_content_projection(case)
    assert "generator" not in projection
    assert "case_content_sha256" not in projection
    assert "label_recorded_at" not in projection
    assert "first_run_at" not in projection
    assert "case_id" not in projection


def test_decoding_refuses_a_tampered_content_digest(violation_record):
    record = dict(violation_record)
    record["case_content_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        decode_case(record)


def test_a_recorded_first_run_round_trips_without_changing_the_digest(
    violation_record,
):
    """Lifecycle metadata is audit-only: recording it may not move the digest.

    ``benchmark/MANIFEST.yaml`` registers ``first_run_at`` as "Null until first
    detector execution, then immutable", so the codec must carry a recorded
    value through a round trip while leaving ``case_content_sha256`` alone.
    """

    unexecuted = dict(violation_record)
    unexecuted["first_run_at"] = None
    baseline = decode_case(unexecuted)
    record = dict(violation_record)
    record["first_run_at"] = "2026-08-23T00:00:00.000000Z"
    executed = decode_case(record)
    assert executed.first_run_at == datetime(2026, 8, 23, tzinfo=timezone.utc)
    assert baseline.first_run_at is None
    assert case_content_sha256(executed) == case_content_sha256(baseline)
    assert case_content_sha256(executed) == violation_record["case_content_sha256"]
    assert json.loads(case_record_line(executed))["first_run_at"] == (
        "2026-08-23T00:00:00.000000Z"
    )


def test_decoding_refuses_a_malformed_first_run(violation_record):
    for malformed in ("2026-08-23T00:00:00.000000", "not-a-timestamp", 0):
        record = dict(violation_record)
        record["first_run_at"] = malformed
        with pytest.raises(ValueError):
            decode_case(record)


# U: the generator never executes the registered detector ------------------


# The D8 execution harness lives in the same package and must reach the frozen
# policy - that is its whole purpose. The property this test protects is
# narrower and unchanged: nothing on the generation path may touch a detector,
# so the generation modules are enumerated rather than globbed.
GENERATION_MODULES = (
    "__init__.py",
    "codec.py",
    "deterministic_generator.py",
    "manifest.py",
    "models.py",
    "recipes.py",
)


def test_benchmark_package_never_imports_the_detector():
    package = REPOSITORY_ROOT / "src" / "mandateguard" / "benchmark"
    sources = [package / name for name in GENERATION_MODULES]
    sources.append(REPOSITORY_ROOT / "scripts" / "generate_tier_ab_benchmark.py")
    assert sources
    assert all(path.exists() for path in sources)
    present = {path.name for path in package.glob("*.py")}
    assert set(GENERATION_MODULES) <= present
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            assert not any(
                prefix in stripped for prefix in FORBIDDEN_MODULE_PREFIXES
            ), f"{path.name}: {stripped}"
            for symbol in (
                "evaluate_tier_a",
                "evaluate_tier_b",
                "authorize_transaction",
                "finalize_authorization",
                "SemanticVerifier",
            ):
                assert symbol not in stripped, f"{path.name}: {stripped}"


def test_generation_loads_no_detector_module(tmp_path):
    root = _seed_root(tmp_path / "isolated")
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT / 'src')!r})\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from mandateguard.benchmark.deterministic_generator import write_corpus\n"
        f"write_corpus(Path({str(root)!r}), datetime(2026, 8, 23, tzinfo=timezone.utc))\n"
        "loaded = sorted(n for n in sys.modules if n.startswith("
        f"{FORBIDDEN_MODULE_PREFIXES!r}))\n"
        "print('|'.join(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == ""
