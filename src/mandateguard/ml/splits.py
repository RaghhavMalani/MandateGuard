"""Frozen train / validation / test split for the discovery catalog.

Membership is a deterministic function of ``catalog_product_id``, so the split
does not depend on row order, on a RNG seed surviving a refactor, or on a saved
file. Re-running the trainer reproduces the same partition; adding listings
never moves an existing one between partitions.

The split is stratified by top-level category so a rare class is present in all
three partitions or in none of them, and it is **frozen before any test
evaluation runs**: ``freeze_split`` writes a manifest of partition sizes and a
digest of the test-set ids, and the evaluator refuses to score against a test
set whose digest has changed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Sequence


SPLIT_VERSION = "discovery-split-v1"
TRAIN_FRACTION = 0.70
VALIDATION_FRACTION = 0.15
# The remainder is the test partition.

PARTITIONS = ("train", "validation", "test")


def assignment_value(catalog_product_id: str, *, salt: str = SPLIT_VERSION) -> float:
    """Stable value in [0, 1) derived from the id alone."""

    digest = sha256(f"{salt}\x1f{catalog_product_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


@dataclass(frozen=True, slots=True)
class FrozenSplit:
    train: tuple[int, ...]
    validation: tuple[int, ...]
    test: tuple[int, ...]
    test_digest: str
    label_support: Mapping[str, Mapping[str, int]]

    def sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "test": len(self.test),
        }


def stratified_split(
    labels: Sequence[str], ids: Sequence[str]
) -> FrozenSplit:
    """Partition rows by hashed id, stratified within each label."""

    if len(labels) != len(ids):
        raise ValueError("labels and ids must be the same length")
    buckets: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for position, (label, identifier) in enumerate(zip(labels, ids, strict=True)):
        buckets[label].append((assignment_value(identifier), position))
    train: list[int] = []
    validation: list[int] = []
    test: list[int] = []
    support: dict[str, dict[str, int]] = {}
    for label, entries in buckets.items():
        entries.sort()
        total = len(entries)
        train_end = int(round(total * TRAIN_FRACTION))
        validation_end = train_end + int(round(total * VALIDATION_FRACTION))
        label_train = [position for _, position in entries[:train_end]]
        label_validation = [position for _, position in entries[train_end:validation_end]]
        label_test = [position for _, position in entries[validation_end:]]
        train.extend(label_train)
        validation.extend(label_validation)
        test.extend(label_test)
        support[label] = {
            "train": len(label_train),
            "validation": len(label_validation),
            "test": len(label_test),
            "total": total,
        }
    train.sort()
    validation.sort()
    test.sort()
    digest = sha256(
        "\n".join(sorted(ids[position] for position in test)).encode("utf-8")
    ).hexdigest()
    return FrozenSplit(
        train=tuple(train),
        validation=tuple(validation),
        test=tuple(test),
        test_digest=digest,
        label_support=support,
    )


def freeze_split(split: FrozenSplit, path: Path, *, catalog_sha256: str) -> str:
    """Commit the split before the test partition is ever scored."""

    payload = {
        "split_version": SPLIT_VERSION,
        "catalog_sha256": catalog_sha256,
        "fractions": {
            "train": TRAIN_FRACTION,
            "validation": VALIDATION_FRACTION,
            "test": round(1.0 - TRAIN_FRACTION - VALIDATION_FRACTION, 10),
        },
        "sizes": split.sizes(),
        "test_id_digest": split.test_digest,
        "label_support": {
            label: dict(counts) for label, counts in sorted(split.label_support.items())
        },
        "note": (
            "Membership is sha256(split_version|catalog_product_id) thresholded "
            "within each label. It does not depend on row order or a RNG seed."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return split.test_digest


def verify_frozen_split(split: FrozenSplit, path: Path) -> None:
    """Refuse to report test metrics against a split that has since moved."""

    try:
        recorded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"frozen split manifest {path} is missing; freeze the split before "
            "evaluating on the test partition"
        ) from error
    if recorded.get("test_id_digest") != split.test_digest:
        raise RuntimeError(
            "the test partition has changed since the split was frozen; "
            "refusing to report test metrics"
        )
