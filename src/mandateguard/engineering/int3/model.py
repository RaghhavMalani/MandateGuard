"""Preregistered StandardScaler + L2 logistic sufficiency baseline.

The 14-dimensional runtime feature manifest and all pipeline hyperparameters
are frozen before INT-3 subset labels exist. Fitting delegates to a
scikit-learn Pipeline. A fitted immutable value stores the scaler statistics
and standardized-space logistic coefficients so inference remains auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from mandateguard.engineering.int3.features import assert_no_target_leakage
from mandateguard.engineering.int3.model_manifest import (
    MODEL_FEATURE_NAMES,
    MODEL_PIPELINE_SPEC,
)
from mandateguard.engineering.int3.models import Int3ExperimentError, probability


DEFAULT_L2_INVERSE_STRENGTH = 1.0
DEFAULT_MAX_ITERATIONS = 2000
DEFAULT_SOLVER = "lbfgs"
DEFAULT_RANDOM_STATE = 0
DEFAULT_TOLERANCE = 0.0001
DEFAULT_SUFFICIENCY_THRESHOLD = 0.5


class SufficiencyModelNotFittedError(Int3ExperimentError):
    """predict/predict_proba was called before the pipeline was fitted."""


class SufficiencyModelUnavailableError(Int3ExperimentError):
    """scikit-learn is required to fit, and is not installed."""


class SufficiencyTrainingDataError(Int3ExperimentError):
    """The supplied training matrix or target vector cannot be fitted."""


def _matrix(value: object, name: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise SufficiencyTrainingDataError(f"{name} must be a non-empty sequence")
    rows: list[tuple[float, ...]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != len(
            MODEL_FEATURE_NAMES
        ):
            raise SufficiencyTrainingDataError(
                f"{name} rows must have exactly {len(MODEL_FEATURE_NAMES)} features"
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
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    scaled = math.exp(value)
    return scaled / (1.0 + scaled)


def _finite_vector(
    value: tuple[float, ...] | None,
    *,
    name: str,
    positive: bool,
) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != len(MODEL_FEATURE_NAMES):
        raise Int3ExperimentError(
            f"{name} must supply one value per preregistered model feature"
        )
    parsed: list[float] = []
    for item in value:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or (positive and float(item) <= 0.0)
        ):
            qualifier = "positive finite" if positive else "finite"
            raise Int3ExperimentError(f"{name} must contain {qualifier} numbers")
        parsed.append(float(item))
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class SufficiencyModel:
    """Immutable preregistered pipeline state.

    Hyperparameters are fields for audit visibility, but construction rejects
    any value that differs from the preregistered manifest.
    """

    feature_names: tuple[str, ...] = MODEL_FEATURE_NAMES
    coefficients: tuple[float, ...] | None = None
    intercept: float | None = None
    scaler_mean: tuple[float, ...] | None = None
    scaler_scale: tuple[float, ...] | None = None
    l2_inverse_regularization_strength: float = DEFAULT_L2_INVERSE_STRENGTH
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    solver: str = DEFAULT_SOLVER
    random_state: int = DEFAULT_RANDOM_STATE
    tolerance: float = DEFAULT_TOLERANCE
    class_weight: None = None
    training_row_count: int | None = None

    def __post_init__(self) -> None:
        assert_no_target_leakage(self.feature_names)
        if self.feature_names != MODEL_FEATURE_NAMES:
            raise Int3ExperimentError(
                "feature_names must be the frozen MODEL_FEATURE_NAMES"
            )
        frozen_hyperparameters = (
            float(self.l2_inverse_regularization_strength) == 1.0
            and self.max_iterations == 2000
            and self.solver == "lbfgs"
            and self.random_state == 0
            and float(self.tolerance) == 0.0001
            and self.class_weight is None
        )
        if not frozen_hyperparameters:
            raise Int3ExperimentError(
                "model hyperparameters differ from the preregistered pipeline"
            )
        state = (
            self.coefficients,
            self.intercept,
            self.scaler_mean,
            self.scaler_scale,
        )
        if any(item is None for item in state) and not all(
            item is None for item in state
        ):
            raise Int3ExperimentError(
                "coefficients, intercept, scaler_mean, and scaler_scale must be set together"
            )
        if self.coefficients is not None:
            coefficients = _finite_vector(
                self.coefficients, name="coefficients", positive=False
            )
            mean = _finite_vector(
                self.scaler_mean, name="scaler_mean", positive=False
            )
            scale = _finite_vector(
                self.scaler_scale, name="scaler_scale", positive=True
            )
            if (
                isinstance(self.intercept, bool)
                or not isinstance(self.intercept, (int, float))
                or not math.isfinite(float(self.intercept))
            ):
                raise Int3ExperimentError("intercept must be a finite number")
            object.__setattr__(self, "coefficients", coefficients)
            object.__setattr__(self, "intercept", float(self.intercept))
            object.__setattr__(self, "scaler_mean", mean)
            object.__setattr__(self, "scaler_scale", scale)
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

    @property
    def pipeline_spec(self) -> dict[str, object]:
        return {
            "steps": tuple(MODEL_PIPELINE_SPEC["steps"]),
            "standard_scaler": dict(MODEL_PIPELINE_SPEC["standard_scaler"]),
            "logistic_regression": dict(
                MODEL_PIPELINE_SPEC["logistic_regression"]
            ),
            "hyperparameter_tuning_after_labels": False,
        }

    @classmethod
    def from_coefficients(
        cls,
        *,
        coefficients: Sequence[float],
        intercept: float,
        scaler_mean: Sequence[float] | None = None,
        scaler_scale: Sequence[float] | None = None,
    ) -> SufficiencyModel:
        """Build explicit synthetic fitted state without training."""

        mean = (
            tuple(0.0 for _ in MODEL_FEATURE_NAMES)
            if scaler_mean is None
            else tuple(scaler_mean)
        )
        scale = (
            tuple(1.0 for _ in MODEL_FEATURE_NAMES)
            if scaler_scale is None
            else tuple(scaler_scale)
        )
        return cls(
            coefficients=tuple(coefficients),
            intercept=intercept,
            scaler_mean=mean,
            scaler_scale=scale,
        )

    def weights(self) -> dict[str, float]:
        """Return standardized-space per-feature logistic coefficients."""

        if self.coefficients is None:
            raise SufficiencyModelNotFittedError(
                "an unfitted sufficiency model has no weights"
            )
        return dict(zip(MODEL_FEATURE_NAMES, self.coefficients, strict=True))

    def fit(
        self,
        feature_matrix: Sequence[Sequence[float]],
        targets: Sequence[bool],
    ) -> SufficiencyModel:
        """Fit the frozen pipeline and return new immutable fitted state."""

        rows = _matrix(feature_matrix, "feature_matrix")
        labels = _targets(targets, "targets", len(rows))
        if len(set(labels)) < 2:
            raise SufficiencyTrainingDataError(
                "logistic regression requires both sufficiency classes"
            )
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError as error:  # pragma: no cover - environment dependent
            raise SufficiencyModelUnavailableError(
                "fitting the sufficiency model requires scikit-learn"
            ) from error
        pipeline = Pipeline(
            steps=(
                ("standard_scaler", StandardScaler(with_mean=True, with_std=True)),
                (
                    "logistic_regression",
                    LogisticRegression(
                        l1_ratio=0.0,
                        C=1.0,
                        solver="lbfgs",
                        max_iter=2000,
                        fit_intercept=True,
                        random_state=0,
                        class_weight=None,
                        tol=0.0001,
                    ),
                ),
            )
        )
        pipeline.fit([list(row) for row in rows], list(labels))
        scaler = pipeline.named_steps["standard_scaler"]
        estimator = pipeline.named_steps["logistic_regression"]
        return SufficiencyModel(
            coefficients=tuple(float(item) for item in estimator.coef_[0]),
            intercept=float(estimator.intercept_[0]),
            scaler_mean=tuple(float(item) for item in scaler.mean_),
            scaler_scale=tuple(float(item) for item in scaler.scale_),
            training_row_count=len(rows),
        )

    def predict_proba(
        self, feature_matrix: Sequence[Sequence[float]]
    ) -> tuple[float, ...]:
        """Return P(single-execution action stability) for each raw row."""

        if (
            self.coefficients is None
            or self.intercept is None
            or self.scaler_mean is None
            or self.scaler_scale is None
        ):
            raise SufficiencyModelNotFittedError(
                "predict_proba requires a fitted sufficiency pipeline"
            )
        rows = _matrix(feature_matrix, "feature_matrix")
        return tuple(
            _sigmoid(
                self.intercept
                + sum(
                    weight * ((value - mean) / scale)
                    for weight, value, mean, scale in zip(
                        self.coefficients,
                        row,
                        self.scaler_mean,
                        self.scaler_scale,
                        strict=True,
                    )
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
        probability(threshold, "threshold")
        return tuple(
            value >= float(threshold) for value in self.predict_proba(feature_matrix)
        )
