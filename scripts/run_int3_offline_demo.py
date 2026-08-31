"""Print the three synthetic INT-3 controller decisions; make no live calls."""

from __future__ import annotations


def main() -> int:
    from mandateguard.engineering.int3.demo import run_offline_demo

    print("INT-3 OFFLINE SYNTHETIC DEMO")
    print("semantic_provider_calls=0 evidence_fetch_calls=0 razorpay_calls=0")
    for scenario in run_offline_demo():
        candidate = ""
        if scenario.candidate_evidence_id is not None:
            candidate = (
                f" candidate={scenario.candidate_evidence_id} "
                f"counterfactual_p={scenario.counterfactual_p_sufficient:.2f} "
                f"voi={scenario.voi:.4f}"
            )
        print(
            f"{scenario.scenario_id} p_sufficient={scenario.p_sufficient:.2f} "
            f"action={scenario.controller.selected_action.value}{candidate}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
