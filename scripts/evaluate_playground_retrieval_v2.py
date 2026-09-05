"""Execute the separately frozen 170-prompt Playground retrieval UX evaluation."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandateguard.product.service import CommerceLabService  # noqa: E402
from mandateguard.sandbox.retrieval_quality import evaluate_retrieval_v2  # noqa: E402


OUTPUT = ROOT / "data" / "eval" / "judge-playground-v3" / "RETRIEVAL_V2_REPORT.json"


def main() -> int:
    with CommerceLabService(repository_root=ROOT) as service:
        report = evaluate_retrieval_v2(service, ROOT)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "outcomes"}, indent=2))
    print(f"report {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
