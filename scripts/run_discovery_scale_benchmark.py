"""Measure discovery catalog scale on this machine, in one process.

    python scripts/run_discovery_scale_benchmark.py

Writes `artifacts/engineering/discovery/scale_benchmark.json`. Needs no training
dependencies: it exercises the same standard-library runtime the demo serves.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.ml.scale_bench import run_benchmark  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=REPOSITORY_ROOT / "data" / "processed")
    parser.add_argument("--models-dir", type=Path, default=REPOSITORY_ROOT / "data" / "models")
    parser.add_argument("--queries", type=int, default=500)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--no-embedding", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "engineering" / "discovery" / "scale_benchmark.json",
    )
    args = parser.parse_args(argv)

    report = run_benchmark(
        processed_dir=args.processed_dir,
        models_dir=args.models_dir,
        query_count=args.queries,
        top_k=args.top_k,
        with_embedding=not args.no_embedding,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
