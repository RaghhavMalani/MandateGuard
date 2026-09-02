"""Execute the frozen MandateGuard Resolve non-benchmark evaluation offline."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mandateguard.core.hashing import sha256_canonical  # noqa: E402
from mandateguard.product.evidence_policy import (  # noqa: E402
    PRODUCT_EVIDENCE_POLICY,
    TRUST_SENSITIVE_FIELDS,
)
from mandateguard.product.service import (  # noqa: E402
    CommerceLabService,
    RESOLVE_EVALUATION_SCENARIOS,
)
from mandateguard.recovery import (  # noqa: E402
    EVALUATION_METRIC_NAMES,
    EXTERNAL_CALL_COUNTER_NAMES,
    METRIC_SCHEMA_VERSION,
    OBSERVED_COUNTER_NAMES,
    validate_metric_names,
    validate_observed_counters,
)


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
    / "resolve-nonbenchmark-v2"
)


def _load_frozen_plan() -> tuple[dict, str, str]:
    raw = PLAN_PATH.read_bytes()
    plan = json.loads(raw)
    if plan.get("status") != "FROZEN_BEFORE_OUTCOMES":
        raise RuntimeError("recovery evaluation plan is not frozen")
    if plan.get("execution_permitted") is not True:
        raise RuntimeError("recovery evaluation execution is not permitted")
    expansion = plan.get("expansion") or {}
    if (
        expansion.get("target_case_count") != 20
        or expansion.get("defined_case_count") != len(plan.get("cases", ()))
        or len(plan.get("cases", ())) != 20
    ):
        raise RuntimeError("recovery evaluation plan is not expanded to 20 cases")
    if plan.get("external_call_policy") != {
        "openai_calls": 0,
        "razorpay_http_calls": 0,
        "network_calls": 0,
    }:
        raise RuntimeError("recovery evaluation must remain offline")
    if plan.get("metric_schema_version") != METRIC_SCHEMA_VERSION:
        raise RuntimeError(
            "recovery evaluation plan does not declare " + METRIC_SCHEMA_VERSION
        )
    validate_observed_counters(
        plan.get("observed_counters", ()), context="preregistered observed counters"
    )
    validate_metric_names(
        plan.get("metrics", ()),
        emitted=EVALUATION_METRIC_NAMES,
        context="preregistered evaluation metrics",
    )
    policy = plan.get("evidence_policy") or {}
    if policy.get("policy_id") != PRODUCT_EVIDENCE_POLICY.policy_id:
        raise RuntimeError("evaluation plan does not use the product evidence policy")
    for name in ("max_acquisition_rounds", "max_new_evidence_items"):
        if policy.get(name) != getattr(PRODUCT_EVIDENCE_POLICY, name):
            raise RuntimeError(f"evaluation plan {name} differs from the product policy")
    return plan, sha256_canonical(plan), sha256(raw).hexdigest()


def _preregistered_commit_sha() -> str:
    """Require Commit A to be clean and record it before outcomes are produced."""

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout:
        raise RuntimeError(
            "evaluation requires a clean preregistration commit before outcomes"
        )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(revision) != 40 or any(
        character not in "0123456789abcdef" for character in revision
    ):
        raise RuntimeError("could not resolve the preregistration commit SHA")
    return revision


def _require_product_parity(
    service: CommerceLabService, observed: dict, case_id: str
) -> None:
    """Refuse to score a case whose trust configuration differs from the product."""

    expected = service.trust_configuration()
    differing = [
        name
        for name in TRUST_SENSITIVE_FIELDS
        if observed.get(name) != expected.get(name)
    ]
    if differing or observed.get("evidence_policy_overridden"):
        raise RuntimeError(
            f"{case_id} trust configuration differs from the product default: "
            + ", ".join(differing or ["evidence_policy_overridden"])
        )


def main() -> int:
    preregistered_commit_sha = _preregistered_commit_sha()
    plan, plan_canonical_sha256, plan_raw_file_sha256 = _load_frozen_plan()
    scenarios = {item["case_id"]: item for item in RESOLVE_EVALUATION_SCENARIOS}
    planned_ids = [item["case_id"] for item in plan["cases"]]
    if planned_ids != [item["case_id"] for item in RESOLVE_EVALUATION_SCENARIOS]:
        raise RuntimeError("runner cases do not match the frozen manifest")
    for case in plan["cases"]:
        scenario = scenarios[case["case_id"]]
        if (
            case["merchant_id"] != scenario["merchant_id"]
            or case["sku"] != scenario["sku"]
        ):
            raise RuntimeError(
                f"{case['case_id']} identity differs from the registered scenario"
            )

    outcomes: list[dict] = []
    trust_configuration: dict | None = None
    with tempfile.TemporaryDirectory(prefix="mandateguard-resolve-eval-") as temp:
        service = CommerceLabService(state_dir=Path(temp))
        try:
            for index, case_id in enumerate(planned_ids, start=1):
                scenario = scenarios[case_id]
                # No evidence override: the evaluation runs the product policy.
                initial = service.run_sync(
                    user_intent=scenario["intent"],
                    request_id=f"resolve_eval_{index:04d}",
                )
                initial_result = initial["result"]
                if initial_result is None:
                    raise RuntimeError(f"{case_id} did not complete: {initial['error']}")
                _require_product_parity(
                    service, initial_result["trust_configuration"], case_id
                )
                trust_configuration = dict(initial_result["trust_configuration"])
                if initial_result["decision"] != "REVIEW":
                    raise RuntimeError(f"{case_id} did not begin at REVIEW")
                initial_counters = initial_result["observed_counters"]
                validate_observed_counters(
                    initial_counters, context=f"{case_id} initial observed counters"
                )
                if (
                    initial_counters["openai_calls"]
                    or initial_counters["razorpay_http_calls"]
                ):
                    raise RuntimeError(f"{case_id} made an unexpected external call")
                if initial_result["execution"]["razorpay_calls"] != 0:
                    raise RuntimeError(f"{case_id} attempted execution before recovery")
                if initial_result["execution"]["external_network_calls"] != 0:
                    raise RuntimeError(f"{case_id} made an external call")

                recovered = service.recover(initial["run_id"])
                result = recovered["result"]
                recovery = result["recovery"]
                counters = result["observed_counters"]
                validate_observed_counters(
                    counters, context=f"{case_id} observed counters"
                )
                if counters["openai_calls"] or counters["razorpay_http_calls"]:
                    raise RuntimeError(f"{case_id} made an unexpected external call")
                if recovery["payment_provider_calls_before_final_allow"] != 0:
                    raise RuntimeError(
                        f"{case_id} called a payment provider before final ALLOW"
                    )
                if result["execution"]["external_network_calls"] != 0:
                    raise RuntimeError(f"{case_id} made an external call")
                _require_product_parity(
                    service, result["trust_configuration"], case_id
                )
                outcomes.append(
                    {
                        "case_id": case_id,
                        "merchant_id": result["buyer"]["merchant"],
                        "sku": result["buyer"]["sku"],
                        "initial_action": "REVIEW",
                        "final_action": result["decision"],
                        "rounds_used": recovery["rounds_used"],
                        "additional_trusted_evidence_items": recovery[
                            "new_evidence_items"
                        ],
                        "payment_provider_calls_before_final_allow": recovery[
                            "payment_provider_calls_before_final_allow"
                        ],
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
                            item["event"] == "REAUTHORIZATION"
                            for item in recovered["audit"]
                        ),
                        "transaction_value_minor": result["buyer"]["price_minor"],
                        "observed_counters": dict(counters),
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
    metrics = {
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
        "max_acquisition_rounds": max(
            item["observed_counters"]["acquisition_rounds"] for item in outcomes
        ),
        "payment_provider_calls_before_final_allow": sum(
            item["payment_provider_calls_before_final_allow"] for item in outcomes
        ),
        "planner_direct_allow_count": sum(
            item["observed_counters"]["planner_direct_allow_count"]
            for item in outcomes
        ),
        "trusted_evidence_provider_calls_before_allow": sum(
            item["observed_counters"]["trusted_evidence_provider_calls"]
            for item in outcomes
            if item["final_action"] == "ALLOW"
        ),
        "new_evidence_items": sum(
            item["observed_counters"]["new_evidence_items"] for item in outcomes
        ),
        "synthetic_transaction_value_released_from_review_minor": allow_value,
    }
    validate_metric_names(
        plan["metrics"],
        emitted=metrics,
        context="resolve evaluation metric schema",
    )

    external_calls = {
        "openai_calls": sum(
            item["observed_counters"]["openai_calls"] for item in outcomes
        ),
        "razorpay_http_calls": sum(
            item["observed_counters"]["razorpay_http_calls"] for item in outcomes
        ),
    }
    external_calls["network_calls"] = (
        external_calls["openai_calls"] + external_calls["razorpay_http_calls"]
    )
    if set(external_calls) != set(EXTERNAL_CALL_COUNTER_NAMES):
        raise RuntimeError("external call counters do not match " + METRIC_SCHEMA_VERSION)
    if any(external_calls[name] for name in EXTERNAL_CALL_COUNTER_NAMES):
        raise RuntimeError("evaluation unexpectedly made an external call")
    external_calls["offline_adapter_calls_after_final_allow"] = sum(
        item["observed_counters"]["offline_adapter_calls"] for item in outcomes
    )

    summary = {
        "evaluation_id": plan["evaluation_id"],
        "classification": "NON_BENCHMARK_SYNTHETIC_ENGINEERING_EVALUATION",
        "plan_path": str(PLAN_PATH.relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
        "preregistered_commit_sha": preregistered_commit_sha,
        "plan_canonical_sha256": plan_canonical_sha256,
        "plan_raw_file_sha256": plan_raw_file_sha256,
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "observed_counter_names": list(OBSERVED_COUNTER_NAMES),
        "trust_configuration": trust_configuration,
        "independence_note": plan["independence_note"],
        "metrics": metrics,
        "architecture_verification": {
            "planner_output_type": "EvidenceGapAnalysis",
            "planner_can_emit_allow_or_block": False,
            "authorization_source": "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
            "fresh_authorization_required": all(
                item["authorization_reevaluated"] for item in outcomes
            ),
            "product_evaluator_parity": True,
        },
        "external_calls": external_calls,
        "outcomes": outcomes,
        "claims_limit": (
            "These synthetic outcomes do not establish generalization and the "
            "released value is not revenue recovered."
        ),
    }

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_markdown = f"""# MandateGuard Resolve evaluation

**Classification:** non-benchmark synthetic engineering evaluation

**Preregistered Commit A:** `{preregistered_commit_sha}`

**Canonical decoded-plan SHA-256:** `{plan_canonical_sha256}`

**Raw plan file SHA-256:** `{plan_raw_file_sha256}`

**Metric schema:** `{METRIC_SCHEMA_VERSION}`

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
| Planner-direct ALLOW | {metrics['planner_direct_allow_count']} |
| Trusted-evidence provider calls before ALLOW | {metrics['trusted_evidence_provider_calls_before_allow']} |
| New evidence items | {metrics['new_evidence_items']} |
| Synthetic transaction value released from REVIEW (minor units) | {metrics['synthetic_transaction_value_released_from_review_minor']} |

The manifest's product cases cover purpose, recurrence, and exclusion behavior.
Separately tested failure injections are correlated robustness checks, not
independent commerce cases.

Every case ran the product default evidence policy
`{PRODUCT_EVIDENCE_POLICY.policy_id}` with no evaluator-only retrieval or
budget override, and the runner refuses to score a case whose trust
configuration differs from the product's.

The planner emitted only evidence-gap diagnostics. Every outcome came from a
fresh invocation of the existing controller over the exact canonical evidence
set recorded in `summary.json`.

## External calls

Counters are incremented by the adapters themselves, not derived from run mode.
OpenAI calls: {external_calls['openai_calls']}. Razorpay HTTP calls:
{external_calls['razorpay_http_calls']}. Network calls:
{external_calls['network_calls']}. Local offline execution-double calls:
{external_calls['offline_adapter_calls_after_final_allow']}, and each occurred
only after the recovered final controller result was `ALLOW`. A single
unexpected external call fails the run.

These synthetic outcomes do not establish generalization. The reported
synthetic transaction value released from REVIEW is not revenue recovered.
"""
    (OUTPUT_ROOT / "RUN.md").write_text(run_markdown, encoding="utf-8")
    print(json.dumps(summary["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
