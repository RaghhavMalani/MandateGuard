"""Frozen train / validation / test splits for the discovery catalog.

Two splits are defined here, and they answer different questions.

``stratified_split`` (row-wise)
    Membership is a deterministic function of ``catalog_product_id``, stratified
    by top-level category. It does not depend on row order, on a RNG seed
    surviving a refactor, or on a saved file.

``grouped_split`` (product family)
    The same construction, but the unit assigned to a partition is a *product
    family* rather than a row. This matters because a marketplace crawl lists one
    product many times - across sizes, colours, sellers, and re-postings - and a
    row-wise split scatters those near-identical rows across train and test. The
    resulting test score is then partly a memorization score: the model has seen
    the test row's twin during training. Grouping by family removes that path.

The grouping function is ``family_key`` and it is **frozen**: its version string
is recorded in every split manifest, so a later change to the normalization is
visible as a different split rather than as a quietly different number.

Both splits are **frozen before any test evaluation runs**: ``freeze_split``
writes a manifest of partition sizes and a digest of the test-set ids, and the
evaluator refuses to score against a test set whose digest has changed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Sequence


SPLIT_VERSION = "discovery-split-v1"
GROUPED_SPLIT_VERSION = "discovery-grouped-split-v1"
TRAIN_FRACTION = 0.70
VALIDATION_FRACTION = 0.15
# The remainder is the test partition.

PARTITIONS = ("train", "validation", "test")

#: Frozen. Changing any of the three constants below changes which rows can see
#: each other, so the version string moves with them and every manifest records
#: it. A metric produced under one version is not comparable to one produced
#: under another.
FAMILY_KEY_VERSION = "discovery-product-family-v1"

_NON_WORD = re.compile(r"[^a-z0-9]+")

#: Tokens that distinguish one *variant* of a product from another rather than
#: one product from another. They are removed before the family key is taken, so
#: "... Shorts (Blue, Large)" and "... Shorts (Red, Small)" land in one family.
_VARIANT_TOKENS = frozenset(
    {
        "assorted",
        "black",
        "blue",
        "brown",
        "colour",
        "color",
        "combo",
        "cm",
        "gm",
        "gold",
        "golden",
        "grey",
        "gray",
        "green",
        "inch",
        "kg",
        "large",
        "medium",
        "ml",
        "multi",
        "multicolor",
        "multicolour",
        "pack",
        "packs",
        "pc",
        "pcs",
        "piece",
        "pieces",
        "pink",
        "purple",
        "red",
        "regular",
        "set",
        "silver",
        "size",
        "sizes",
        "small",
        "white",
        "xl",
        "xs",
        "xxl",
        "xxxl",
        "yellow",
    }
)


def family_key(*, title: str, brand: str | None) -> str:
    """A stable identifier for the product family a listing belongs to.

    Frozen under ``FAMILY_KEY_VERSION``. The normalization is deliberately
    aggressive in one direction only: it merges listings that differ by variant
    wording, and it never merges listings from different brands.

    Over-merging costs training data. Under-merging leaves the leak the grouped
    split exists to close. Where the two trade off, this errs toward merging,
    because an inflated test score is the more misleading failure.
    """

    tokens = [token for token in _NON_WORD.split((title or "").casefold()) if token]
    core = [
        token
        for token in tokens
        if token not in _VARIANT_TOKENS and not token.isdigit() and len(token) > 1
    ]
    if not core:
        # A title that is entirely variant wording or digits still needs a key;
        # fall back to the raw tokens rather than collapsing every such listing
        # into one enormous family.
        core = tokens or [(title or "").strip().casefold() or "untitled"]
    normalized_brand = _NON_WORD.sub(" ", (brand or "").casefold()).strip()
    payload = f"{FAMILY_KEY_VERSION}\x1f{normalized_brand}\x1f{' '.join(core)}"
    return sha256(payload.encode("utf-8")).hexdigest()[:32]


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
    split_version: str = SPLIT_VERSION
    group_counts: Mapping[str, int] | None = None
    family_key_version: str | None = None

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


def grouped_split(
    labels: Sequence[str], ids: Sequence[str], groups: Sequence[str]
) -> FrozenSplit:
    """Partition *product families* by hashed family key, stratified by label.

    Every row of a family goes to exactly one partition, so a listing's
    near-identical twin can never sit on the other side of the train/test line.
    A family's label is the majority top-level category of its rows, which keeps
    stratification meaningful when a family straddles two categories.
    """

    if not (len(labels) == len(ids) == len(groups)):
        raise ValueError("labels, ids and groups must be the same length")

    members: dict[str, list[int]] = defaultdict(list)
    for position, group in enumerate(groups):
        members[group].append(position)

    # One label per family: the majority, ties broken by label name so the
    # assignment does not depend on dictionary or row order.
    family_label: dict[str, str] = {}
    for group, positions in members.items():
        counts: dict[str, int] = defaultdict(int)
        for position in positions:
            counts[labels[position]] += 1
        family_label[group] = min(
            sorted(counts), key=lambda label: (-counts[label], label)
        )

    buckets: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for group, label in family_label.items():
        buckets[label].append(
            (assignment_value(group, salt=GROUPED_SPLIT_VERSION), group)
        )

    train: list[int] = []
    validation: list[int] = []
    test: list[int] = []
    support: dict[str, dict[str, int]] = {}
    group_counts = {"train": 0, "validation": 0, "test": 0}
    for label, entries in buckets.items():
        entries.sort()
        total = len(entries)
        train_end = int(round(total * TRAIN_FRACTION))
        validation_end = train_end + int(round(total * VALIDATION_FRACTION))
        partitions = (
            ("train", entries[:train_end], train),
            ("validation", entries[train_end:validation_end], validation),
            ("test", entries[validation_end:], test),
        )
        counts: dict[str, int] = {}
        for name, chosen, sink in partitions:
            rows = [
                position for _, group in chosen for position in members[group]
            ]
            sink.extend(rows)
            counts[name] = len(rows)
            group_counts[name] += len(chosen)
        support[label] = {
            **counts,
            "total": sum(counts.values()),
            "families": total,
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
        split_version=GROUPED_SPLIT_VERSION,
        group_counts={**group_counts, "total": len(members)},
        family_key_version=FAMILY_KEY_VERSION,
    )


def freeze_split(split: FrozenSplit, path: Path, *, catalog_sha256: str) -> str:
    """Commit the split before the test partition is ever scored."""

    payload = {
        "split_version": split.split_version,
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
            "Membership is sha256(split_version|unit_id) thresholded within each "
            "label, where the unit is a row for the row-wise split and a product "
            "family for the grouped split. It does not depend on row order or a "
            "RNG seed."
        ),
    }
    if split.group_counts is not None:
        payload["group_counts"] = dict(split.group_counts)
    if split.family_key_version is not None:
        payload["family_key_version"] = split.family_key_version
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
            f"the frozen split manifest {Path(path).name} is missing; freeze the "
            "split before evaluating on the test partition"
        ) from error
    if recorded.get("split_version") != split.split_version:
        raise RuntimeError(
            "the frozen split manifest describes a different split construction; "
            "refusing to report test metrics"
        )
    if recorded.get("test_id_digest") != split.test_digest:
        raise RuntimeError(
            "the test partition has changed since the split was frozen; "
            "refusing to report test metrics"
        )
