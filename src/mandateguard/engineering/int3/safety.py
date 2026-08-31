"""Safety composition boundary for evidence-sufficiency advice.

INT-3 is an evidence-routing layer, never an authorization authority.  A Tier
A/B BLOCK or REVIEW is final for this layer.  Even when Tier A/B allows the
flow to continue, DECIDE means only "proceed to the existing semantic
verifier"; it never means ALLOW and never reaches execution directly.
"""

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
    """The only routes this learned layer can emit."""

    BLOCK = "BLOCK"
    REVIEW = "REVIEW"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    PROCEED_TO_SEMANTIC = "PROCEED_TO_SEMANTIC"


@dataclass(frozen=True, slots=True)
class SafeSufficiencyDecision:
    """A controller recommendation after deterministic precedence is applied."""

    tier_ab_action: DecisionAction
    controller_action: ControllerAction
    selected_route: SufficiencyRoute
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.tier_ab_action, DecisionAction):
            raise Int3ExperimentError("tier_ab_action must be DecisionAction")
        if not isinstance(self.controller_action, ControllerAction):
            raise Int3ExperimentError(
                "controller_action must be ControllerAction"
            )
        if not isinstance(self.selected_route, SufficiencyRoute):
            raise Int3ExperimentError("selected_route must be SufficiencyRoute")
        if not isinstance(self.reason, str) or not self.reason:
            raise Int3ExperimentError("reason must be non-empty")
        expected = _expected_route(self.tier_ab_action, self.controller_action)
        if self.selected_route is not expected:
            raise Int3ExperimentError(
                "selected_route violates Tier A/B or semantic-verifier precedence"
            )


def _expected_route(
    tier_ab_action: DecisionAction, controller_action: ControllerAction
) -> SufficiencyRoute:
    if tier_ab_action is DecisionAction.BLOCK:
        return SufficiencyRoute.BLOCK
    if tier_ab_action is DecisionAction.REVIEW:
        return SufficiencyRoute.REVIEW
    return {
        ControllerAction.DECIDE: SufficiencyRoute.PROCEED_TO_SEMANTIC,
        ControllerAction.RETRIEVE_MORE: SufficiencyRoute.RETRIEVE_MORE,
        ControllerAction.REVIEW: SufficiencyRoute.REVIEW,
    }[controller_action]


def enforce_sufficiency_safety_boundary(
    *, tier_ab_action: DecisionAction, controller: ControllerDecision
) -> SafeSufficiencyDecision:
    """Apply immutable policy precedence to learned sufficiency advice.

    The result has no ALLOW route.  Existing semantic verification, execution
    capability checks, the execution ledger, and the Razorpay gate remain
    downstream and authoritative.
    """

    if not isinstance(tier_ab_action, DecisionAction):
        raise TypeError("tier_ab_action must be DecisionAction")
    if not isinstance(controller, ControllerDecision):
        raise TypeError("controller must be ControllerDecision")
    route = _expected_route(tier_ab_action, controller.selected_action)
    if tier_ab_action is not DecisionAction.ALLOW:
        reason = (
            f"Tier A/B {tier_ab_action.value} is authoritative; the learned "
            f"{controller.selected_action.value} recommendation is ignored."
        )
    elif route is SufficiencyRoute.PROCEED_TO_SEMANTIC:
        reason = (
            "Evidence is routed to the existing semantic verifier; INT-3 does "
            "not authorize or invoke execution."
        )
    elif route is SufficiencyRoute.RETRIEVE_MORE:
        reason = "Acquire the ranked missing trusted evidence before semantic inference."
    else:
        reason = "Escalate to REVIEW without changing an authorization outcome."
    return SafeSufficiencyDecision(
        tier_ab_action=tier_ab_action,
        controller_action=controller.selected_action,
        selected_route=route,
        reason=reason,
    )
