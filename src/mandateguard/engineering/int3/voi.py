"""Counterfactual value-of-information ranking for missing evidence.

The planner is offline and side-effect free.  It constructs the feature vector
that *would* exist after adding each remaining eligible evidence item, asks an
already-fitted sufficiency model for probabilities, and ranks candidates by
probability gain per unit acquisition cost.  It never fetches evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Callable, Mapping

from mandateguard.engineering.int3.features import (
    SubsetFeatureInput,
    extract_subset_features,
    feature_vector,
)
from mandateguard.engineering.int3.model import (
    SufficiencyModel,
    SufficiencyModelNotFittedError,
)
from mandateguard.engineering.int3.models import (
    Int3ExperimentError,
    positive_number,
    probability,
)


FeatureExtractor = Callable[[SubsetFeatureInput], Mapping[str, float]]


@dataclass(frozen=True, slots=True)
class EvidenceValueCandidate:
    """One missing evidence item and its counterfactual model value."""

    rank: int
    evidence_id: str
    current_probability: float
    counterfactual_probability: float
    delta_p: float
    acquisition_cost: float
    voi: float
    counterfactual_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise Int3ExperimentError("rank must be a positive integer")
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise Int3ExperimentError("evidence_id must be non-empty")
        current = probability(self.current_probability, "current_probability")
        counterfactual = probability(
            self.counterfactual_probability, "counterfactual_probability"
        )
        cost = positive_number(self.acquisition_cost, "acquisition_cost")
        expected_delta = counterfactual - current
        if not math.isclose(self.delta_p, expected_delta, rel_tol=0.0, abs_tol=1e-12):
            raise Int3ExperimentError("delta_p must equal counterfactual minus current")
        expected_voi = expected_delta / cost
        if not math.isclose(self.voi, expected_voi, rel_tol=0.0, abs_tol=1e-12):
            raise Int3ExperimentError("voi must equal delta_p / acquisition_cost")
        if (
            not isinstance(self.counterfactual_evidence_ids, tuple)
            or self.evidence_id not in self.counterfactual_evidence_ids
        ):
            raise Int3ExperimentError(
                "counterfactual_evidence_ids must contain the candidate"
            )


@dataclass(frozen=True, slots=True)
class _UnrankedCandidate:
    evidence_id: str
    eligible_index: int
    current_probability: float
    counterfactual_probability: float
    delta_p: float
    acquisition_cost: float
    voi: float
    counterfactual_evidence_ids: tuple[str, ...]


def rank_evidence_by_voi(
    *,
    current: SubsetFeatureInput,
    remaining_evidence_ids: tuple[str, ...],
    model: SufficiencyModel,
    acquisition_costs: Mapping[str, float],
    feature_extractor: FeatureExtractor = extract_subset_features,
) -> tuple[EvidenceValueCandidate, ...]:
    """Rank one-step evidence additions by ``delta_p / acquisition_cost``.

    ``remaining_evidence_ids`` may be a selected portion of the eligible
    complement, but every listed item must be eligible and absent from the
    current subset.  Counterfactual subsets always preserve frozen eligible
    order, so feature construction and output are deterministic.
    """

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

    current_features = feature_extractor(current)
    current_probability = model.predict_proba((feature_vector(current_features),))[0]
    current_probability = probability(current_probability, "current_probability")

    unranked: list[_UnrankedCandidate] = []
    for evidence_id in remaining_evidence_ids:
        selected = current_ids | {evidence_id}
        counterfactual_evidence = tuple(
            item for item in eligible if item.evidence_id in selected
        )
        counterfactual = replace(current, subset_evidence=counterfactual_evidence)
        counterfactual_features = feature_extractor(counterfactual)
        counterfactual_probability = model.predict_proba(
            (feature_vector(counterfactual_features),)
        )[0]
        counterfactual_probability = probability(
            counterfactual_probability, "counterfactual_probability"
        )
        cost = positive_number(
            acquisition_costs[evidence_id], f"acquisition_cost({evidence_id})"
        )
        delta = counterfactual_probability - current_probability
        unranked.append(
            _UnrankedCandidate(
                evidence_id=evidence_id,
                eligible_index=eligible_ids.index(evidence_id),
                current_probability=current_probability,
                counterfactual_probability=counterfactual_probability,
                delta_p=delta,
                acquisition_cost=cost,
                voi=delta / cost,
                counterfactual_evidence_ids=tuple(
                    item.evidence_id for item in counterfactual_evidence
                ),
            )
        )

    ordered = sorted(
        unranked,
        key=lambda item: (-item.voi, -item.delta_p, item.eligible_index),
    )
    return tuple(
        EvidenceValueCandidate(
            rank=index,
            evidence_id=item.evidence_id,
            current_probability=item.current_probability,
            counterfactual_probability=item.counterfactual_probability,
            delta_p=item.delta_p,
            acquisition_cost=item.acquisition_cost,
            voi=item.voi,
            counterfactual_evidence_ids=item.counterfactual_evidence_ids,
        )
        for index, item in enumerate(ordered, start=1)
    )
