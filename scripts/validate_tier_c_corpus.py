"""Validate the committed Tier C corpus for one split.

At D8-A no Tier C case exists, so the expected output is an empty corpus
reported as valid in partial-development mode. That is the correct milestone
state, not a failure.

    python scripts/validate_tier_c_corpus.py --mode partial_development

This script reads case files, validates them, and prints a report. It writes
nothing, calls no detector, and calls no model.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.benchmark.tier_c.corpus import CORPUS_ROOT, load_corpus  # noqa: E402
from mandateguard.benchmark.tier_c.validation import (  # noqa: E402
    MODE_SPLIT,
    ValidationMode,
    validate_tier_c_corpus,
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ValidationMode],
        default=ValidationMode.PARTIAL_DEVELOPMENT.value,
        help=(
            "validation mode; the split follows from it, since the partial and "
            "final development modes are development-scoped by definition"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT / CORPUS_ROOT,
        help="corpus root directory (default: benchmark/cases/tier_c)",
    )
    parser.add_argument(
        "--detector-freeze-at",
        type=_timestamp,
        default=None,
        help="detector freeze timestamp, required for held-out isolation audit",
    )
    args = parser.parse_args(argv)

    mode = ValidationMode(args.mode)
    split = MODE_SPLIT[mode]
    corpus = load_corpus(args.root, split)
    report = validate_tier_c_corpus(
        corpus.cases, mode, detector_freeze_at=args.detector_freeze_at
    )

    print(f"corpus root: {args.root}")
    print(f"split: {split.value}")
    print(report.render())
    if corpus.is_empty:
        print(
            "note: no Tier C case is authored yet; an empty corpus is the "
            "expected D8-A state."
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
