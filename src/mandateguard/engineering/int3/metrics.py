"""Evaluation metrics for a future INT-3 sufficiency model.

The safety-sensitive quantity is the **false-SUFFICIENT** count: the model said
a subset preserved the frozen full-evidence action, and it did not.  Those are
the cases where a learned gate would have waved through an evidence set that
changes the authorization outcome.

Generic accuracy is deliberately not computed.  On a six-query engineering
corpus with heavily imbalanced folds it would be the least informative number
available and the easiest to over-read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from mandateguard.engineering.int3.models import Int3ExperimentError, probability


def _aligned(
    probabilities: Sequence[float],
    targets: Sequence[bool],
) -> tuple[tuple[float, ...], tuple[bool, ...]]:
    if not isinstance(probabilities, (list, tuple)) or not probabilities:
        raise Int3ExperimentError("probabilities must be a non-empty sequence")
    if not isinstance(targets, (list, tuple)) or len(targets) != len(probabilities):
        raise Int3ExperimentError("targets must align with probabilities")
    parsed_probabilities = tuple(
        probability(item, "probability") for item in probabilities
    )
    parsed_targets: list[bool] = []
    for item in targets:
        if not isinstance(item, bool):
            raise Int3ExperimentError("targets must be booleans")
        parsed_targets.append(item)
    return parsed_probabilities, tuple(parsed_targets)


def brier_score(
    probabilities: Sequence[float], targets: Sequence[bool]
) -> float:
    """Mean squared error between predicted P(stable) and the observed label."""

    values, labels = _aligned(probabilities, targets)
    return sum(
        (value - (1.0 if label else 0.0)) ** 2
        for value, label in zip(values, labels, strict=True)
    ) / len(values)


def roc_auc(
    probabilities: Sequence[float], targets: Sequence[bool]
) -> float | None:
    """Rank-based ROC-AUC, or None when a fold has only one class.

    Ties contribute 0.5, matching the standard rank-sum definition.
    """

    values, labels = _aligned(probabilities, targets)
    positives = [
        value for value, label in zip(values, labels, strict=True) if label
    ]
    negatives = [
        value for value, label in zip(values, labels, strict=True) if not label
    ]
    if not positives or not negatives:
        return None
    concordant = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                concordant += 1.0
            elif positive == negative:
                concordant += 0.5
    return concordant / (len(positives) * len(negatives))


@dataclass(frozen=True, slots=True)
class SufficiencyMetrics:
    """Evaluation summary for one fold or one pooled evaluation.

    ``false_sufficient_rate`` is the unsafe share of predictions labeled
    sufficient.  The ``*_among_*`` rates are conditioned on the true class and
    the ``*_overall`` rates retain the whole-fold denominator.  Reporting all
    three denominators prevents a small or imbalanced fold from hiding risk.
    """

    observation_count: int
    stable_count: int
    unstable_count: int
    predicted_sufficient_count: int
    predicted_insufficient_count: int
    brier_score: float
    false_sufficient_count: int
    false_sufficient_rate: float
    false_sufficient_rate_among_unstable: float | None
    false_sufficient_rate_overall: float
    false_insufficient_count: int
    false_insufficient_rate: float
    false_insufficient_rate_among_stable: float | None
    false_insufficient_rate_overall: float
    review_escalation_count: int
    review_escalation_rate: float
    roc_auc: float | None

    def __post_init__(self) -> None:
        if self.observation_count < 1:
            raise Int3ExperimentError("observation_count must be positive")
        if self.stable_count + self.unstable_count != self.observation_count:
            raise Int3ExperimentError("class counts must sum to observation_count")
        if (
            self.predicted_sufficient_count + self.predicted_insufficient_count
            != self.observation_count
        ):
            raise Int3ExperimentError(
                "prediction counts must sum to observation_count"
            )
        if not 0 <= self.review_escalation_count <= self.observation_count:
            raise Int3ExperimentError(
                "review_escalation_count must be within observation_count"
            )
        for value, name in (
            (self.false_sufficient_rate, "false_sufficient_rate"),
            (self.false_sufficient_rate_overall, "false_sufficient_rate_overall"),
            (self.false_insufficient_rate, "false_insufficient_rate"),
            (self.false_insufficient_rate_overall, "false_insufficient_rate_overall"),
            (self.review_escalation_rate, "review_escalation_rate"),
        ):
            probability(value, name)
        for value, name in (
            (
                self.false_sufficient_rate_among_unstable,
                "false_sufficient_rate_among_unstable",
            ),
            (
                self.false_insufficient_rate_among_stable,
                "false_insufficient_rate_among_stable",
            ),
            (self.roc_auc, "roc_auc"),
        ):
            if value is not None:
                probability(value, name)
        if self.brier_score < 0.0 or self.brier_score > 1.0:
            raise Int3ExperimentError("brier_score must be within [0, 1]")


def evaluate_sufficiency(
    *,
    probabilities: Sequence[float],
    targets: Sequence[bool],
    predictions: Sequence[bool],
    escalations: Sequence[bool] | None = None,
) -> SufficiencyMetrics:
    """Score one evaluation without computing or reporting generic accuracy.

    ``predictions`` are the hard sufficiency labels the caller acted on.
    ``escalations`` optionally carries the controller's actual escalation
    decisions; when omitted, every predicted-insufficient row counts as an
    escalation.
    """

    values, labels = _aligned(probabilities, targets)
    if not isinstance(predictions, (list, tuple)) or len(predictions) != len(values):
        raise Int3ExperimentError("predictions must align with probabilities")
    parsed_predictions: list[bool] = []
    for item in predictions:
        if not isinstance(item, bool):
            raise Int3ExperimentError("predictions must be booleans")
        parsed_predictions.append(item)

    if escalations is None:
        parsed_escalations = tuple(not item for item in parsed_predictions)
    else:
        if not isinstance(escalations, (list, tuple)) or len(escalations) != len(
            values
        ):
            raise Int3ExperimentError("escalations must align with probabilities")
        for item in escalations:
            if not isinstance(item, bool):
                raise Int3ExperimentError("escalations must be booleans")
        parsed_escalations = tuple(escalations)

    total = len(values)
    stable = sum(1 for label in labels if label)
    unstable = total - stable
    predicted_sufficient = sum(1 for item in parsed_predictions if item)
    predicted_insufficient = total - predicted_sufficient
    false_sufficient = sum(
        1
        for predicted, label in zip(parsed_predictions, labels, strict=True)
        if predicted and not label
    )
    false_insufficient = sum(
        1
        for predicted, label in zip(parsed_predictions, labels, strict=True)
        if not predicted and label
    )
    escalation_count = sum(1 for item in parsed_escalations if item)
    return SufficiencyMetrics(
        observation_count=total,
        stable_count=stable,
        unstable_count=unstable,
        predicted_sufficient_count=predicted_sufficient,
        predicted_insufficient_count=predicted_insufficient,
        brier_score=brier_score(values, labels),
        false_sufficient_count=false_sufficient,
        false_sufficient_rate=(
            false_sufficient / predicted_sufficient
            if predicted_sufficient
            else 0.0
        ),
        false_sufficient_rate_among_unstable=(
            false_sufficient / unstable if unstable else None
        ),
        false_sufficient_rate_overall=false_sufficient / total,
        false_insufficient_count=false_insufficient,
        false_insufficient_rate=(
            false_insufficient / predicted_insufficient
            if predicted_insufficient
            else 0.0
        ),
        false_insufficient_rate_among_stable=(
            false_insufficient / stable if stable else None
        ),
        false_insufficient_rate_overall=false_insufficient / total,
        review_escalation_count=escalation_count,
        review_escalation_rate=escalation_count / total,
        roc_auc=roc_auc(values, labels),
    )
