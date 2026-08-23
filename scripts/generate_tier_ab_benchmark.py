"""Generate the registered deterministic Tier A/B benchmark corpus.

Usage::

    python scripts/generate_tier_ab_benchmark.py \
        --label-recorded-at 2026-08-23T00:00:00Z

``--label-recorded-at`` is required and must be an explicit timezone-aware UTC
timestamp: the generator never reads the wall clock, so re-running it with the
same timestamp reproduces byte-identical output.

This command generates and labels the corpus. It does not execute it. It does
not import the Tier A/B policy, the semantic verifier, or the execution gate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.benchmark.deterministic_generator import write_corpus  # noqa: E402


FORBIDDEN_MODULE_PREFIXES = (
    "mandateguard.policy",
    "mandateguard.semantic",
    "mandateguard.execution",
    "mandateguard.replay",
)


def parse_utc_timestamp(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "--label-recorded-at must carry an explicit UTC offset"
        )
    if parsed.utcoffset().total_seconds() != 0:
        raise argparse.ArgumentTypeError("--label-recorded-at must be UTC")
    return parsed.astimezone(timezone.utc)


def assert_detector_never_loaded() -> None:
    loaded = sorted(
        name
        for name in sys.modules
        if name.startswith(FORBIDDEN_MODULE_PREFIXES)
    )
    if loaded:
        raise SystemExit(
            "refusing to generate: corpus generation loaded detector modules "
            + ", ".join(loaded)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label-recorded-at",
        required=True,
        type=parse_utc_timestamp,
        help="explicit timezone-aware UTC timestamp recorded with every label",
    )
    parser.add_argument(
        "--root",
        default=REPOSITORY_ROOT,
        type=Path,
        help="repository root that contains benchmark/MANIFEST.yaml",
    )
    arguments = parser.parse_args(argv)

    assert_detector_never_loaded()
    corpus = write_corpus(arguments.root, arguments.label_recorded_at)
    assert_detector_never_loaded()

    print(json.dumps(corpus.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
