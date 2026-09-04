"""Run the authorization-scale benchmark over the frozen synthetic universe.

    python scripts/run_authorization_scale_benchmark.py

The world was frozen in `data/eval/authorization-scale/WORLD_FREEZE.json` before
any of this executed. This script refuses to report a rung whose regenerated
descriptor stream disagrees with that freeze, so a drifted generator is a hard
failure rather than a differently-shaped result.

No OpenAI, no Razorpay HTTP, no Hugging Face. The provider client counts and
returns; it never opens a socket.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.engineering.authscale.benchmark import run_benchmark  # noqa: E402
from mandateguard.engineering.authscale.universe import (  # noqa: E402
    descriptor_stream_sha256,
)


FREEZE_PATH = REPOSITORY_ROOT / "data" / "eval" / "authorization-scale" / "WORLD_FREEZE.json"
OUT_DIR = REPOSITORY_ROOT / "artifacts" / "engineering" / "authorization-scale"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-counts",
        type=int,
        nargs="+",
        default=None,
        help="ladder rungs to run; defaults to the frozen ladder",
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR / "benchmark.json")
    args = parser.parse_args(argv)

    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    frozen_streams = {
        int(rung["case_count"]): str(rung["case_descriptor_stream_sha256"])
        for rung in freeze["scale_ladder"]
    }
    counts = args.case_counts or sorted(frozen_streams)

    rungs = []
    for count in counts:
        expected = frozen_streams.get(count)
        regenerated = descriptor_stream_sha256(count)
        if expected is not None and regenerated != expected:
            raise SystemExit(
                f"case descriptor stream for {count} cases does not match the "
                "freeze; the generator has drifted and the run is void"
            )
        print(f"running {count:,} cases ...", flush=True)
        rung = run_benchmark(case_count=count)
        # A rung the freeze does not name is still legitimate to measure; it is
        # labelled so nobody later reads it as frozen-and-verified.
        rung["freeze_status"] = (
            "MATCHES_FREEZE" if expected is not None else "NOT_IN_FREEZE_EXPLORATORY"
        )
        rung["case_descriptor_stream_sha256_regenerated"] = regenerated
        rungs.append(rung)
        counters = rung["counters"]
        print(
            f"  agreement {counters['target_invariant_agreement']}/{counters['total_cases']}"
            f"  {rung['actions']}"
            f"  p50={rung['authorization_latency_ms']['p50']}ms"
            f"  {counters['authorizations_per_second']}/s",
            flush=True,
        )

    report = {
        "schema_version": "authorization-scale-report-v1",
        "freeze_artifact": "data/eval/authorization-scale/WORLD_FREEZE.json",
        "freeze_payload_sha256": freeze["freeze_payload_sha256"],
        "taxonomy_sha256": freeze["taxonomy_sha256"],
        "chronology": (
            "The world, the taxonomy, the expected safe actions, and the case "
            "descriptor stream digests were committed before this benchmark "
            "existed. Every rung below re-derives the stream and refuses to "
            "report if it disagrees."
        ),
        "ladder": rungs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
