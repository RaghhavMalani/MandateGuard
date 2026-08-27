"""Live diagnostics for non-benchmark semantic MVP fixtures.

Importing this module does not import a provider adapter or authorization path.
Those frozen components are imported inside live-only functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from time import perf_counter_ns
from typing import Iterable

from mandateguard.core.canonical import canonical_json_text
from mandateguard.engineering.semantic_fixtures import (
    EngineeringExpectation,
    SemanticMvpFixture,
    build_semantic_scenario,
)


class SemanticMvpLiveError(RuntimeError):
    """Live engineering diagnostics could not be configured or recorded."""


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class EngineeringLiveResult:
    """One non-benchmark live diagnostic record."""

    fixture_id: str
    engineering_expectation: EngineeringExpectation
    semantic_status: str
    final_action: str
    reason: str
    semantic_input_sha256: str
    latency_ms: int
    provider: str
    model_id: str
    run_at: datetime
    engineering_expectation_match: bool

    def __post_init__(self) -> None:
        if not self.fixture_id.startswith("SMVP-"):
            raise SemanticMvpLiveError("fixture_id is not an engineering fixture")
        if not isinstance(self.engineering_expectation, EngineeringExpectation):
            raise SemanticMvpLiveError("engineering_expectation is invalid")
        if self.semantic_status not in {"PASS", "VIOLATION", "ABSTAIN"}:
            raise SemanticMvpLiveError("semantic_status is invalid")
        if self.final_action not in {"ALLOW", "BLOCK", "REVIEW"}:
            raise SemanticMvpLiveError("final_action is invalid")
        if not isinstance(self.reason, str) or not self.reason:
            raise SemanticMvpLiveError("reason must be non-empty")
        if (
            not isinstance(self.semantic_input_sha256, str)
            or not _SHA256_RE.fullmatch(self.semantic_input_sha256)
        ):
            raise SemanticMvpLiveError("semantic_input_sha256 is invalid")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int)
            or self.latency_ms < 0
        ):
            raise SemanticMvpLiveError("latency_ms must be non-negative")
        if not isinstance(self.provider, str) or not self.provider:
            raise SemanticMvpLiveError("provider must be non-empty")
        if not isinstance(self.model_id, str) or not self.model_id:
            raise SemanticMvpLiveError("model_id must be non-empty")
        if (
            not isinstance(self.run_at, datetime)
            or self.run_at.tzinfo is None
            or self.run_at.utcoffset() is None
        ):
            raise SemanticMvpLiveError("run_at must be timezone-aware")
        if not isinstance(self.engineering_expectation_match, bool):
            raise SemanticMvpLiveError(
                "engineering_expectation_match must be boolean"
            )


def create_openai_semantic_verifier(
    *,
    model_id: str,
    cache_directory: Path,
    client: object | None = None,
) -> object:
    """Create the existing frozen verifier and adapter for explicit live mode."""

    if not isinstance(model_id, str) or not model_id:
        raise SemanticMvpLiveError("live mode requires a non-empty model_id")
    if not isinstance(cache_directory, Path):
        raise TypeError("cache_directory must be pathlib.Path")
    if client is None:
        try:
            from openai import OpenAI
        except ImportError as error:
            raise SemanticMvpLiveError(
                "live mode requires the optional openai package"
            ) from error
        client = OpenAI()

    from mandateguard.semantic.cache import FileSemanticCache
    from mandateguard.semantic.openai_adapter import (
        OpenAIResponsesSemanticModel,
    )
    from mandateguard.semantic.verifier import SemanticVerifier

    return SemanticVerifier(
        model=OpenAIResponsesSemanticModel(client=client, model_id=model_id),
        cache=FileSemanticCache(cache_directory),
    )


def run_live_fixture(
    fixture: SemanticMvpFixture,
    *,
    semantic_verifier: object,
    provider: str = "openai_responses",
) -> EngineeringLiveResult:
    """Run one fixture through the existing frozen authorization pipeline."""

    # Importing the authorization path is intentionally confined to live mode.
    from mandateguard.semantic.orchestration import authorize_transaction
    from mandateguard.semantic.verifier import SemanticMode

    scenario = build_semantic_scenario(fixture)
    started = perf_counter_ns()
    authorization = authorize_transaction(
        **scenario.authorization_inputs(),
        semantic_verifier=semantic_verifier,
        semantic_mode=SemanticMode.LIVE,
    )
    latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
    if authorization.deterministic_decision.action.value != "ALLOW":
        raise SemanticMvpLiveError(
            f"{fixture.fixture_id} did not reach the semantic path cleanly"
        )
    semantic_decision = authorization.semantic_decision
    if semantic_decision is None:
        raise SemanticMvpLiveError(
            f"{fixture.fixture_id} produced no semantic decision"
        )
    semantic_status = semantic_decision.verdict.value
    reasons = "; ".join(
        result.reason for result in semantic_decision.constraint_results
    )
    return EngineeringLiveResult(
        fixture_id=fixture.fixture_id,
        engineering_expectation=fixture.engineering_expectation,
        semantic_status=semantic_status,
        final_action=authorization.final_action.value,
        reason=reasons,
        semantic_input_sha256=semantic_decision.semantic_input_sha256,
        latency_ms=latency_ms,
        provider=provider,
        model_id=semantic_decision.model_id,
        run_at=datetime.now(timezone.utc),
        engineering_expectation_match=(
            semantic_status == fixture.engineering_expectation.value
        ),
    )


def run_live_fixtures(
    fixtures: Iterable[SemanticMvpFixture],
    *,
    semantic_verifier: object,
    provider: str = "openai_responses",
) -> tuple[EngineeringLiveResult, ...]:
    """Run only the caller-selected engineering fixtures."""

    return tuple(
        run_live_fixture(
            fixture,
            semantic_verifier=semantic_verifier,
            provider=provider,
        )
        for fixture in fixtures
    )


def live_result_record(result: EngineeringLiveResult) -> dict[str, object]:
    return {
        "fixture_id": result.fixture_id,
        "engineering_expectation": result.engineering_expectation.value,
        "semantic_status": result.semantic_status,
        "final_action": result.final_action,
        "reason": result.reason,
        "semantic_input_sha256": result.semantic_input_sha256,
        "latency_ms": result.latency_ms,
        "provider": result.provider,
        "model_id": result.model_id,
        "run_at": result.run_at.isoformat().replace("+00:00", "Z"),
        "engineering_expectation_match": result.engineering_expectation_match,
    }


def require_engineering_artifact_path(
    path: Path,
    *,
    repository_root: Path,
) -> Path:
    """Reject any result or cache path located under ``benchmark/``."""

    if not isinstance(path, Path) or not isinstance(repository_root, Path):
        raise TypeError("path and repository_root must be pathlib.Path")
    resolved = path.resolve()
    benchmark_root = (repository_root / "benchmark").resolve()
    if resolved == benchmark_root or benchmark_root in resolved.parents:
        raise SemanticMvpLiveError(
            "engineering artifacts must not be written under benchmark/"
        )
    return resolved


def write_live_results(
    results: Iterable[EngineeringLiveResult],
    output_path: Path,
    *,
    repository_root: Path,
) -> Path:
    """Write exclusive engineering JSONL outside every benchmark directory."""

    if not isinstance(output_path, Path) or not isinstance(repository_root, Path):
        raise TypeError("output_path and repository_root must be pathlib.Path")
    require_engineering_artifact_path(
        output_path,
        repository_root=repository_root,
    )
    values = tuple(results)
    if not values:
        raise SemanticMvpLiveError("at least one live result is required")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8", newline="\n") as stream:
            for result in values:
                stream.write(canonical_json_text(live_result_record(result)) + "\n")
    except OSError as error:
        raise SemanticMvpLiveError(
            f"cannot create engineering result artifact {output_path}"
        ) from error
    return output_path
