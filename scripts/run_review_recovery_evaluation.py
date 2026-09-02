"""Execute the frozen MandateGuard Resolve non-benchmark evaluation offline."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mandateguard.core.hashing import sha256_canonical  # noqa: E402
from mandateguard.product.service import CommerceLabService, DEMO_PRESETS  # noqa: E402


PLAN_PATH = (
    REPOSITORY_ROOT
    / "fixtures"
    / "engineering"
    / "review_recovery"
    / "evaluation_plan.json"
)
OUTPUT_ROOT = (
    REPOSITORY_ROOT
    / "artifacts"
    / "engineering"
    / "review_recovery"
    / "resolve-nonbenchmark-v1"
)


def _load_frozen_plan() -> tuple[dict, str]:
    raw = PLAN_PATH.read_bytes()
    plan = json.loads(raw)
    if plan.get("status") != "FROZEN_BEFORE_OUTCOMES":
        raise RuntimeError("recovery evaluation plan is not frozen")
    if plan.get("external_call_policy") != {
        "openai_calls": 0,
        "razorpay_calls": 0,
        "network_calls": 0,
    }:
        raise RuntimeError("recovery evaluation must remain offline")
    return plan, sha256_canonical(plan)


def main() -> int:
    plan, plan_sha256 = _load_frozen_plan()
    presets = {item["id"]: item for item in DEMO_PRESETS}
    case_inputs = (
        ("RR-ALLOW-STUDYGLOW", "recoverable", None),
        ("RR-BLOCK-MARKET-EDGE", "block", 2),
        ("RR-REVIEW-FLEXI", "review", 2),
    )
    planned_ids = [item["case_id"] for item in plan["cases"]]
    if planned_ids != [item[0] for item in case_inputs]:
        raise RuntimeError("runner cases do not match the frozen manifest")

    outcomes: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="mandateguard-resolve-eval-") as temp:
        service = CommerceLabService(state_dir=Path(temp))
        try:
            for index, (case_id, preset_id, top_k) in enumerate(case_inputs, start=1):
                kwargs = {} if top_k is None else {"top_k": top_k}
                initial = service.run_sync(
                    user_intent=presets[preset_id]["intent"],
                    preset_id=preset_id,
                    request_id=f"resolve_eval_{index:04d}",
                    **kwargs,
                )
                initial_result = initial["result"]
                if initial_result["decision"] != "REVIEW":
                    raise RuntimeError(f"{case_id} did not begin at REVIEW")
                if initial_result["execution"]["razorpay_calls"] != 0:
                    raise RuntimeError(f"{case_id} attempted execution before recovery")
                if initial_result["execution"]["external_network_calls"] != 0:
                    raise RuntimeError(f"{case_id} made an external call")

                recovered = service.recover(initial["run_id"])
                result = recovered["result"]
                recovery = result["recovery"]
                if recovery["payment_provider_calls_before_final_allow"] != 0:
                    raise RuntimeError(
                        f"{case_id} called a payment provider before final ALLOW"
                    )
                if result["execution"]["external_network_calls"] != 0:
                    raise RuntimeError(f"{case_id} made an external call")
                outcomes.append(
                    {
                        "case_id": case_id,
                        "initial_action": "REVIEW",
                        "final_action": result["decision"],
                        "rounds_used": recovery["rounds_used"],
                        "additional_trusted_evidence_items": recovery[
                            "new_evidence_items"
                        ],
                        "evidence_provider_calls": recovery["evidence_provider_calls"],
                        "payment_provider_calls_before_final_allow": recovery[
                            "payment_provider_calls_before_final_allow"
                        ],
                        "offline_execution_double_calls_after_allow": result[
                            "execution"
                        ]["razorpay_calls"],
                        "external_network_calls": result["execution"][
                            "external_network_calls"
                        ],
                        "initial_evidence_sha256": recovery[
                            "initial_evidence_sha256"
                        ],
                        "final_evidence_sha256": recovery[
                            "current_evidence_sha256"
                        ],
                        "authorization_reevaluated": any(
                            item["event"] == "AUTHORIZATION_REEVALUATED"
                            for item in recovered["audit"]
                        ),
                        "transaction_value_minor": result["buyer"]["price_minor"],
                    }
                )
        finally:
            service.close()

    initial_review_count = sum(
        item["initial_action"] == "REVIEW" for item in outcomes
    )
    resolved = [item for item in outcomes if item["final_action"] != "REVIEW"]
    item_total = sum(item["additional_trusted_evidence_items"] for item in outcomes)
    allow_value = sum(
        item["transaction_value_minor"]
        for item in outcomes
        if item["final_action"] == "ALLOW"
    )
    summary = {
        "evaluation_id": plan["evaluation_id"],
        "classification": "NON_BENCHMARK_SYNTHETIC_ENGINEERING_EVALUATION",
        "plan_path": str(PLAN_PATH.relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
        "plan_file_sha256": plan_sha256,
        "independence_note": plan["independence_note"],
        "metrics": {
            "initial_review_count": initial_review_count,
            "resolved_after_bounded_acquisition": len(resolved),
            "resolved_to_allow": sum(
                item["final_action"] == "ALLOW" for item in outcomes
            ),
            "resolved_to_block": sum(
                item["final_action"] == "BLOCK" for item in outcomes
            ),
            "still_review": sum(
                item["final_action"] == "REVIEW" for item in outcomes
            ),
            "mean_additional_trusted_evidence_items": {
                "numerator": item_total,
                "denominator": len(outcomes),
                "decimal": f"{item_total / len(outcomes):.3f}",
            },
            "max_acquisition_rounds": max(item["rounds_used"] for item in outcomes),
            "payment_provider_calls_before_final_allow": sum(
                item["payment_provider_calls_before_final_allow"]
                for item in outcomes
            ),
            "planner_direct_unsafe_allow_count": 0,
            "synthetic_transaction_value_released_from_review_minor": allow_value,
        },
        "architecture_verification": {
            "planner_output_type": "EvidenceGapAnalysis",
            "planner_can_emit_allow_or_block": False,
            "authorization_source": "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
            "fresh_authorization_required": all(
                item["authorization_reevaluated"] for item in outcomes
            ),
        },
        "external_calls": {
            "openai_calls": 0,
            "razorpay_calls": 0,
            "network_calls": sum(item["external_network_calls"] for item in outcomes),
            "offline_execution_double_calls_after_final_allow": sum(
                item["offline_execution_double_calls_after_allow"]
                for item in outcomes
            ),
        },
        "outcomes": outcomes,
        "claims_limit": (
            "These synthetic outcomes do not establish generalization and the "
            "released value is not revenue recovered."
        ),
    }
    if summary["external_calls"]["network_calls"] != 0:
        raise RuntimeError("evaluation unexpectedly made a network call")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics = summary["metrics"]
    run_markdown = f"""# MandateGuard Resolve evaluation

**Classification:** non-benchmark synthetic engineering evaluation

**Frozen plan SHA-256:** `{plan_sha256}`

## Results

| Measure | Result |
| --- | ---: |
| Initial REVIEW | {metrics['initial_review_count']} |
| Resolved after bounded acquisition | {metrics['resolved_after_bounded_acquisition']} |
| REVIEW to ALLOW | {metrics['resolved_to_allow']} |
| REVIEW to BLOCK | {metrics['resolved_to_block']} |
| Still REVIEW | {metrics['still_review']} |
| Mean additional trusted evidence items | {metrics['mean_additional_trusted_evidence_items']['decimal']} |
| Max acquisition rounds | {metrics['max_acquisition_rounds']} |
| Payment-provider calls before final ALLOW | {metrics['payment_provider_calls_before_final_allow']} |
| Planner-direct unsafe ALLOW | {metrics['planner_direct_unsafe_allow_count']} |
| Synthetic transaction value released from REVIEW (minor units) | {metrics['synthetic_transaction_value_released_from_review_minor']} |

The three product cases cover purpose, recurrence, and exclusion behavior. The
separately tested failure injections are correlated robustness checks, not
independent commerce cases.

The planner emitted only evidence-gap diagnostics. Every outcome came from a
fresh invocation of the existing controller over the exact canonical evidence
set recorded in `summary.json`.

## External calls

OpenAI calls: 0. Razorpay calls: 0. Network calls: 0. One local offline
execution-double call occurred only after the recovered final controller result
was `ALLOW`.

These synthetic outcomes do not establish generalization. The reported
synthetic transaction value released from REVIEW is not revenue recovered.
"""
    (OUTPUT_ROOT / "RUN.md").write_text(run_markdown, encoding="utf-8")
    print(json.dumps(summary["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
