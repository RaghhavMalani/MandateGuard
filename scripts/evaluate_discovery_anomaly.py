"""Ask whether a learned anomaly detector beats the deterministic analytics.

    python scripts/evaluate_discovery_anomaly.py

Writes `artifacts/engineering/discovery/anomaly_evaluation.json` and prints the
verdict, which may well be "keep the baseline".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.discovery.catalog import load_catalog  # noqa: E402
from mandateguard.discovery.classifier import load_classifier  # noqa: E402
from mandateguard.ml.anomaly_eval import evaluate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=REPOSITORY_ROOT / "data" / "processed")
    parser.add_argument("--sample", type=int, default=600)
    parser.add_argument("--models-dir", type=Path, default=REPOSITORY_ROOT / "data" / "models")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "engineering" / "discovery" / "anomaly_evaluation.json",
    )
    args = parser.parse_args(argv)

    classifier_path = args.models_dir / "category_classifier.mgdx"
    report = evaluate(
        load_catalog(args.processed_dir),
        sample=args.sample,
        classifier=load_classifier(classifier_path) if classifier_path.exists() else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"cases: {report['cases']} ({report['defective_cases']} defective)")
    print(f"baseline : {report['baseline']}")
    print(f"candidate: {report['candidate']}")
    print(f"delta ROC AUC: {report['roc_auc_improvement']}")
    ablation = report["category_laundering_ablation"]
    print(
        "category laundering: with classifier AUC="
        f"{ablation['with_ml_mismatch_feature']['roc_auc']:.4f}, without="
        f"{ablation['without_ml_mismatch_feature']['roc_auc']:.4f}, gain="
        f"{ablation['roc_auc_gain_from_classifier']:+.4f} -> {ablation['verdict']}"
    )
    print(f"DECISION: {report['decision']}")
    print(report["decision_reason"])
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
