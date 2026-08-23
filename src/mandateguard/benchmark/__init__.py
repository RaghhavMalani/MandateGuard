"""D7 deterministic Tier A/B benchmark corpus generation.

This package generates, labels, and hashes the registered 1,008-case Tier A/B
benchmark corpus. It never executes it: no module here imports
``mandateguard.policy``, ``mandateguard.semantic``, ``mandateguard.execution``,
or ``mandateguard.replay``, and every generated case carries
``first_run_at: null``.
"""

from mandateguard.benchmark.models import (
    BENCHMARK_FAMILIES,
    CASE_SCHEMA_VERSION,
    GENERATOR_VERSION,
    BenchmarkCase,
    EvaluationInputs,
    GeneratorAudit,
    TargetExpectation,
)

__all__ = [
    "BENCHMARK_FAMILIES",
    "CASE_SCHEMA_VERSION",
    "GENERATOR_VERSION",
    "BenchmarkCase",
    "EvaluationInputs",
    "GeneratorAudit",
    "TargetExpectation",
]
