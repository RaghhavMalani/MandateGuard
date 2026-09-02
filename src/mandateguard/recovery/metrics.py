"""One versioned metric schema for the Resolve engineering evaluation.

The preregistration manifest and the runner must name the same metrics. Any
unknown or missing name is a schema violation and refuses the evaluation rather
than silently drifting, which is how `planner_direct_unsafe_allow_count` and
`planner_direct_allow_count` came apart.
"""

from __future__ import annotations

from collections.abc import Iterable


METRIC_SCHEMA_VERSION = "RESOLVE_METRIC_SCHEMA_V2"

#: Counters observed from real adapters and resources during a run. Every one is
#: incremented by an instrumented call site, never derived from `run.mode`.
OBSERVED_COUNTER_NAMES: tuple[str, ...] = (
    "openai_calls",
    "razorpay_http_calls",
    "offline_adapter_calls",
    "trusted_evidence_provider_calls",
    "acquisition_rounds",
    "new_evidence_items",
    "planner_direct_allow_count",
)

#: Metrics the evaluation summary emits. Names are exact and versioned.
EVALUATION_METRIC_NAMES: tuple[str, ...] = (
    "initial_review_count",
    "resolved_after_bounded_acquisition",
    "resolved_to_allow",
    "resolved_to_block",
    "still_review",
    "mean_additional_trusted_evidence_items",
    "max_acquisition_rounds",
    "payment_provider_calls_before_final_allow",
    "planner_direct_allow_count",
    "trusted_evidence_provider_calls_before_allow",
    "new_evidence_items",
    "synthetic_transaction_value_released_from_review_minor",
)

#: External-call counters that must be zero for an offline evaluation. A single
#: unexpected call fails the run instead of being assumed away.
EXTERNAL_CALL_COUNTER_NAMES: tuple[str, ...] = (
    "openai_calls",
    "razorpay_http_calls",
    "network_calls",
)


class MetricSchemaError(RuntimeError):
    """A manifest or runner named a metric outside the versioned schema."""


def _report(kind: str, names: Iterable[str]) -> str:
    return f"{kind}: " + ", ".join(sorted(names))


def validate_metric_names(
    planned: Iterable[str], *, emitted: Iterable[str], context: str
) -> None:
    """Require the planned and emitted metric name sets to match the schema."""

    planned_names = set(planned)
    emitted_names = set(emitted)
    schema = set(EVALUATION_METRIC_NAMES)
    problems: list[str] = []
    if planned_names - schema:
        problems.append(_report("unknown planned metrics", planned_names - schema))
    if schema - planned_names:
        problems.append(_report("missing planned metrics", schema - planned_names))
    if emitted_names - schema:
        problems.append(_report("unknown emitted metrics", emitted_names - schema))
    if schema - emitted_names:
        problems.append(_report("missing emitted metrics", schema - emitted_names))
    if problems:
        raise MetricSchemaError(
            f"{context} does not match {METRIC_SCHEMA_VERSION}; " + "; ".join(problems)
        )


def validate_observed_counters(counters: Iterable[str], *, context: str) -> None:
    """Require the observed counter set to match the versioned schema exactly."""

    observed = set(counters)
    schema = set(OBSERVED_COUNTER_NAMES)
    problems: list[str] = []
    if observed - schema:
        problems.append(_report("unknown observed counters", observed - schema))
    if schema - observed:
        problems.append(_report("missing observed counters", schema - observed))
    if problems:
        raise MetricSchemaError(
            f"{context} does not match {METRIC_SCHEMA_VERSION}; " + "; ".join(problems)
        )
