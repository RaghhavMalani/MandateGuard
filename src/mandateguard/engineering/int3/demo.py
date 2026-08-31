"""Synthetic, zero-call demonstration of the INT-3 decision infrastructure."""

from __future__ import annotations

from dataclasses import dataclass

from mandateguard.engineering.int3.controller import (
    ControllerAction,
    ControllerCosts,
    ControllerDecision,
    select_controller_action,
)
from mandateguard.engineering.int3.models import Int3ExperimentError, probability


@dataclass(frozen=True, slots=True)
class OfflineDemoScenario:
    """One synthetic probability/cost case and its expected controller result."""

    scenario_id: str
    description: str
    p_sufficient: float
    controller: ControllerDecision
    candidate_evidence_id: str | None = None
    counterfactual_p_sufficient: float | None = None
    acquisition_cost: float | None = None
    voi: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise Int3ExperimentError("scenario_id must be non-empty")
        if not isinstance(self.description, str) or not self.description:
            raise Int3ExperimentError("description must be non-empty")
        probability(self.p_sufficient, "p_sufficient")
        if not isinstance(self.controller, ControllerDecision):
            raise Int3ExperimentError("controller must be ControllerDecision")
        candidate_values = (
            self.candidate_evidence_id,
            self.counterfactual_p_sufficient,
            self.acquisition_cost,
            self.voi,
        )
        if any(item is not None for item in candidate_values) and any(
            item is None for item in candidate_values
        ):
            raise Int3ExperimentError(
                "synthetic candidate fields must be either all set or all null"
            )


def run_offline_demo() -> tuple[OfflineDemoScenario, ...]:
    """Return the three required synthetic outcomes without external calls."""

    high = select_controller_action(
        p_sufficient=0.95,
        costs=ControllerCosts(unstable_decision=1.0, retrieve=0.20, review=0.40),
    )
    valuable = select_controller_action(
        p_sufficient=0.20,
        costs=ControllerCosts(unstable_decision=1.0, retrieve=0.10, review=0.40),
    )
    expensive = select_controller_action(
        p_sufficient=0.20,
        costs=ControllerCosts(unstable_decision=1.0, retrieve=0.75, review=0.35),
    )
    if (
        high.selected_action is not ControllerAction.DECIDE
        or valuable.selected_action is not ControllerAction.RETRIEVE_MORE
        or expensive.selected_action is not ControllerAction.REVIEW
    ):
        raise Int3ExperimentError("offline demo costs no longer produce A/B/C outcomes")
    return (
        OfflineDemoScenario(
            scenario_id="A",
            description="high sufficiency",
            p_sufficient=0.95,
            controller=high,
        ),
        OfflineDemoScenario(
            scenario_id="B",
            description="low sufficiency with valuable missing evidence",
            p_sufficient=0.20,
            controller=valuable,
            candidate_evidence_id="synthetic-high-value-evidence",
            counterfactual_p_sufficient=0.80,
            acquisition_cost=0.10,
            voi=(0.80 - 0.20) / 0.10,
        ),
        OfflineDemoScenario(
            scenario_id="C",
            description="low sufficiency with low-value expensive evidence",
            p_sufficient=0.20,
            controller=expensive,
            candidate_evidence_id="synthetic-low-value-evidence",
            counterfactual_p_sufficient=0.25,
            acquisition_cost=0.75,
            voi=(0.25 - 0.20) / 0.75,
        ),
    )
