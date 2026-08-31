"""One-step expected-loss controller for evidence sufficiency.

Retrieval is a candidate-specific action: acquisition cost is paid, then the
controller takes the cheaper of deciding with the counterfactual probability
or human review. No retrieval threshold or unexplained fixed retrieval loss is
used.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping, Sequence

from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    nonnegative_number,
    positive_number,
    probability,
)


class ControllerAction(str, Enum):
    DECIDE = "DECIDE"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    REVIEW = "REVIEW"


@dataclass(frozen=True, slots=True)
class ControllerCosts:
    """Engineering losses that are independent of evidence identity."""

    unstable_decision: float
    review: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unstable_decision",
            nonnegative_number(
                self.unstable_decision, "C_UNSTABLE_DECISION"
            ),
        )
        object.__setattr__(
            self, "review", nonnegative_number(self.review, "C_REVIEW")
        )

    @property
    def C_UNSTABLE_DECISION(self) -> float:  # noqa: N802
        return self.unstable_decision

    @property
    def C_REVIEW(self) -> float:  # noqa: N802
        return self.review


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    """Counterfactual probability and acquisition cost for one missing item."""

    evidence_id: str
    p_after: float
    acquisition_cost: float

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise Int3ExperimentError("evidence_id must be non-empty")
        object.__setattr__(self, "p_after", probability(self.p_after, "p_after"))
        object.__setattr__(
            self,
            "acquisition_cost",
            positive_number(
                self.acquisition_cost,
                f"C_ACQUIRE({self.evidence_id})",
            ),
        )


@dataclass(frozen=True, slots=True)
class RetrievalLoss:
    """Full one-step loss decomposition for one evidence candidate."""

    evidence_id: str
    p_after: float
    acquisition_cost: float
    decide_after_loss: float
    review_after_loss: float
    best_terminal_action: ControllerAction
    best_terminal_loss: float
    total_expected_loss: float

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise Int3ExperimentError("evidence_id must be non-empty")
        probability(self.p_after, "p_after")
        positive_number(self.acquisition_cost, "acquisition_cost")
        for value, name in (
            (self.decide_after_loss, "decide_after_loss"),
            (self.review_after_loss, "review_after_loss"),
            (self.best_terminal_loss, "best_terminal_loss"),
            (self.total_expected_loss, "total_expected_loss"),
        ):
            nonnegative_number(value, name)
        if self.best_terminal_action not in (
            ControllerAction.DECIDE,
            ControllerAction.REVIEW,
        ):
            raise Int3ExperimentError(
                "best_terminal_action must be DECIDE or REVIEW"
            )
        expected_best = min(self.decide_after_loss, self.review_after_loss)
        if not math.isclose(
            self.best_terminal_loss, expected_best, rel_tol=0.0, abs_tol=1e-12
        ):
            raise Int3ExperimentError("best_terminal_loss is inconsistent")
        expected_total = self.acquisition_cost + expected_best
        if not math.isclose(
            self.total_expected_loss,
            expected_total,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise Int3ExperimentError("total_expected_loss is inconsistent")


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    """Overall minimum-loss action with complete terminal/retrieval losses."""

    selected_action: ControllerAction
    p_current: float
    decide_loss: float
    review_loss: float
    retrieval_losses: tuple[RetrievalLoss, ...]
    best_retrieval_evidence_id: str | None
    expected_losses: Mapping[ControllerAction, float]
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.selected_action, ControllerAction):
            raise Int3ExperimentError("selected_action must be ControllerAction")
        object.__setattr__(
            self, "p_current", probability(self.p_current, "p_current")
        )
        nonnegative_number(self.decide_loss, "decide_loss")
        nonnegative_number(self.review_loss, "review_loss")
        if not isinstance(self.retrieval_losses, tuple) or not all(
            isinstance(item, RetrievalLoss) for item in self.retrieval_losses
        ):
            raise Int3ExperimentError("retrieval_losses must contain RetrievalLoss")
        ids = tuple(item.evidence_id for item in self.retrieval_losses)
        if len(ids) != len(set(ids)):
            raise Int3ExperimentError("retrieval candidate IDs must be unique")
        if self.retrieval_losses:
            minimum_retrieval_loss = min(
                item.total_expected_loss for item in self.retrieval_losses
            )
            expected_best = next(
                item
                for item in self.retrieval_losses
                if item.total_expected_loss == minimum_retrieval_loss
            )
            if self.best_retrieval_evidence_id != expected_best.evidence_id:
                raise Int3ExperimentError("best retrieval provenance is inconsistent")
        elif self.best_retrieval_evidence_id is not None:
            raise Int3ExperimentError(
                "best retrieval must be null when no evidence remains"
            )
        expected_keys = {ControllerAction.DECIDE, ControllerAction.REVIEW}
        if self.retrieval_losses:
            expected_keys.add(ControllerAction.RETRIEVE_MORE)
        if not isinstance(self.expected_losses, Mapping) or set(
            self.expected_losses
        ) != expected_keys:
            raise Int3ExperimentError("expected_losses has unavailable actions")
        parsed: dict[ControllerAction, float] = {}
        for action in expected_keys:
            parsed[action] = nonnegative_number(
                self.expected_losses[action], f"loss({action.value})"
            )
        if parsed[ControllerAction.DECIDE] != self.decide_loss:
            raise Int3ExperimentError("DECIDE loss decomposition is inconsistent")
        if parsed[ControllerAction.REVIEW] != self.review_loss:
            raise Int3ExperimentError("REVIEW loss decomposition is inconsistent")
        minimum = min(parsed.values())
        if parsed[self.selected_action] != minimum:
            raise Int3ExperimentError("selected_action must minimize expected loss")
        if not isinstance(self.reason, str) or not self.reason:
            raise Int3ExperimentError("reason must be non-empty")
        object.__setattr__(self, "expected_losses", MappingProxyType(parsed))

    @property
    def p_sufficient(self) -> float:
        """Compatibility/readability alias for the current probability."""

        return self.p_current

    def loss_for(self, action: ControllerAction) -> float:
        if not isinstance(action, ControllerAction):
            raise TypeError("action must be ControllerAction")
        if action not in self.expected_losses:
            raise Int3ExperimentError(f"{action.value} is not available")
        return self.expected_losses[action]


def decide_expected_loss(*, p_sufficient: float, costs: ControllerCosts) -> float:
    if not isinstance(costs, ControllerCosts):
        raise TypeError("costs must be ControllerCosts")
    p = probability(p_sufficient, "p_sufficient")
    return (1.0 - p) * costs.unstable_decision


def retrieval_expected_loss(
    *, candidate: RetrievalCandidate, costs: ControllerCosts
) -> RetrievalLoss:
    if not isinstance(candidate, RetrievalCandidate):
        raise TypeError("candidate must be RetrievalCandidate")
    if not isinstance(costs, ControllerCosts):
        raise TypeError("costs must be ControllerCosts")
    decide_after = decide_expected_loss(p_sufficient=candidate.p_after, costs=costs)
    review_after = costs.review
    # Terminal ties prefer REVIEW.
    terminal_action = (
        ControllerAction.DECIDE
        if decide_after < review_after
        else ControllerAction.REVIEW
    )
    terminal_loss = min(decide_after, review_after)
    return RetrievalLoss(
        evidence_id=candidate.evidence_id,
        p_after=candidate.p_after,
        acquisition_cost=candidate.acquisition_cost,
        decide_after_loss=decide_after,
        review_after_loss=review_after,
        best_terminal_action=terminal_action,
        best_terminal_loss=terminal_loss,
        total_expected_loss=candidate.acquisition_cost + terminal_loss,
    )


_SAFETY_TIE_ORDER = (
    ControllerAction.REVIEW,
    ControllerAction.RETRIEVE_MORE,
    ControllerAction.DECIDE,
)


def select_controller_action(
    *,
    p_current: float,
    costs: ControllerCosts,
    retrieval_candidates: Sequence[RetrievalCandidate] = (),
) -> ControllerDecision:
    """Select DECIDE, REVIEW, or the best one-step evidence acquisition."""

    p = probability(p_current, "p_current")
    if not isinstance(costs, ControllerCosts):
        raise TypeError("costs must be ControllerCosts")
    if not isinstance(retrieval_candidates, (list, tuple)):
        raise TypeError("retrieval_candidates must be a sequence")
    candidates = tuple(retrieval_candidates)
    if not all(isinstance(item, RetrievalCandidate) for item in candidates):
        raise Int3ExperimentError(
            "retrieval_candidates must contain RetrievalCandidate"
        )
    if len({item.evidence_id for item in candidates}) != len(candidates):
        raise Int3ExperimentError("retrieval candidate IDs must be unique")

    decide_loss = decide_expected_loss(p_sufficient=p, costs=costs)
    review_loss = costs.review
    decompositions = tuple(
        retrieval_expected_loss(candidate=item, costs=costs)
        for item in candidates
    )
    best_retrieval = (
        min(
            enumerate(decompositions),
            key=lambda pair: (pair[1].total_expected_loss, pair[0]),
        )[1]
        if decompositions
        else None
    )
    losses: dict[ControllerAction, float] = {
        ControllerAction.DECIDE: decide_loss,
        ControllerAction.REVIEW: review_loss,
    }
    if best_retrieval is not None:
        losses[ControllerAction.RETRIEVE_MORE] = (
            best_retrieval.total_expected_loss
        )
    minimum = min(losses.values())
    selected = next(
        action
        for action in _SAFETY_TIE_ORDER
        if action in losses and losses[action] == minimum
    )
    retrieval_text = (
        "RETRIEVE_MORE=unavailable"
        if best_retrieval is None
        else (
            f"RETRIEVE_MORE[{best_retrieval.evidence_id}]="
            f"{best_retrieval.acquisition_cost:.6f}+min("
            f"{best_retrieval.decide_after_loss:.6f},"
            f"{best_retrieval.review_after_loss:.6f})="
            f"{best_retrieval.total_expected_loss:.6f}"
        )
    )
    reason = (
        f"{selected.value} minimizes one-step expected engineering loss: "
        f"DECIDE=(1-{p:.6f})*{costs.unstable_decision:.6f}="
        f"{decide_loss:.6f}; REVIEW={review_loss:.6f}; {retrieval_text}."
    )
    return ControllerDecision(
        selected_action=selected,
        p_current=p,
        decide_loss=decide_loss,
        review_loss=review_loss,
        retrieval_losses=decompositions,
        best_retrieval_evidence_id=(
            best_retrieval.evidence_id if best_retrieval is not None else None
        ),
        expected_losses=losses,
        reason=reason,
    )
