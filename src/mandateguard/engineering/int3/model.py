"""Interpretable L2-regularized logistic-regression sufficiency baseline.

Fitting delegates to scikit-learn's L2 ``LogisticRegression``.  Inference does
not: a fitted model stores its intercept and one coefficient per frozen
feature, and ``predict_proba`` evaluates the logistic link directly.  That
keeps the deployed decision path auditable, keeps scikit-learn out of the
prediction dependency chain, and lets the offline demo and the tests construct
an explicit model from synthetic coefficients without any training data.

No model is trained in INT-3A: live subset labels do not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from mandateguard.engineering.int3.features import (
    FEATURE_NAMES,
    assert_no_target_leakage,
)
from mandateguard.engineering.int3.models import Int3ExperimentError, probability


DEFAULT_L2_INVERSE_STRENGTH = 1.0
DEFAULT_MAX_ITERATIONS = 1000
DEFAULT_SUFFICIENCY_THRESHOLD = 0.5


class SufficiencyModelNotFittedError(Int3ExperimentError):
    """predict/predict_proba was called before the model had coefficients."""


class SufficiencyModelUnavailableError(Int3ExperimentError):
    """scikit-learn is required to fit, and is not installed."""


class SufficiencyTrainingDataError(Int3ExperimentError):
    """The supplied training matrix or target vector cannot be fitted."""


def _matrix(value: object, name: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise SufficiencyTrainingDataError(f"{name} must be a non-empty sequence")
    rows: list[tuple[float, ...]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != len(FEATURE_NAMES):
            raise SufficiencyTrainingDataError(
                f"{name} rows must have exactly {len(FEATURE_NAMES)} features"
            )
        parsed: list[float] = []
        for item in row:
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
            ):
                raise SufficiencyTrainingDataError(
                    f"{name} must contain finite numbers"
                )
            parsed.append(float(item))
        rows.append(tuple(parsed))
    return tuple(rows)


def _targets(value: object, name: str, expected: int) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != expected:
        raise SufficiencyTrainingDataError(
            f"{name} must align one-to-one with the feature matrix"
        )
    parsed: list[int] = []
    for item in value:
        if isinstance(item, bool):
            parsed.append(int(item))
            continue
        if item in (0, 1) and isinstance(item, int):
            parsed.append(int(item))
            continue
        raise SufficiencyTrainingDataError(f"{name} must contain booleans or 0/1")
    return tuple(parsed)


def _sigmoid(value: float) -> float:
    # Split by sign so neither branch can overflow math.exp.
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    scaled = math.exp(value)
    return scaled / (1.0 + scaled)


@dataclass(frozen=True, slots=True)
class SufficiencyModel:
    """A frozen, fully specified logistic sufficiency model.

    ``coefficients`` and ``intercept`` are null until the model is fitted.  A
    fitted instance is immutable; ``fit`` returns a new instance rather than
    mutating the caller's model.
    """

    feature_names: tuple[str, ...] = FEATURE_NAMES
    coefficients: tuple[float, ...] | None = None
    intercept: float | None = None
    l2_inverse_regularization_strength: float = DEFAULT_L2_INVERSE_STRENGTH
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    training_row_count: int | None = None

    def __post_init__(self) -> None:
        assert_no_target_leakage(self.feature_names)
        if self.feature_names != FEATURE_NAMES:
            raise Int3ExperimentError("feature_names must be the frozen FEATURE_NAMES")
        if (
            isinstance(self.l2_inverse_regularization_strength, bool)
            or not isinstance(
                self.l2_inverse_regularization_strength, (int, float)
            )
            or not math.isfinite(float(self.l2_inverse_regularization_strength))
            or float(self.l2_inverse_regularization_strength) <= 0.0
        ):
            raise Int3ExperimentError(
                "l2_inverse_regularization_strength must be a positive number"
            )
        if (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or self.max_iterations < 1
        ):
            raise Int3ExperimentError("max_iterations must be a positive integer")
        if (self.coefficients is None) != (self.intercept is None):
            raise Int3ExperimentError(
                "coefficients and intercept must be set together"
            )
        if self.coefficients is not None:
            if not isinstance(self.coefficients, tuple) or len(
                self.coefficients
            ) != len(FEATURE_NAMES):
                raise Int3ExperimentError(
                    "coefficients must supply one weight per frozen feature"
                )
            if not all(
                not isinstance(item, bool)
                and isinstance(item, (int, float))
                and math.isfinite(float(item))
                for item in self.coefficients
            ):
                raise Int3ExperimentError("coefficients must be finite numbers")
            object.__setattr__(
                self,
                "coefficients",
                tuple(float(item) for item in self.coefficients),
            )
            if (
                isinstance(self.intercept, bool)
                or not isinstance(self.intercept, (int, float))
                or not math.isfinite(float(self.intercept))
            ):
                raise Int3ExperimentError("intercept must be a finite number")
            object.__setattr__(self, "intercept", float(self.intercept))
        if self.training_row_count is not None and (
            isinstance(self.training_row_count, bool)
            or not isinstance(self.training_row_count, int)
            or self.training_row_count < 1
        ):
            raise Int3ExperimentError(
                "training_row_count must be a positive integer or null"
            )

    @property
    def is_fitted(self) -> bool:
        return self.coefficients is not None

    @classmethod
    def from_coefficients(
        cls,
        *,
        coefficients: Sequence[float],
        intercept: float,
        l2_inverse_regularization_strength: float = DEFAULT_L2_INVERSE_STRENGTH,
    ) -> SufficiencyModel:
        """Build an explicit model without training, for demos and tests."""

        return cls(
            feature_names=FEATURE_NAMES,
            coefficients=tuple(coefficients),
            intercept=intercept,
            l2_inverse_regularization_strength=l2_inverse_regularization_strength,
        )

    def weights(self) -> dict[str, float]:
        """Return the interpretable per-feature weight mapping."""

        if self.coefficients is None:
            raise SufficiencyModelNotFittedError(
                "an unfitted sufficiency model has no weights"
            )
        return dict(zip(FEATURE_NAMES, self.coefficients, strict=True))

    def fit(
        self,
        feature_matrix: Sequence[Sequence[float]],
        targets: Sequence[bool],
    ) -> SufficiencyModel:
        """Fit L2 logistic regression and return a new fitted model.

        INT-3A never calls this on real data: ``decision_stable`` labels do not
        exist until subsets are executed live.
        """

        rows = _matrix(feature_matrix, "feature_matrix")
        labels = _targets(targets, "targets", len(rows))
        if len(set(labels)) < 2:
            raise SufficiencyTrainingDataError(
                "logistic regression requires both sufficiency classes"
            )
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError as error:  # pragma: no cover - environment dependent
            raise SufficiencyModelUnavailableError(
                "fitting the sufficiency model requires the optional "
                "scikit-learn engineering dependency"
            ) from error
        estimator = LogisticRegression(
            # scikit-learn 1.8+ expresses pure L2 as l1_ratio=0; leaving the
            # deprecated ``penalty`` argument unset keeps this forward-safe.
            l1_ratio=0.0,
            C=float(self.l2_inverse_regularization_strength),
            solver="lbfgs",
            max_iter=int(self.max_iterations),
            fit_intercept=True,
        )
        estimator.fit([list(row) for row in rows], list(labels))
        coefficients = tuple(float(item) for item in estimator.coef_[0])
        intercept = float(estimator.intercept_[0])
        return SufficiencyModel(
            feature_names=FEATURE_NAMES,
            coefficients=coefficients,
            intercept=intercept,
            l2_inverse_regularization_strength=(
                self.l2_inverse_regularization_strength
            ),
            max_iterations=self.max_iterations,
            training_row_count=len(rows),
        )

    def predict_proba(
        self, feature_matrix: Sequence[Sequence[float]]
    ) -> tuple[float, ...]:
        """Return P(decision_stable = True) for each row, in row order."""

        if self.coefficients is None or self.intercept is None:
            raise SufficiencyModelNotFittedError(
                "predict_proba requires a fitted sufficiency model"
            )
        rows = _matrix(feature_matrix, "feature_matrix")
        coefficients = self.coefficients
        intercept = self.intercept
        return tuple(
            _sigmoid(
                intercept
                + sum(
                    weight * value
                    for weight, value in zip(coefficients, row, strict=True)
                )
            )
            for row in rows
        )

    def predict(
        self,
        feature_matrix: Sequence[Sequence[float]],
        *,
        threshold: float = DEFAULT_SUFFICIENCY_THRESHOLD,
    ) -> tuple[bool, ...]:
        """Return the hard sufficiency label at an explicit threshold.

        The threshold is a caller-supplied argument, not a hidden constant.
        Production control should use the expected-loss controller instead of a
        bare threshold.
        """

        probability(threshold, "threshold")
        return tuple(
            value >= float(threshold) for value in self.predict_proba(feature_matrix)
        )
