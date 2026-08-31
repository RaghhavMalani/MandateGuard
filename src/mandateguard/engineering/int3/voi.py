"""Counterfactual expected-loss value of information for missing evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Callable, Mapping

from mandateguard.engineering.int3.controller import (
    ControllerAction,
    ControllerCosts,
    RetrievalCandidate,
    decide_expected_loss,
    retrieval_expected_loss,
)
from mandateguard.engineering.int3.features import (
    SubsetFeatureInput,
    extract_subset_features,
)
from mandateguard.engineering.int3.model import (
    SufficiencyModel,
    SufficiencyModelNotFittedError,
)
from mandateguard.engineering.int3.model_manifest import model_feature_vector
from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    positive_number,
    probability,
)


FeatureExtractor = Callable[[SubsetFeatureInput], Mapping[str, float]]


@dataclass(frozen=True, slots=True)
class EvidenceValueCandidate:
    """One-step loss reduction for one missing trusted-evidence item."""

    rank: int
    evidence_id: str
    current_probability: float
    counterfactual_probability: float
    delta_p: float
    acquisition_cost: float
    baseline_decide_loss: float
    baseline_review_loss: float
    baseline_best_terminal_loss: float
    after_decide_loss: float
    after_review_loss: float
    after_best_terminal_action: ControllerAction
    after_best_terminal_loss: float
    expected_loss_after_acquisition: float
    net_voi: float
    counterfactual_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise Int3ExperimentError("rank must be a positive integer")
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise Int3ExperimentError("evidence_id must be non-empty")
        current = probability(self.current_probability, "current_probability")
        after = probability(
            self.counterfactual_probability, "counterfactual_probability"
        )
        cost = positive_number(self.acquisition_cost, "acquisition_cost")
        if not math.isclose(
            self.delta_p, after - current, rel_tol=0.0, abs_tol=1e-12
        ):
            raise Int3ExperimentError("delta_p is inconsistent")
        expected_baseline = min(self.baseline_decide_loss, self.baseline_review_loss)
        if not math.isclose(
            self.baseline_best_terminal_loss,
            expected_baseline,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise Int3ExperimentError("baseline loss is inconsistent")
        expected_after_terminal = min(self.after_decide_loss, self.after_review_loss)
        if not math.isclose(
            self.after_best_terminal_loss,
            expected_after_terminal,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise Int3ExperimentError("after-acquisition terminal loss is inconsistent")
        expected_after = cost + expected_after_terminal
        if not math.isclose(
            self.expected_loss_after_acquisition,
            expected_after,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise Int3ExperimentError("after-acquisition loss is inconsistent")
        if not math.isclose(
            self.net_voi,
            expected_baseline - expected_after,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise Int3ExperimentError("net_voi must be expected loss reduction")
        if self.after_best_terminal_action not in (
            ControllerAction.DECIDE,
            ControllerAction.REVIEW,
        ):
            raise Int3ExperimentError("after terminal action is invalid")
        if (
            not isinstance(self.counterfactual_evidence_ids, tuple)
            or self.evidence_id not in self.counterfactual_evidence_ids
        ):
            raise Int3ExperimentError(
                "counterfactual_evidence_ids must contain the candidate"
            )

    @property
    def voi(self) -> float:
        """Primary VoI alias; it is net expected-loss reduction, not delta/cost."""

        return self.net_voi

    def as_retrieval_candidate(self) -> RetrievalCandidate:
        return RetrievalCandidate(
            evidence_id=self.evidence_id,
            p_after=self.counterfactual_probability,
            acquisition_cost=self.acquisition_cost,
        )


@dataclass(frozen=True, slots=True)
class _UnrankedCandidate:
    evidence_id: str
    eligible_index: int
    current_probability: float
    counterfactual_probability: float
    delta_p: float
    acquisition_cost: float
    after_decide_loss: float
    after_review_loss: float
    after_best_terminal_action: ControllerAction
    after_best_terminal_loss: float
    expected_loss_after_acquisition: float
    net_voi: float
    counterfactual_evidence_ids: tuple[str, ...]


def rank_evidence_by_voi(
    *,
    current: SubsetFeatureInput,
    remaining_evidence_ids: tuple[str, ...],
    model: SufficiencyModel,
    costs: ControllerCosts,
    acquisition_costs: Mapping[str, float],
    feature_extractor: FeatureExtractor = extract_subset_features,
) -> tuple[EvidenceValueCandidate, ...]:
    """Rank one-step acquisitions by net reduction in expected loss."""

    if not isinstance(current, SubsetFeatureInput):
        raise TypeError("current must be SubsetFeatureInput")
    if not isinstance(remaining_evidence_ids, tuple) or not remaining_evidence_ids:
        raise Int3ExperimentError("remaining_evidence_ids must be a non-empty tuple")
    if len(remaining_evidence_ids) != len(set(remaining_evidence_ids)):
        raise Int3ExperimentError("remaining_evidence_ids must be unique")
    if not isinstance(model, SufficiencyModel):
        raise TypeError("model must be SufficiencyModel")
    if not model.is_fitted:
        raise SufficiencyModelNotFittedError("VoI planning requires a fitted model")
    if not isinstance(costs, ControllerCosts):
        raise TypeError("costs must be ControllerCosts")
    if not isinstance(acquisition_costs, Mapping):
        raise TypeError("acquisition_costs must be a mapping")
    if not callable(feature_extractor):
        raise TypeError("feature_extractor must be callable")

    eligible = current.eligible_evidence
    eligible_ids = tuple(item.evidence_id for item in eligible)
    eligible_by_id = {item.evidence_id: item for item in eligible}
    current_ids = frozenset(item.evidence_id for item in current.subset_evidence)
    for evidence_id in remaining_evidence_ids:
        if evidence_id not in eligible_by_id:
            raise Int3ExperimentError("remaining evidence must be eligible")
        if evidence_id in current_ids:
            raise Int3ExperimentError("remaining evidence must be absent from the subset")
        if evidence_id not in acquisition_costs:
            raise Int3ExperimentError(
                f"missing acquisition cost for evidence {evidence_id!r}"
            )

    current_probability = model.predict_proba(
        (model_feature_vector(feature_extractor(current)),)
    )[0]
    current_probability = probability(current_probability, "current_probability")
    baseline_decide = decide_expected_loss(
        p_sufficient=current_probability, costs=costs
    )
    baseline_review = costs.review
    baseline_best = min(baseline_decide, baseline_review)

    unranked: list[_UnrankedCandidate] = []
    for evidence_id in remaining_evidence_ids:
        selected = current_ids | {evidence_id}
        counterfactual_evidence = tuple(
            item for item in eligible if item.evidence_id in selected
        )
        counterfactual = replace(current, subset_evidence=counterfactual_evidence)
        after_probability = model.predict_proba(
            (model_feature_vector(feature_extractor(counterfactual)),)
        )[0]
        after_probability = probability(after_probability, "p_after")
        cost = positive_number(
            acquisition_costs[evidence_id], f"C_ACQUIRE({evidence_id})"
        )
        retrieval = retrieval_expected_loss(
            candidate=RetrievalCandidate(
                evidence_id=evidence_id,
                p_after=after_probability,
                acquisition_cost=cost,
            ),
            costs=costs,
        )
        unranked.append(
            _UnrankedCandidate(
                evidence_id=evidence_id,
                eligible_index=eligible_ids.index(evidence_id),
                current_probability=current_probability,
                counterfactual_probability=after_probability,
                delta_p=after_probability - current_probability,
                acquisition_cost=cost,
                after_decide_loss=retrieval.decide_after_loss,
                after_review_loss=retrieval.review_after_loss,
                after_best_terminal_action=retrieval.best_terminal_action,
                after_best_terminal_loss=retrieval.best_terminal_loss,
                expected_loss_after_acquisition=retrieval.total_expected_loss,
                net_voi=baseline_best - retrieval.total_expected_loss,
                counterfactual_evidence_ids=tuple(
                    item.evidence_id for item in counterfactual_evidence
                ),
            )
        )

    ordered = sorted(
        unranked,
        key=lambda item: (-item.net_voi, -item.delta_p, item.eligible_index),
    )
    return tuple(
        EvidenceValueCandidate(
            rank=index,
            evidence_id=item.evidence_id,
            current_probability=item.current_probability,
            counterfactual_probability=item.counterfactual_probability,
            delta_p=item.delta_p,
            acquisition_cost=item.acquisition_cost,
            baseline_decide_loss=baseline_decide,
            baseline_review_loss=baseline_review,
            baseline_best_terminal_loss=baseline_best,
            after_decide_loss=item.after_decide_loss,
            after_review_loss=item.after_review_loss,
            after_best_terminal_action=item.after_best_terminal_action,
            after_best_terminal_loss=item.after_best_terminal_loss,
            expected_loss_after_acquisition=item.expected_loss_after_acquisition,
            net_voi=item.net_voi,
            counterfactual_evidence_ids=item.counterfactual_evidence_ids,
        )
        for index, item in enumerate(ordered, start=1)
    )
