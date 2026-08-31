from __future__ import annotations

import math

import pytest

from mandateguard.engineering.int3.controller import (
    ControllerAction,
    ControllerCosts,
    select_controller_action,
)
from mandateguard.engineering.int3.dataset import decision_stable
from mandateguard.engineering.int3.demo import run_offline_demo
from mandateguard.engineering.int3.features import (
    FEATURE_NAMES,
    SubsetFeatureInput,
)
from mandateguard.engineering.int3.metrics import (
    brier_score,
    evaluate_sufficiency,
    roc_auc,
)
from mandateguard.engineering.int3.model import (
    SufficiencyModel,
    SufficiencyModelNotFittedError,
    SufficiencyTrainingDataError,
)
from mandateguard.engineering.int3.models import Int3ExperimentError
from mandateguard.engineering.int3.safety import (
    SufficiencyRoute,
    enforce_sufficiency_safety_boundary,
)
from mandateguard.engineering.int3.voi import rank_evidence_by_voi
from mandateguard.models.decision import DecisionAction
from mandateguard.semantic.evidence import SemanticEvidenceEntry


def _row(*, evidence_count: float) -> tuple[float, ...]:
    values = [0.0] * len(FEATURE_NAMES)
    values[FEATURE_NAMES.index("evidence_count")] = evidence_count
    return tuple(values)


def _synthetic_model_for_feature(name: str, weight: float) -> SufficiencyModel:
    coefficients = [0.0] * len(FEATURE_NAMES)
    coefficients[FEATURE_NAMES.index(name)] = weight
    return SufficiencyModel.from_coefficients(
        coefficients=coefficients,
        intercept=-3.0,
    )


def test_decision_stable_compares_actions_not_semantic_expectations():
    assert decision_stable(
        subset_final_action="BLOCK", full_reference_action="BLOCK"
    ) is True
    assert decision_stable(
        subset_final_action="ALLOW", full_reference_action="BLOCK"
    ) is False
    assert decision_stable(
        subset_final_action="REVIEW", full_reference_action="REVIEW"
    ) is True
    with pytest.raises(TypeError):
        decision_stable(  # type: ignore[call-arg]
            subset_final_action="ALLOW",
            full_reference_action="ALLOW",
            engineering_expectation="PASS",
        )


def test_logistic_model_exposes_fit_predict_proba_and_predict():
    model = SufficiencyModel()
    rows = (_row(evidence_count=0.0), _row(evidence_count=1.0))
    fitted = model.fit(rows, (False, True))
    assert model.is_fitted is False
    assert fitted.is_fitted is True
    assert fitted.training_row_count == 2
    assert set(fitted.weights()) == set(FEATURE_NAMES)
    probabilities = fitted.predict_proba(rows)
    assert len(probabilities) == 2
    assert all(0.0 <= value <= 1.0 for value in probabilities)
    assert probabilities[0] < probabilities[1]
    assert fitted.predict(rows) == (False, True)


def test_logistic_model_refuses_unfitted_or_one_class_use():
    with pytest.raises(SufficiencyModelNotFittedError):
        SufficiencyModel().predict_proba((_row(evidence_count=1.0),))
    with pytest.raises(SufficiencyTrainingDataError, match="both"):
        SufficiencyModel().fit(
            (_row(evidence_count=0.0), _row(evidence_count=1.0)),
            (True, True),
        )


def test_brier_score_and_roc_auc_are_probability_aware():
    probabilities = (0.1, 0.8, 0.4, 0.9)
    targets = (False, True, False, True)
    expected = (0.1**2 + 0.2**2 + 0.4**2 + 0.1**2) / 4
    assert brier_score(probabilities, targets) == pytest.approx(expected)
    assert roc_auc(probabilities, targets) == 1.0
    assert roc_auc((0.2, 0.8), (True, True)) is None


def test_false_sufficient_false_insufficient_and_review_metrics():
    metrics = evaluate_sufficiency(
        probabilities=(0.9, 0.8, 0.4, 0.2),
        targets=(True, False, True, False),
        predictions=(True, True, False, False),
        escalations=(False, False, True, True),
    )
    assert metrics.false_sufficient_count == 1
    assert metrics.false_sufficient_rate == 0.5
    assert metrics.false_sufficient_rate_among_unstable == 0.5
    assert metrics.false_sufficient_rate_overall == 0.25
    assert metrics.false_insufficient_count == 1
    assert metrics.false_insufficient_rate == 0.5
    assert metrics.false_insufficient_rate_among_stable == 0.5
    assert metrics.false_insufficient_rate_overall == 0.25
    assert metrics.review_escalation_count == 2
    assert metrics.review_escalation_rate == 0.5
    assert not hasattr(metrics, "accuracy")


def test_expected_loss_controller_calculates_every_action_explicitly():
    costs = ControllerCosts(unstable_decision=10.0, retrieve=1.5, review=3.0)
    result = select_controller_action(p_sufficient=0.7, costs=costs)
    assert result.loss_for(ControllerAction.DECIDE) == pytest.approx(3.0)
    assert result.loss_for(ControllerAction.RETRIEVE_MORE) == 1.5
    assert result.loss_for(ControllerAction.REVIEW) == 3.0
    assert result.selected_action is ControllerAction.RETRIEVE_MORE
    assert "DECIDE=(1-0.700000)*10.000000=3.000000" in result.reason


def test_expected_loss_controller_uses_explicit_safety_tie_order():
    result = select_controller_action(
        p_sufficient=1.0,
        costs=ControllerCosts(unstable_decision=1.0, retrieve=0.0, review=0.0),
    )
    assert result.selected_action is ControllerAction.REVIEW


def _voi_feature_input() -> SubsetFeatureInput:
    base = SemanticEvidenceEntry(
        evidence_id="base-evidence",
        merchant_id="merchant-1",
        sku=None,
        source_kind="merchant_terms",
        text="General merchant terms that are already present.",
    )
    low_value = SemanticEvidenceEntry(
        evidence_id="low-value-evidence",
        merchant_id="merchant-1",
        sku="sku-other",
        source_kind="product_page",
        text="Evidence about a different product with no relevant annotation.",
    )
    high_value = SemanticEvidenceEntry(
        evidence_id="high-value-evidence",
        merchant_id="merchant-1",
        sku="sku-1",
        source_kind="product_page",
        text="Evidence annotated as required and relevant to the requested SKU.",
    )
    return SubsetFeatureInput(
        query_id="synthetic-query",
        eligible_evidence=(base, low_value, high_value),
        subset_evidence=(base,),
        transaction_skus=("sku-1",),
        constraint_kinds=("purpose",),
        required_evidence_ids=("high-value-evidence",),
        relevant_evidence_ids=("high-value-evidence",),
        score_surface=None,
    )


def test_voi_constructs_counterfactual_features_and_ranks_probability_gain_per_cost():
    current = _voi_feature_input()
    model = _synthetic_model_for_feature("relevant_annotation_fraction", 6.0)
    ranked = rank_evidence_by_voi(
        current=current,
        remaining_evidence_ids=("low-value-evidence", "high-value-evidence"),
        model=model,
        acquisition_costs={
            "low-value-evidence": 0.5,
            "high-value-evidence": 2.0,
        },
    )
    assert tuple(item.evidence_id for item in ranked) == (
        "high-value-evidence",
        "low-value-evidence",
    )
    assert ranked[0].delta_p > 0.0
    assert ranked[0].voi == pytest.approx(
        ranked[0].delta_p / ranked[0].acquisition_cost
    )
    assert ranked[0].counterfactual_evidence_ids == (
        "base-evidence",
        "high-value-evidence",
    )
    assert ranked[1].delta_p == pytest.approx(0.0)


@pytest.mark.parametrize(
    "costs",
    [
        {"low-value-evidence": 1.0},
        {"low-value-evidence": 1.0, "high-value-evidence": 0.0},
        {"low-value-evidence": 1.0, "high-value-evidence": math.inf},
    ],
)
def test_voi_requires_a_finite_positive_cost_for_every_candidate(costs):
    with pytest.raises(Int3ExperimentError, match="cost"):
        rank_evidence_by_voi(
            current=_voi_feature_input(),
            remaining_evidence_ids=("low-value-evidence", "high-value-evidence"),
            model=_synthetic_model_for_feature(
                "relevant_annotation_fraction", 6.0
            ),
            acquisition_costs=costs,
        )


def test_voi_requires_a_fitted_model():
    with pytest.raises(SufficiencyModelNotFittedError):
        rank_evidence_by_voi(
            current=_voi_feature_input(),
            remaining_evidence_ids=("high-value-evidence",),
            model=SufficiencyModel(),
            acquisition_costs={"high-value-evidence": 1.0},
        )


def test_learned_layer_cannot_override_tier_a_or_b_block_or_review():
    controller = select_controller_action(
        p_sufficient=0.999,
        costs=ControllerCosts(unstable_decision=100.0, retrieve=10.0, review=20.0),
    )
    assert controller.selected_action is ControllerAction.DECIDE
    blocked = enforce_sufficiency_safety_boundary(
        tier_ab_action=DecisionAction.BLOCK,
        controller=controller,
    )
    reviewed = enforce_sufficiency_safety_boundary(
        tier_ab_action=DecisionAction.REVIEW,
        controller=controller,
    )
    continued = enforce_sufficiency_safety_boundary(
        tier_ab_action=DecisionAction.ALLOW,
        controller=controller,
    )
    assert blocked.selected_route is SufficiencyRoute.BLOCK
    assert reviewed.selected_route is SufficiencyRoute.REVIEW
    assert continued.selected_route is SufficiencyRoute.PROCEED_TO_SEMANTIC
    assert all(route.value != "ALLOW" for route in SufficiencyRoute)


def test_offline_demo_produces_the_three_required_decisions():
    scenarios = run_offline_demo()
    assert tuple(item.scenario_id for item in scenarios) == ("A", "B", "C")
    assert tuple(item.controller.selected_action for item in scenarios) == (
        ControllerAction.DECIDE,
        ControllerAction.RETRIEVE_MORE,
        ControllerAction.REVIEW,
    )
    assert scenarios[1].voi is not None and scenarios[1].voi > scenarios[2].voi
