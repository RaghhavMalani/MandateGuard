"""Leave-one-query-out splitting for the INT-3 sufficiency dataset.

Every subset of a query is derived from the same six frozen cases, the same
mandate, and the same evidence texts.  Randomly splitting subsets of one query
across train and test would leak that shared structure and inflate any reported
number.  The only supported protocol is therefore leave-one-query-out: a fold's
test set is *all* subsets of exactly one query, and its training set is every
subset of the remaining queries.
"""

from __future__ import annotations

from dataclasses import dataclass

from mandateguard.engineering.int3.dataset import (
    SufficiencyDataset,
    SufficiencyDatasetRow,
)
from mandateguard.engineering.int3.models import Int3ExperimentError


@dataclass(frozen=True, slots=True)
class LeaveOneQueryOutFold:
    """One fold: every subset of ``held_out_query_id`` is the test set."""

    fold_index: int
    held_out_query_id: str
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if isinstance(self.fold_index, bool) or not isinstance(
            self.fold_index, int
        ):
            raise Int3ExperimentError("fold_index must be an integer")
        if self.fold_index < 0:
            raise Int3ExperimentError("fold_index must be non-negative")
        if not isinstance(self.held_out_query_id, str) or not self.held_out_query_id:
            raise Int3ExperimentError("held_out_query_id must be non-empty")
        for values, name in (
            (self.train_indices, "train_indices"),
            (self.test_indices, "test_indices"),
        ):
            if not isinstance(values, tuple) or not values:
                raise Int3ExperimentError(f"{name} must be a non-empty tuple")
            if not all(
                isinstance(item, int) and not isinstance(item, bool) and item >= 0
                for item in values
            ):
                raise Int3ExperimentError(f"{name} must contain row indices")
            if len(values) != len(set(values)):
                raise Int3ExperimentError(f"{name} must be unique")
            if tuple(sorted(values)) != values:
                raise Int3ExperimentError(f"{name} must be ascending")
        if set(self.train_indices) & set(self.test_indices):
            raise Int3ExperimentError("train and test indices must be disjoint")

    def train_rows(
        self, dataset: SufficiencyDataset
    ) -> tuple[SufficiencyDatasetRow, ...]:
        return dataset.subset(self.train_indices)

    def test_rows(
        self, dataset: SufficiencyDataset
    ) -> tuple[SufficiencyDatasetRow, ...]:
        return dataset.subset(self.test_indices)


def leave_one_query_out_folds(
    dataset: SufficiencyDataset,
) -> tuple[LeaveOneQueryOutFold, ...]:
    """Build one fold per distinct query, in first-appearance order."""

    if not isinstance(dataset, SufficiencyDataset):
        raise TypeError("dataset must be SufficiencyDataset")
    query_ids = dataset.query_ids
    if len(query_ids) < 2:
        raise Int3ExperimentError(
            "leave-one-query-out requires at least two distinct queries"
        )
    folds: list[LeaveOneQueryOutFold] = []
    for fold_index, held_out in enumerate(query_ids):
        test = tuple(
            index
            for index, row in enumerate(dataset.rows)
            if row.query_id == held_out
        )
        train = tuple(
            index
            for index, row in enumerate(dataset.rows)
            if row.query_id != held_out
        )
        folds.append(
            LeaveOneQueryOutFold(
                fold_index=fold_index,
                held_out_query_id=held_out,
                train_indices=train,
                test_indices=test,
            )
        )
    return tuple(folds)


def assert_no_query_leakage(
    dataset: SufficiencyDataset,
    folds: tuple[LeaveOneQueryOutFold, ...],
) -> None:
    """Refuse any fold whose train and test sets share a query or a row."""

    if not isinstance(dataset, SufficiencyDataset):
        raise TypeError("dataset must be SufficiencyDataset")
    if not isinstance(folds, tuple) or not folds:
        raise Int3ExperimentError("folds must be a non-empty tuple")
    query_ids = dataset.query_ids
    if len(folds) != len(query_ids):
        raise Int3ExperimentError("there must be exactly one fold per query")
    if tuple(item.held_out_query_id for item in folds) != query_ids:
        raise Int3ExperimentError("folds must hold out each query exactly once")
    all_indices = frozenset(range(len(dataset.rows)))
    for fold in folds:
        train_queries = {
            dataset.rows[index].query_id for index in fold.train_indices
        }
        test_queries = {
            dataset.rows[index].query_id for index in fold.test_indices
        }
        if test_queries != {fold.held_out_query_id}:
            raise Int3ExperimentError(
                "a fold test set must contain exactly the held-out query"
            )
        if fold.held_out_query_id in train_queries:
            raise Int3ExperimentError(
                "the held-out query must never appear in the training set"
            )
        if train_queries & test_queries:
            raise Int3ExperimentError("train and test queries must be disjoint")
        if (
            frozenset(fold.train_indices) | frozenset(fold.test_indices)
        ) != all_indices:
            raise Int3ExperimentError("each fold must partition every dataset row")
        expected_test = {
            index
            for index in all_indices
            if dataset.rows[index].query_id == fold.held_out_query_id
        }
        if frozenset(fold.test_indices) != expected_test:
            raise Int3ExperimentError(
                "a fold test set must contain every subset of the held-out query"
            )
