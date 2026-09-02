"""Preregistration for the 20-case MandateGuard Resolve recovery evaluation.

This package holds only fixture loading, structural validation, and the freeze
gate. It contains no authorization logic: every decision in the evaluation comes
from the existing frozen controller and the existing bounded recovery
orchestration.
"""

from mandateguard.engineering.resolve_eval.metrics import (
    PREREGISTERED_OBSERVED_METRIC_DEFINITIONS,
    PREREGISTERED_OBSERVED_METRIC_NAMES,
    validate_preregistered_observed_metrics,
)
from mandateguard.engineering.resolve_eval.worlds import (
    EXPECTED_CASE_COUNT,
    FIXTURE_ROOT,
    WORLD_SCHEMA,
    BindingProbe,
    ProviderFault,
    ResolveCaseWorld,
    WorldFixtureError,
    build_registry,
    load_world,
    load_worlds,
    read_strict_json,
)

__all__ = [
    "EXPECTED_CASE_COUNT",
    "FIXTURE_ROOT",
    "PREREGISTERED_OBSERVED_METRIC_DEFINITIONS",
    "PREREGISTERED_OBSERVED_METRIC_NAMES",
    "WORLD_SCHEMA",
    "BindingProbe",
    "ProviderFault",
    "ResolveCaseWorld",
    "WorldFixtureError",
    "build_registry",
    "load_world",
    "load_worlds",
    "read_strict_json",
    "validate_preregistered_observed_metrics",
]
