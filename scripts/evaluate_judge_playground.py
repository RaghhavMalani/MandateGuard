"""Run the frozen judge query set through the Playground and record the mix.

    python scripts/evaluate_judge_playground.py [--out data/eval/judge-playground]

Every outcome comes from the real controller over the real sandbox catalogue.
The script measures; it does not assert. Read the report, and if ordinary
requests are ending in REVIEW, fix the sandbox data rather than the controller.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.product.service import CommerceLabService  # noqa: E402
from mandateguard.sandbox.health import evaluate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "eval" / "judge-playground",
        help="directory the report is written to",
    )
    arguments = parser.parse_args()
    arguments.out.mkdir(parents=True, exist_ok=True)

    state_dir = Path(tempfile.mkdtemp(prefix="mandateguard-judge-health-"))
    with CommerceLabService(state_dir=state_dir) as service:
        report = evaluate(service, REPOSITORY_ROOT)

    target = arguments.out / "JUDGE_QUERY_REPORT.json"
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    overall = report["overall"]
    ordinary = report["ordinary"]
    print(f"queries                {report['queries']}")
    print(f"world                  {report['world_version']}")
    print(f"candidate found rate   {overall['candidate_found_rate']:.3f}")
    print("overall                " + _rates(overall["rates"]))
    print("ordinary requests      " + _rates(ordinary["rates"]))
    insistent = report["insistent_selection"]
    print(
        f"insistent selection    " + _rates(insistent["rates"]) + f"  (n={insistent['total']})"
    )
    for cohort, distribution in report["by_cohort"].items():
        print(f"  {cohort:20s} " + _rates(distribution["rates"]))
    print(f"search p50/p95 ms      {report['latency_ms']['search_p50']} / {report['latency_ms']['search_p95']}")
    print(
        "authorization p50/p95  "
        f"{report['latency_ms']['authorization_p50']} / {report['latency_ms']['authorization_p95']}"
    )
    print(f"report                 {target}")
    return 0


def _rates(rates: dict[str, float]) -> str:
    return "  ".join(
        f"{key.lower()}={value:.3f}"
        for key, value in rates.items()
        if key != "ERROR" or value
    )


if __name__ == "__main__":
    raise SystemExit(main())
