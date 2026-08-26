"""Capture human-written developer Tier C drafts from the authoring worksheet.

Examples:

    python scripts/capture_developer_tier_c_candidates.py
    python scripts/capture_developer_tier_c_candidates.py --mode final_candidates
    python scripts/capture_developer_tier_c_candidates.py --output drafts.jsonl

The command never supplies semantic text, never assigns ground truth, and never
creates a finalized corpus record.  Blank rows are skipped in partial mode.
If ``--output`` is omitted, canonical candidate JSONL is written to stdout.
An output path must not already exist, preventing accidental replacement of
captured human work.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSHEET = (
    REPOSITORY_ROOT
    / "benchmark"
    / "tier_c"
    / "authoring"
    / "dev"
    / "developer_candidates.tsv"
)
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.benchmark.tier_c.developer_capture import (  # noqa: E402
    AuthoringMode,
    DeveloperCaptureError,
    candidate_record_line,
    capture_candidates,
    load_worksheet,
    validate_clean_envelope,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "worksheet",
        nargs="?",
        type=Path,
        default=DEFAULT_WORKSHEET,
        help="UTF-8 developer candidate TSV",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in AuthoringMode],
        default=AuthoringMode.PARTIAL.value,
        help="partial permits blank/missing rows; final_candidates requires all 88",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new JSONL path outside the finalized Tier C corpus (default: stdout)",
    )
    args = parser.parse_args(argv)

    try:
        rows = load_worksheet(args.worksheet)
        candidates = capture_candidates(rows, AuthoringMode(args.mode))
        for candidate in candidates:
            validate_clean_envelope(candidate)
        lines = tuple(candidate_record_line(candidate) for candidate in candidates)
        if args.output is not None:
            if not lines:
                raise DeveloperCaptureError(
                    "no complete candidates to write; blank worksheet rows are not cases"
                )
            if args.output.exists():
                raise DeveloperCaptureError(
                    f"output already exists and will not be replaced: {args.output}"
                )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                for line in lines:
                    stream.write(line + "\n")
        else:
            for line in lines:
                print(line)
    except (DeveloperCaptureError, OSError) as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1

    destination = "stdout" if args.output is None else str(args.output)
    print(
        f"captured {len(candidates)} unadjudicated developer candidate(s) to "
        f"{destination}; blank rows skipped",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
