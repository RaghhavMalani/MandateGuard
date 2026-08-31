"""Pure expected-loss controller for the INT-3 sufficiency layer.

The controller does not authorize a payment and does not interpret evidence.
It compares three engineering actions using an explicit probability and cost
model.  Safety composition with Tier A/B lives in :mod:`safety`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping

from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    nonnegative_number,
    probability,
)


class ControllerAction(str, Enum):
    """Actions the evidence-sufficiency layer may recommend."""

    DECIDE = "DECIDE"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    REVIEW = "REVIEW"


@dataclass(frozen=True, slots=True)
class ControllerCosts:
    """Configurable engineering costs used by the expected-loss calculation."""

    unstable_decision: float
    retrieve: float
    review: float

    def __post_init__(self) -> None:
        for value, name in (
            (self.unstable_decision, "C_UNSTABLE_DECISION"),
            (self.retrieve, "C_RETRIEVE"),
            (self.review, "C_REVIEW"),
        ):
            object.__setattr__(
                self,
                {
                    "C_UNSTABLE_DECISION": "unstable_decision",
                    "C_RETRIEVE": "retrieve",
                    "C_REVIEW": "review",
                }[name],
                nonnegative_number(value, name),
            )

    @property
    def C_UNSTABLE_DECISION(self) -> float:  # noqa: N802 - mirrors the thesis
        return self.unstable_decision

    @property
    def C_RETRIEVE(self) -> float:  # noqa: N802 - mirrors the thesis
        return self.retrieve

    @property
    def C_REVIEW(self) -> float:  # noqa: N802 - mirrors the thesis
        return self.review


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    """Selected controller action and the complete auditable loss surface."""

    selected_action: ControllerAction
    p_sufficient: float
    expected_losses: Mapping[ControllerAction, float]
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.selected_action, ControllerAction):
            raise Int3ExperimentError("selected_action must be a ControllerAction")
        object.__setattr__(
            self, "p_sufficient", probability(self.p_sufficient, "p_sufficient")
        )
        if not isinstance(self.expected_losses, Mapping) or set(
            self.expected_losses
        ) != set(ControllerAction):
            raise Int3ExperimentError(
                "expected_losses must contain DECIDE, RETRIEVE_MORE, and REVIEW"
            )
        parsed: dict[ControllerAction, float] = {}
        for action in ControllerAction:
            value = self.expected_losses[action]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise Int3ExperimentError("expected losses must be finite and non-negative")
            parsed[action] = float(value)
        minimum = min(parsed.values())
        if parsed[self.selected_action] != minimum:
            raise Int3ExperimentError("selected_action must minimize expected loss")
        if not isinstance(self.reason, str) or not self.reason:
            raise Int3ExperimentError("reason must be non-empty")
        object.__setattr__(self, "expected_losses", MappingProxyType(parsed))

    def loss_for(self, action: ControllerAction) -> float:
        if not isinstance(action, ControllerAction):
            raise TypeError("action must be ControllerAction")
        return self.expected_losses[action]


# Equal-loss choices prefer human review, then information acquisition, then a
# potentially unstable decision.  This tie rule is explicit and deterministic.
_SAFETY_TIE_ORDER = (
    ControllerAction.REVIEW,
    ControllerAction.RETRIEVE_MORE,
    ControllerAction.DECIDE,
)


def select_controller_action(
    *, p_sufficient: float, costs: ControllerCosts
) -> ControllerDecision:
    """Select the minimum-expected-loss action without a hidden threshold.

    The deliberately small model is the only one identifiable from the stated
    inputs:

    ``L(DECIDE) = (1 - p_sufficient) * C_UNSTABLE_DECISION``
    ``L(RETRIEVE_MORE) = C_RETRIEVE``
    ``L(REVIEW) = C_REVIEW``

    VoI planning may be used upstream to choose the evidence acquisition and
    therefore the concrete retrieval cost; this controller never fetches it.
    """

    p = probability(p_sufficient, "p_sufficient")
    if not isinstance(costs, ControllerCosts):
        raise TypeError("costs must be ControllerCosts")
    losses = {
        ControllerAction.DECIDE: (1.0 - p) * costs.unstable_decision,
        ControllerAction.RETRIEVE_MORE: costs.retrieve,
        ControllerAction.REVIEW: costs.review,
    }
    minimum = min(losses.values())
    selected = next(action for action in _SAFETY_TIE_ORDER if losses[action] == minimum)
    reason = (
        f"{selected.value} minimizes expected engineering loss: "
        f"DECIDE=(1-{p:.6f})*{costs.unstable_decision:.6f}="
        f"{losses[ControllerAction.DECIDE]:.6f}; "
        f"RETRIEVE_MORE={losses[ControllerAction.RETRIEVE_MORE]:.6f}; "
        f"REVIEW={losses[ControllerAction.REVIEW]:.6f}."
    )
    return ControllerDecision(
        selected_action=selected,
        p_sufficient=p,
        expected_losses=losses,
        reason=reason,
    )
