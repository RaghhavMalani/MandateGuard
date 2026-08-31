"""Strict INT-3 sufficiency dataset structure.

A row carries three strictly separated regions:

* ``provenance`` -- identity and frozen reference facts, never model input;
* ``features``   -- the complete diagnostic pre-inference mapping;
* ``decision_stable`` -- the engineering target, null until live subset
  execution supplies an observed subset action.

``decision_stable`` is defined only as::

    decision_stable = subset_final_action == full_reference_action

It is never derived from an engineering expectation label.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from mandateguard.engineering.int3.features import (
    FEATURE_NAMES,
    assert_no_target_leakage,
    feature_vector,
)
from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    REFERENCE_ACTIONS,
)
from mandateguard.engineering.int3.model_manifest import (
    MODEL_FEATURE_NAMES,
    model_feature_vector,
)


PROVENANCE_COLUMNS: tuple[str, ...] = (
    "observation_id",
    "query_id",
    "subset_mask",
    "subset_size",
    "eligible_size",
    "subset_evidence_ids",
)

TARGET_COLUMN = "decision_stable"


def decision_stable(
    *, subset_final_action: str, full_reference_action: str
) -> bool:
    """Define evidence sufficiency as stability against the frozen reference."""

    for value, name in (
        (subset_final_action, "subset_final_action"),
        (full_reference_action, "full_reference_action"),
    ):
        if value not in REFERENCE_ACTIONS:
            raise Int3ExperimentError(f"{name} must be one of {REFERENCE_ACTIONS}")
    return subset_final_action == full_reference_action


@dataclass(frozen=True, slots=True)
class SufficiencyDatasetRow:
    """One subset observation as a model-ready row."""

    observation_id: str
    query_id: str
    subset_mask: str
    subset_size: int
    eligible_size: int
    subset_evidence_ids: tuple[str, ...]
    features: Mapping[str, float]
    decision_stable: bool | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.observation_id, "observation_id"),
            (self.query_id, "query_id"),
            (self.subset_mask, "subset_mask"),
        ):
            if not isinstance(value, str) or not value:
                raise Int3ExperimentError(f"{name} must be a non-empty string")
        if set(self.subset_mask) - {"0", "1"}:
            raise Int3ExperimentError("subset_mask must be a bitmask")
        for value, name in (
            (self.subset_size, "subset_size"),
            (self.eligible_size, "eligible_size"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise Int3ExperimentError(f"{name} must be a positive integer")
        if self.subset_size > self.eligible_size:
            raise Int3ExperimentError("subset_size cannot exceed eligible_size")
        if len(self.subset_mask) != self.eligible_size:
            raise Int3ExperimentError("subset_mask must match eligible_size")
        if self.subset_mask.count("1") != self.subset_size:
            raise Int3ExperimentError("subset_mask must match subset_size")
        if not isinstance(self.subset_evidence_ids, tuple) or not self.subset_evidence_ids:
            raise Int3ExperimentError("subset_evidence_ids must be non-empty")
        if len(self.subset_evidence_ids) != self.subset_size:
            raise Int3ExperimentError("subset_evidence_ids must match subset_size")
        if len(self.subset_evidence_ids) != len(set(self.subset_evidence_ids)):
            raise Int3ExperimentError("subset_evidence_ids must be unique")
        if not isinstance(self.features, Mapping):
            raise Int3ExperimentError("features must be a mapping")
        if frozenset(self.features) != frozenset(FEATURE_NAMES):
            raise Int3ExperimentError("features must cover exactly FEATURE_NAMES")
        for name in FEATURE_NAMES:
            value = self.features[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise Int3ExperimentError(f"feature {name!r} must be finite")
        if self.decision_stable is not None and not isinstance(
            self.decision_stable, bool
        ):
            raise Int3ExperimentError("decision_stable must be boolean or null")
        object.__setattr__(
            self,
            "features",
            MappingProxyType(
                {name: float(self.features[name]) for name in FEATURE_NAMES}
            ),
        )

    @property
    def is_labeled(self) -> bool:
        return self.decision_stable is not None

    @property
    def vector(self) -> tuple[float, ...]:
        """Return all diagnostic features in diagnostic manifest order."""

        return feature_vector(self.features)

    @property
    def model_vector(self) -> tuple[float, ...]:
        """Return only preregistered runtime-deployable model features."""

        return model_feature_vector(self.features)

    def with_label(self, value: bool) -> SufficiencyDatasetRow:
        """Return a labeled copy; the plan's own rows stay unlabeled."""

        if not isinstance(value, bool):
            raise Int3ExperimentError("decision_stable must be boolean")
        return SufficiencyDatasetRow(
            observation_id=self.observation_id,
            query_id=self.query_id,
            subset_mask=self.subset_mask,
            subset_size=self.subset_size,
            eligible_size=self.eligible_size,
            subset_evidence_ids=self.subset_evidence_ids,
            features=self.features,
            decision_stable=value,
        )


@dataclass(frozen=True, slots=True)
class SufficiencyDataset:
    """An ordered, query-tagged collection of sufficiency rows."""

    feature_names: tuple[str, ...]
    rows: tuple[SufficiencyDatasetRow, ...]

    def __post_init__(self) -> None:
        assert_no_target_leakage(self.feature_names)
        if self.feature_names != MODEL_FEATURE_NAMES:
            raise Int3ExperimentError(
                "feature_names must be the frozen MODEL_FEATURE_NAMES"
            )
        if not isinstance(self.rows, tuple) or not self.rows:
            raise Int3ExperimentError("rows must be a non-empty tuple")
        if not all(isinstance(item, SufficiencyDatasetRow) for item in self.rows):
            raise Int3ExperimentError("rows contains an invalid record")
        identifiers = [item.observation_id for item in self.rows]
        if len(identifiers) != len(set(identifiers)):
            raise Int3ExperimentError("dataset observation IDs must be unique")

    @property
    def query_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for row in self.rows:
            if row.query_id not in seen:
                seen.append(row.query_id)
        return tuple(seen)

    @property
    def labeled_rows(self) -> tuple[SufficiencyDatasetRow, ...]:
        return tuple(item for item in self.rows if item.is_labeled)

    @property
    def unlabeled_rows(self) -> tuple[SufficiencyDatasetRow, ...]:
        return tuple(item for item in self.rows if not item.is_labeled)

    @property
    def is_fully_labeled(self) -> bool:
        return not self.unlabeled_rows

    def feature_matrix(self) -> tuple[tuple[float, ...], ...]:
        """Return the preregistered deployable model matrix."""

        return self.model_feature_matrix()

    def model_feature_matrix(self) -> tuple[tuple[float, ...], ...]:
        return tuple(item.model_vector for item in self.rows)

    def diagnostic_feature_matrix(self) -> tuple[tuple[float, ...], ...]:
        return tuple(item.vector for item in self.rows)

    @property
    def model_feature_names(self) -> tuple[str, ...]:
        return self.feature_names

    @property
    def diagnostic_feature_names(self) -> tuple[str, ...]:
        return FEATURE_NAMES

    def targets(self) -> tuple[bool, ...]:
        if not self.is_fully_labeled:
            raise Int3ExperimentError(
                "targets are unavailable until every row carries decision_stable"
            )
        return tuple(bool(item.decision_stable) for item in self.rows)

    def subset(
        self, indices: tuple[int, ...]
    ) -> tuple[SufficiencyDatasetRow, ...]:
        if not isinstance(indices, tuple):
            raise TypeError("indices must be a tuple")
        try:
            return tuple(self.rows[index] for index in indices)
        except (IndexError, TypeError) as error:
            raise Int3ExperimentError("dataset index is out of range") from error


def build_dataset(rows: Iterable[SufficiencyDatasetRow]) -> SufficiencyDataset:
    """Build the strict dataset from already-validated rows."""

    return SufficiencyDataset(
        feature_names=MODEL_FEATURE_NAMES,
        rows=tuple(rows),
    )


def dataset_csv_columns() -> tuple[str, ...]:
    """Return the exact, ordered sufficiency_dataset.csv header."""

    return (*PROVENANCE_COLUMNS, *FEATURE_NAMES, TARGET_COLUMN)


def write_sufficiency_dataset_csv(
    dataset: SufficiencyDataset, output_path: Path
) -> Path:
    """Exclusively create the dataset CSV; unlabeled targets are written empty."""

    if not isinstance(dataset, SufficiencyDataset):
        raise TypeError("dataset must be SufficiencyDataset")
    if not isinstance(output_path, Path):
        raise TypeError("output_path must be pathlib.Path")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = dataset_csv_columns()
    with output_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in dataset.rows:
            record: dict[str, object] = {
                "observation_id": row.observation_id,
                "query_id": row.query_id,
                "subset_mask": row.subset_mask,
                "subset_size": row.subset_size,
                "eligible_size": row.eligible_size,
                "subset_evidence_ids": "|".join(row.subset_evidence_ids),
                TARGET_COLUMN: (
                    "" if row.decision_stable is None else int(row.decision_stable)
                ),
            }
            record.update(
                {name: row.features[name] for name in FEATURE_NAMES}
            )
            writer.writerow(record)
    return output_path
