"""Non-benchmark semantic MVP engineering corpus and runner tests."""

from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mandateguard.engineering.semantic_fixtures import (
    BENCHMARK_ONLY_FIELDS,
    EngineeringExpectation,
    FixtureDifficulty,
    SemanticFamily,
    SemanticMvpFixtureError,
    build_semantic_scenario,
    fixture_record,
    fixture_record_line,
    load_fixture_corpus,
    parse_fixture_line,
    select_fixtures,
    validate_clean_deterministic_envelope,
)
from mandateguard.engineering.semantic_runner import (
    EngineeringLiveResult,
    SemanticMvpLiveError,
    live_result_record,
    require_engineering_artifact_path,
    run_live_fixture,
    write_live_results,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPOSITORY_ROOT / "fixtures" / "semantic_mvp" / "semantic_cases.jsonl"
)
MANIFEST_PATH = REPOSITORY_ROOT / "benchmark" / "MANIFEST.yaml"
RUNNER_SCRIPT = REPOSITORY_ROOT / "scripts" / "run_semantic_mvp_fixtures.py"
RUNNER_MODULE = (
    REPOSITORY_ROOT
    / "src"
    / "mandateguard"
    / "engineering"
    / "semantic_runner.py"
)
FROZEN_PRODUCT_BASE = "f6e794546efefd1083fc246515d4c96e9949d859"
# Derived from git objects at FROZEN_PRODUCT_BASE, not from the working tree.
FORMAL_FILE_SHA256 = {
    "benchmark/MANIFEST.yaml": (
        "a6ab3c7d826c545b637a11954c3816611f348c21684912daaf393f38ea0aeef1"
    ),
    "benchmark/PROTOCOL.md": (
        "d307b6d0c0ffdf40707285ea384ac3c16e54d899edb75262c87897be1543a0e2"
    ),
    "TAXONOMY.md": (
        "6d1659d09f7653cc839c17977184c20038c49eb73b5dcfa12210c1ac20d388fd"
    ),
}


def _git(
    repository_root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        capture_output=True,
        check=check,
    )


def _committed_git_object_bytes(
    repository_root: Path,
    revision: str,
    relative_path: str,
) -> bytes:
    return _git(
        repository_root,
        "show",
        f"{revision}:{relative_path}",
    ).stdout


def _assert_no_protected_file_drift(
    repository_root: Path,
    relative_paths: tuple[str, ...],
) -> None:
    checks = (
        ("unstaged", ("diff", "--quiet", "--", *relative_paths)),
        (
            "staged",
            ("diff", "--cached", "--quiet", "HEAD", "--", *relative_paths),
        ),
    )
    for drift_kind, arguments in checks:
        completed = _git(repository_root, *arguments, check=False)
        if completed.returncode == 1:
            raise AssertionError(
                f"protected files have {drift_kind} Git drift"
            )
        if completed.returncode != 0:
            detail = completed.stderr.decode(errors="replace").strip()
            raise AssertionError(
                f"Git could not check protected-file {drift_kind} drift: "
                f"{detail}"
            )


@pytest.fixture(scope="module")
def fixtures():
    return load_fixture_corpus(FIXTURE_PATH)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found.update(_all_keys(item))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_all_keys(item))
        return found
    return set()


def test_corpus_contains_exactly_72_unique_engineering_fixtures(fixtures):
    assert len(fixtures) == 72
    assert len({fixture.fixture_id for fixture in fixtures}) == 72
    assert all(fixture.fixture_id.startswith("SMVP-") for fixture in fixtures)
    assert not any(
        fixture.fixture_id.startswith(("C-DEV", "C-HOLD")) for fixture in fixtures
    )


def test_family_and_expectation_distributions_are_exact(fixtures):
    assert Counter(fixture.family for fixture in fixtures) == {
        SemanticFamily.RECURRENCE: 24,
        SemanticFamily.EXCLUSION: 24,
        SemanticFamily.PURPOSE: 24,
    }
    assert Counter(fixture.engineering_expectation for fixture in fixtures) == {
        EngineeringExpectation.PASS: 24,
        EngineeringExpectation.VIOLATION: 24,
        EngineeringExpectation.ABSTAIN: 24,
    }
    assert Counter(
        (fixture.family, fixture.engineering_expectation)
        for fixture in fixtures
    ) == {
        (family, expectation): 8
        for family in SemanticFamily
        for expectation in EngineeringExpectation
    }


def test_demo_distribution_is_one_of_each_outcome_per_family(fixtures):
    demos = tuple(fixture for fixture in fixtures if fixture.demo_priority)
    assert len(demos) == 9
    assert Counter(fixture.family for fixture in demos) == {
        family: 3 for family in SemanticFamily
    }
    assert Counter(
        (fixture.family, fixture.engineering_expectation) for fixture in demos
    ) == {
        (family, expectation): 1
        for family in SemanticFamily
        for expectation in EngineeringExpectation
    }
    assert {fixture.fixture_id for fixture in demos} == {
        "SMVP-REC-PASS-001",
        "SMVP-REC-VIOLATION-001",
        "SMVP-REC-ABSTAIN-001",
        "SMVP-EXC-PASS-001",
        "SMVP-EXC-VIOLATION-001",
        "SMVP-EXC-ABSTAIN-001",
        "SMVP-PUR-PASS-001",
        "SMVP-PUR-VIOLATION-001",
        "SMVP-PUR-ABSTAIN-001",
    }


def test_fixture_quality_fields_are_populated_and_varied(fixtures):
    assert all(fixture.semantic_constraint_text for fixture in fixtures)
    assert all(fixture.semantic_evidence.entries for fixture in fixtures)
    assert all(fixture.developer_rationale for fixture in fixtures)
    assert {fixture.difficulty for fixture in fixtures} == set(FixtureDifficulty)
    for family in SemanticFamily:
        family_fixtures = [
            fixture for fixture in fixtures if fixture.family is family
        ]
        assert len(
            {fixture.semantic_constraint_text for fixture in family_fixtures}
        ) == 24
        assert len(
            {
                len(fixture.semantic_evidence.entries)
                for fixture in family_fixtures
            }
        ) == 3


def test_all_envelopes_are_clean_under_frozen_tier_a_and_b(fixtures):
    for fixture in fixtures:
        scenario = build_semantic_scenario(fixture)
        validate_clean_deterministic_envelope(scenario)
        assert scenario.transaction.payload.cart_recurring is False
        assert scenario.transaction.payload.order_recurring is False
        assert all(
            line.recurring is False
            for line in scenario.transaction.payload.lines
        )


def test_fixture_records_have_no_benchmark_or_lifecycle_fields(fixtures):
    for fixture in fixtures:
        keys = _all_keys(fixture_record(fixture))
        assert not keys.intersection(BENCHMARK_ONLY_FIELDS)
        assert "engineering_expectation" in keys
        assert "ground_truth" not in keys


def test_parser_round_trips_every_fixture_canonically(fixtures):
    for fixture in fixtures:
        encoded = fixture_record_line(fixture)
        decoded = parse_fixture_line(encoded)
        assert decoded == fixture
        assert fixture_record_line(decoded) == encoded


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {key: item for key, item in value.items() if key != "family"},
        lambda value: {**value, "ground_truth": "benign"},
        lambda value: {**value, "family": "C-DEV-PURPOSE"},
        lambda value: {**value, "demo_priority": "true"},
        lambda value: {
            **value,
            "semantic_evidence": {
                **value["semantic_evidence"],
                "entries": [],
            },
        },
    ],
)
def test_malformed_fixture_is_rejected(fixtures, mutation):
    record = mutation(fixture_record(fixtures[0]))
    with pytest.raises(SemanticMvpFixtureError):
        parse_fixture_line(json.dumps(record))


def test_duplicate_fixture_id_is_rejected(fixtures):
    duplicate = (*fixtures[:-1], fixtures[0])
    from mandateguard.engineering.semantic_fixtures import validate_fixture_corpus

    with pytest.raises(SemanticMvpFixtureError, match="unique"):
        validate_fixture_corpus(duplicate)


def test_plain_loading_imports_no_execution_provider_network_or_razorpay():
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPOSITORY_ROOT / 'src')!r})\n"
        "from pathlib import Path\n"
        "from mandateguard.engineering.semantic_fixtures import "
        "load_fixture_corpus\n"
        f"load_fixture_corpus(Path({str(FIXTURE_PATH)!r}))\n"
        "forbidden=('mandateguard.semantic','mandateguard.execution.razorpay',"
        "'openai','requests','httpx')\n"
        "print('|'.join(sorted(name for name in sys.modules "
        "if name.startswith(forbidden))))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == ""


def test_default_and_validate_only_cli_make_zero_model_calls(tmp_path):
    (tmp_path / "dotenv.py").write_text(
        "raise AssertionError('offline mode imported dotenv')\n",
        encoding="utf-8",
    )
    child_environment = {
        **dict(os.environ),
        "OPENAI_API_KEY": "must-not-be-used",
    }
    child_environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (str(tmp_path), child_environment.get("PYTHONPATH")),
        )
    )
    for extra in ((), ("--validate-only",)):
        completed = subprocess.run(
            [sys.executable, str(RUNNER_SCRIPT), *extra],
            capture_output=True,
            text=True,
            check=True,
            env=child_environment,
        )
        assert "ENGINEERING FIXTURE DIAGNOSTIC" in completed.stdout
        assert "validation=PASS" in completed.stdout
        assert "live=false model_calls=0" in completed.stdout


def test_live_cli_loads_both_semantic_variables_from_dotenv(
    tmp_path,
    monkeypatch,
):
    runner_globals = runpy.run_path(
        str(RUNNER_SCRIPT),
        run_name="semantic_mvp_runner_under_test",
    )
    live_main = runner_globals["main"]
    live_main.__globals__["REPOSITORY_ROOT"] = tmp_path
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=synthetic-local-key\n"
        "MANDATEGUARD_SEMANTIC_MODEL=gpt-5.6-terra\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MANDATEGUARD_SEMANTIC_MODEL", raising=False)

    import mandateguard.engineering.semantic_runner as semantic_runner

    captured: dict[str, str] = {}

    def fake_create_verifier(*, model_id, cache_directory):
        captured["api_key"] = os.environ["OPENAI_API_KEY"]
        captured["model_id"] = model_id
        return object()

    monkeypatch.setattr(
        semantic_runner,
        "require_engineering_artifact_path",
        lambda path, *, repository_root: path,
    )
    monkeypatch.setattr(
        semantic_runner,
        "create_openai_semantic_verifier",
        fake_create_verifier,
    )
    monkeypatch.setattr(
        semantic_runner,
        "run_live_fixtures",
        lambda fixtures, *, semantic_verifier: (),
    )
    monkeypatch.setattr(
        semantic_runner,
        "write_live_results",
        lambda results, output_path, *, repository_root: output_path,
    )

    exit_code = live_main(
        [
            "--live",
            "--limit",
            "1",
            "--fixtures",
            str(FIXTURE_PATH),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert exit_code == 0
    assert captured == {
        "api_key": "synthetic-local-key",
        "model_id": "gpt-5.6-terra",
    }


def test_cli_selection_flags_are_deterministic(fixtures):
    assert len(select_fixtures(fixtures, demo_only=True)) == 9
    assert len(select_fixtures(fixtures, demo_only=True, limit=3)) == 3
    selected = select_fixtures(
        fixtures, case_id="SMVP-PUR-ABSTAIN-001"
    )
    assert tuple(item.fixture_id for item in selected) == (
        "SMVP-PUR-ABSTAIN-001",
    )


def test_developer_rationale_never_enters_frozen_semantic_request(fixtures):
    from mandateguard.semantic.verifier import build_semantic_request

    fixture = fixtures[0]
    scenario = build_semantic_scenario(fixture)
    request = build_semantic_request(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        semantic_evidence=scenario.semantic_evidence,
        model_id="engineering-test-model",
    )
    serialized = repr(request)
    assert fixture.developer_rationale not in serialized
    assert fixture.semantic_constraint_text in serialized
    assert {
        entry.text for entry in fixture.semantic_evidence.entries
    }.issubset({entry.text for entry in request.selected_evidence})


def test_live_path_calls_frozen_authorization_pipeline_without_reimplementation(
    fixtures, monkeypatch
):
    import mandateguard.semantic.orchestration as orchestration
    from mandateguard.models.decision import DecisionAction
    from mandateguard.semantic.models import (
        ConstraintResult,
        ConstraintStatus,
        SemanticVerdict,
    )

    calls: list[dict[str, object]] = []

    def frozen_pipeline_stub(**kwargs):
        calls.append(kwargs)
        semantic = SimpleNamespace(
            verdict=SemanticVerdict.PASS,
            constraint_results=(
                ConstraintResult(
                    constraint_id="engineering-stub",
                    status=ConstraintStatus.PASS,
                    reason="synthetic frozen-pipeline reason",
                ),
            ),
            semantic_input_sha256="0" * 64,
            model_id="synthetic-verifier",
        )
        return SimpleNamespace(
            deterministic_decision=SimpleNamespace(
                action=DecisionAction.ALLOW
            ),
            semantic_decision=semantic,
            final_action=DecisionAction.ALLOW,
        )

    monkeypatch.setattr(
        orchestration, "authorize_transaction", frozen_pipeline_stub
    )
    verifier = object()
    result = run_live_fixture(fixtures[0], semantic_verifier=verifier)

    assert len(calls) == 1
    assert calls[0]["semantic_verifier"] is verifier
    assert "semantic_evidence" in calls[0]
    assert result.semantic_status == "PASS"
    source = RUNNER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "mandateguard.semantic.orchestration" in imports
    assert "mandateguard.policy.tier_a" not in imports
    assert "mandateguard.policy.tier_b" not in imports
    assert ".evaluate(" not in source


def test_engineering_live_artifacts_cannot_enter_benchmark(fixtures, tmp_path):
    result = EngineeringLiveResult(
        fixture_id=fixtures[0].fixture_id,
        engineering_expectation=fixtures[0].engineering_expectation,
        semantic_status="PASS",
        final_action="ALLOW",
        reason="synthetic artifact reason",
        semantic_input_sha256="1" * 64,
        latency_ms=1,
        provider="synthetic-provider",
        model_id="synthetic-model",
        run_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        engineering_expectation_match=True,
    )
    output = tmp_path / "artifacts" / "engineering" / "result.jsonl"
    assert write_live_results(
        (result,), output, repository_root=tmp_path
    ) == output
    decoded = json.loads(output.read_text(encoding="utf-8"))
    assert decoded == live_result_record(result)
    assert "ground_truth" not in decoded
    assert "first_run_at" not in decoded

    with pytest.raises(SemanticMvpLiveError, match="benchmark"):
        require_engineering_artifact_path(
            tmp_path / "benchmark" / "semantic-cache",
            repository_root=tmp_path,
        )
    with pytest.raises(SemanticMvpLiveError, match="benchmark"):
        write_live_results(
            (result,),
            tmp_path / "benchmark" / "results" / "result.jsonl",
            repository_root=tmp_path,
        )


def test_formal_benchmark_files_match_frozen_product_base(tmp_path):
    for relative_path, expected in FORMAL_FILE_SHA256.items():
        actual = sha256(
            _committed_git_object_bytes(
                REPOSITORY_ROOT,
                "HEAD",
                relative_path,
            )
        ).hexdigest()
        assert actual == expected, (
            f"{relative_path} differs from frozen product base "
            f"{FROZEN_PRODUCT_BASE}"
        )

    protected_paths = tuple(FORMAL_FILE_SHA256)
    _assert_no_protected_file_drift(REPOSITORY_ROOT, protected_paths)

    synthetic_root = tmp_path / "line-ending-portability"
    synthetic_root.mkdir()
    _git(synthetic_root, "init", "--quiet")
    _git(synthetic_root, "config", "user.name", "MandateGuard Tests")
    _git(
        synthetic_root,
        "config",
        "user.email",
        "mandateguard-tests@example.invalid",
    )
    _git(synthetic_root, "config", "core.autocrlf", "true")

    synthetic_path = synthetic_root / "protected.md"
    lf_bytes = b"frozen line one\nfrozen line two\n"
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    synthetic_path.write_bytes(lf_bytes)
    _git(synthetic_root, "add", "--", "protected.md")
    _git(synthetic_root, "commit", "--quiet", "-m", "frozen base")

    frozen_digest = sha256(lf_bytes).hexdigest()
    assert sha256(
        _committed_git_object_bytes(
            synthetic_root,
            "HEAD",
            "protected.md",
        )
    ).hexdigest() == frozen_digest

    synthetic_path.write_bytes(crlf_bytes)
    assert sha256(synthetic_path.read_bytes()).hexdigest() != frozen_digest
    assert sha256(
        _committed_git_object_bytes(
            synthetic_root,
            "HEAD",
            "protected.md",
        )
    ).hexdigest() == frozen_digest
    _assert_no_protected_file_drift(synthetic_root, ("protected.md",))

    synthetic_path.write_bytes(b"genuine content change\r\n")
    with pytest.raises(AssertionError, match="unstaged Git drift"):
        _assert_no_protected_file_drift(synthetic_root, ("protected.md",))
    _git(synthetic_root, "add", "--", "protected.md")
    with pytest.raises(AssertionError, match="staged Git drift"):
        _assert_no_protected_file_drift(synthetic_root, ("protected.md",))

    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    assert len(
        re.findall(r"^  - case_id:", manifest, flags=re.MULTILINE)
    ) == 1008
    tier_c_root = REPOSITORY_ROOT / "benchmark" / "cases" / "tier_c"
    assert not tier_c_root.exists()


def test_engineering_paths_are_separate_from_benchmark():
    fixture_relative = FIXTURE_PATH.relative_to(REPOSITORY_ROOT)
    assert fixture_relative.parts[0] == "fixtures"
    assert "benchmark" not in fixture_relative.parts
    assert "benchmark/results" not in RUNNER_MODULE.read_text(encoding="utf-8")
