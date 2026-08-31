"""Non-authorizing composition boundary for sufficiency control."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mandateguard.engineering.int3.controller import (
    ControllerAction,
    ControllerDecision,
)
from mandateguard.engineering.int3.models import Int3ExperimentError
from mandateguard.models.decision import DecisionAction


class SufficiencyRoute(str, Enum):
    """Routing outcomes; deliberately contains neither ALLOW nor BLOCK."""

    TIER_AB_TERMINAL = "TIER_AB_TERMINAL"
    REVIEW = "REVIEW"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    PROCEED_TO_SEMANTIC = "PROCEED_TO_SEMANTIC"


@dataclass(frozen=True, slots=True)
class SafeSufficiencyDecision:
    tier_ab_action: DecisionAction
    controller_action: ControllerAction | None
    selected_route: SufficiencyRoute
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.tier_ab_action, DecisionAction):
            raise Int3ExperimentError("tier_ab_action must be DecisionAction")
        if self.controller_action is not None and not isinstance(
            self.controller_action, ControllerAction
        ):
            raise Int3ExperimentError(
                "controller_action must be ControllerAction or null"
            )
        if not isinstance(self.selected_route, SufficiencyRoute):
            raise Int3ExperimentError("selected_route must be SufficiencyRoute")
        if not isinstance(self.reason, str) or not self.reason:
            raise Int3ExperimentError("reason must be non-empty")
        expected = _expected_route(self.tier_ab_action, self.controller_action)
        if self.selected_route is not expected:
            raise Int3ExperimentError("selected_route violates safety precedence")


def _expected_route(
    tier_ab_action: DecisionAction,
    controller_action: ControllerAction | None,
) -> SufficiencyRoute:
    if tier_ab_action is not DecisionAction.ALLOW:
        if controller_action is not None:
            raise Int3ExperimentError(
                "the learned controller must not run after Tier A/B BLOCK or REVIEW"
            )
        return SufficiencyRoute.TIER_AB_TERMINAL
    if controller_action is None:
        raise Int3ExperimentError(
            "Tier A/B ALLOW requires a sufficiency controller decision"
        )
    return {
        ControllerAction.DECIDE: SufficiencyRoute.PROCEED_TO_SEMANTIC,
        ControllerAction.RETRIEVE_MORE: SufficiencyRoute.RETRIEVE_MORE,
        ControllerAction.REVIEW: SufficiencyRoute.REVIEW,
    }[controller_action]


def enforce_sufficiency_safety_boundary(
    *,
    tier_ab_action: DecisionAction,
    controller: ControllerDecision | None = None,
) -> SafeSufficiencyDecision:
    """Apply Tier A/B precedence without emitting an authorization action.

    Tier A/B terminal decisions bypass the learned controller. After Tier A/B
    ALLOW, DECIDE means only proceed to the existing semantic verifier. Signed
    capability checks, semantic output composition, and the Razorpay gate stay
    authoritative downstream.
    """

    if not isinstance(tier_ab_action, DecisionAction):
        raise TypeError("tier_ab_action must be DecisionAction")
    if controller is not None and not isinstance(controller, ControllerDecision):
        raise TypeError("controller must be ControllerDecision or null")
    controller_action = (
        controller.selected_action if controller is not None else None
    )
    route = _expected_route(tier_ab_action, controller_action)
    if route is SufficiencyRoute.TIER_AB_TERMINAL:
        reason = (
            f"Tier A/B {tier_ab_action.value} is authoritative; the learned "
            "sufficiency controller is not invoked."
        )
    elif route is SufficiencyRoute.PROCEED_TO_SEMANTIC:
        reason = (
            "Proceed to the existing semantic verifier; INT-3 does not emit "
            "ALLOW/BLOCK or invoke execution."
        )
    elif route is SufficiencyRoute.RETRIEVE_MORE:
        reason = "Acquire the selected trusted evidence, then reassess once."
    else:
        reason = "Escalate to REVIEW without changing authorization precedence."
    return SafeSufficiencyDecision(
        tier_ab_action=tier_ab_action,
        controller_action=controller_action,
        selected_route=route,
        reason=reason,
    )
