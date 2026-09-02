"""Preregistered observed metrics for the 20-case Resolve evaluation.

The shared schema in :mod:`mandateguard.recovery.metrics` stays untouched: it is
already frozen for the product-parity evaluator, and widening it there would
silently change what the product's own counter validation accepts. This module
declares the evaluation's preregistered observed-metric names on top of that
schema and refuses any set that drifts from it.

Every runtime counter name in ``OBSERVED_COUNTER_NAMES`` also appears here, so
no observed counter can be inferred from a mode string instead of read from an
instrumented call site.
"""

from __future__ import annotations

from collections.abc import Iterable

from mandateguard.recovery.metrics import (
    EVALUATION_METRIC_NAMES,
    METRIC_SCHEMA_VERSION,
    OBSERVED_COUNTER_NAMES,
    MetricSchemaError,
)


#: Metrics observed for the 20-case evaluation. Outcome counts, real adapter
#: counters, and refusal counters, each incremented by an actual call site or
#: derived from a recorded per-case outcome.
PREREGISTERED_OBSERVED_METRIC_NAMES: tuple[str, ...] = (
    "initial_review_count",
    "resolved_count",
    "review_to_allow_count",
    "review_to_block_count",
    "review_to_review_count",
    "trusted_evidence_provider_calls",
    "provider_calls_before_final_allow",
    "offline_adapter_calls",
    "razorpay_http_calls",
    "openai_calls",
    "acquisition_rounds",
    "new_evidence_items",
    "planner_direct_allow_count",
    "budget_exhaustion_count",
    "authority_conflict_count",
    "source_incomplete_count",
    "binding_rejection_count",
    "expired_recovery_count",
    "replay_rejection_count",
)

#: What each preregistered name counts. Frozen with the names so a later run
#: cannot redefine an ambiguous metric into a more flattering one.
PREREGISTERED_OBSERVED_METRIC_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("initial_review_count", "Cases whose initial controller action was REVIEW."),
    (
        "resolved_count",
        "Cases whose final controller action was not REVIEW after bounded "
        "acquisition.",
    ),
    ("review_to_allow_count", "Cases that moved from REVIEW to ALLOW."),
    ("review_to_block_count", "Cases that moved from REVIEW to BLOCK."),
    ("review_to_review_count", "Cases that remained REVIEW."),
    (
        "trusted_evidence_provider_calls",
        "Trusted-evidence provider fetches counted by the instrumented "
        "provider proxy, including fetches that failed.",
    ),
    (
        "provider_calls_before_final_allow",
        "Payment-provider execution calls observed before a case reached its "
        "fresh final ALLOW. Any non-zero value violates S7.",
    ),
    (
        "offline_adapter_calls",
        "Calls to the network-free payment execution double, all of which may "
        "occur only after a fresh final ALLOW.",
    ),
    ("razorpay_http_calls", "Real Razorpay HTTP calls. Must be zero."),
    ("openai_calls", "Real OpenAI calls. Must be zero."),
    (
        "acquisition_rounds",
        "Acquisition rounds actually reserved, including rounds consumed by a "
        "failed provider fetch.",
    ),
    (
        "new_evidence_items",
        "Trusted evidence records added to an authorization evidence set by "
        "recovery.",
    ),
    (
        "planner_direct_allow_count",
        "Times the evidence-gap planner emitted an authorization action "
        "directly. Any non-zero value violates S11.",
    ),
    (
        "budget_exhaustion_count",
        "Acquisition attempts refused because the applicable authoritative "
        "record set exceeds the evidence-item budget.",
    ),
    (
        "authority_conflict_count",
        "Acquisition attempts refused for an unresolved authority conflict.",
    ),
    (
        "source_incomplete_count",
        "Acquisition attempts refused because a source returned less than its "
        "complete manifested record set.",
    ),
    (
        "binding_rejection_count",
        "Evidence rejected for wrong merchant or wrong SKU binding, whether "
        "reached through acquisition or a direct binding probe.",
    ),
    (
        "expired_recovery_count",
        "Acquisition attempts refused because the selected source or its "
        "records were expired, not yet effective, or superseded.",
    ),
    (
        "replay_rejection_count",
        "Recovered capabilities whose replay was rejected without reaching a "
        "network.",
    ),
)


def validate_preregistered_observed_metrics(
    names: Iterable[str], *, context: str
) -> None:
    """Require the preregistered observed-metric set to match exactly."""

    observed = tuple(names)
    if observed != PREREGISTERED_OBSERVED_METRIC_NAMES:
        raise MetricSchemaError(
            f"{context} does not match the {METRIC_SCHEMA_VERSION} preregistered "
            "observed metric names in order"
        )
    missing = set(OBSERVED_COUNTER_NAMES) - set(observed)
    if missing:
        raise MetricSchemaError(
            f"{context} omits runtime observed counters: " + ", ".join(sorted(missing))
        )


__all__ = [
    "EVALUATION_METRIC_NAMES",
    "METRIC_SCHEMA_VERSION",
    "OBSERVED_COUNTER_NAMES",
    "PREREGISTERED_OBSERVED_METRIC_DEFINITIONS",
    "PREREGISTERED_OBSERVED_METRIC_NAMES",
    "MetricSchemaError",
    "validate_preregistered_observed_metrics",
]
