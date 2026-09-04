"""The evaluation machinery: frozen splits, retrieval scoring, anomaly comparison.

These tests check the *protocol*, not the numbers. A protocol that can be
accidentally loosened is how an honest evaluation turns into a flattering one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.ml.anomaly_eval import (
    DEFECTS,
    EVALUATION_SEED,
    MATERIAL_IMPROVEMENT,
    ML_ONLY_DEFECT,
    _average_precision,
    _recall_at_fpr,
    _roc_auc,
    build_evaluation_set,
    evaluate,
    score_without,
)
from mandateguard.ml.retrieval_eval import (
    CONFIGURATIONS,
    NOT_A_SAFETY_METRIC,
    _score_ranking,
    relevant_documents,
)
from mandateguard.ml.scale_bench import (
    BENCHMARK_SEED,
    _percentile,
)
from mandateguard.ml.splits import (
    PARTITIONS,
    SPLIT_VERSION,
    TRAIN_FRACTION,
    assignment_value,
    freeze_split,
    stratified_split,
    verify_frozen_split,
)

from tests.discovery_factories import build_catalog, build_product


# --------------------------------------------------------------------------
# Frozen split
# --------------------------------------------------------------------------


def test_split_membership_depends_only_on_the_identifier() -> None:
    assert assignment_value("flipkart.abc") == assignment_value("flipkart.abc")
    assert assignment_value("flipkart.abc") != assignment_value("flipkart.abd")
    assert 0.0 <= assignment_value("flipkart.abc") < 1.0


def test_the_split_is_stable_under_row_reordering() -> None:
    labels = ["A"] * 60 + ["B"] * 40
    ids = [f"id-{index:04d}" for index in range(100)]
    first = stratified_split(labels, ids)
    order = list(range(100))[::-1]
    second = stratified_split([labels[i] for i in order], [ids[i] for i in order])
    assert first.test_digest == second.test_digest
    assert first.sizes() == second.sizes()


def test_every_partition_is_disjoint_and_covers_every_row() -> None:
    labels = ["A"] * 60 + ["B"] * 40
    ids = [f"id-{index:04d}" for index in range(100)]
    split = stratified_split(labels, ids)
    combined = set(split.train) | set(split.validation) | set(split.test)
    assert combined == set(range(100))
    assert len(split.train) + len(split.validation) + len(split.test) == 100
    assert not set(split.train) & set(split.test)
    assert not set(split.validation) & set(split.test)


def test_each_label_is_present_in_every_partition() -> None:
    labels = ["A"] * 60 + ["B"] * 40
    ids = [f"id-{index:04d}" for index in range(100)]
    split = stratified_split(labels, ids)
    for partition in PARTITIONS:
        assert split.label_support["A"][partition] > 0
        assert split.label_support["B"][partition] > 0
    assert abs(len(split.train) / 100 - TRAIN_FRACTION) < 0.05


def test_a_moved_test_partition_refuses_to_report_metrics(tmp_path: Path) -> None:
    ids = [f"id-{index:04d}" for index in range(60)]
    labels = ["A"] * 60
    split = stratified_split(labels, ids)
    manifest = tmp_path / "split.json"
    freeze_split(split, manifest, catalog_sha256="0" * 64)
    verify_frozen_split(split, manifest)

    moved = stratified_split(labels, [f"other-{index:04d}" for index in range(60)])
    with pytest.raises(RuntimeError) as error:
        verify_frozen_split(moved, manifest)
    assert "refusing to report test metrics" in str(error.value)


def test_evaluating_without_freezing_first_is_refused(tmp_path: Path) -> None:
    split = stratified_split(["A"] * 20, [f"id-{i}" for i in range(20)])
    with pytest.raises(RuntimeError):
        verify_frozen_split(split, tmp_path / "never-written.json")


def test_the_frozen_manifest_records_what_would_be_needed_to_reproduce_it(
    tmp_path: Path,
) -> None:
    split = stratified_split(["A"] * 40 + ["B"] * 20, [f"id-{i:03d}" for i in range(60)])
    manifest = tmp_path / "split.json"
    freeze_split(split, manifest, catalog_sha256="a" * 64)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["split_version"] == SPLIT_VERSION
    assert payload["catalog_sha256"] == "a" * 64
    assert payload["test_id_digest"] == split.test_digest
    assert set(payload["sizes"]) == set(PARTITIONS)
    assert payload["label_support"]["A"]["total"] == 40


# --------------------------------------------------------------------------
# Retrieval evaluation
# --------------------------------------------------------------------------


def test_relevance_predicates_are_applied_to_the_whole_catalog() -> None:
    catalog = build_catalog()
    relevant = relevant_documents(catalog, {"require_title_any": ["lamp"]})
    assert len(relevant) == 2
    assert relevant_documents(catalog, {"require_title_any": ["nothing here"]}) == frozenset()


def test_a_predicate_price_ceiling_excludes_unpriced_listings() -> None:
    catalog = build_catalog(
        (
            build_product(source_product_id="a", price_minor=50_000),
            build_product(source_product_id="b", price_minor=None, title="Mystery Lamp"),
        )
    )
    relevant = relevant_documents(
        catalog, {"require_title_any": ["lamp"], "max_price_minor": 100_000}
    )
    assert relevant == frozenset({0})


def test_recall_is_capped_by_the_ranking_length_and_named_as_such() -> None:
    """With 100 relevant items, a top-5 ranking cannot exceed 5 of them."""

    relevant = frozenset(range(100))
    recall5, recall10, precision5, success, reciprocal = _score_ranking(
        list(range(10)), relevant
    )
    assert recall5 == 1.0
    assert recall10 == 1.0
    assert precision5 == 1.0
    assert success is True
    assert reciprocal == 1.0


def test_a_ranking_with_nothing_relevant_scores_zero_not_an_error() -> None:
    recall5, recall10, precision5, success, reciprocal = _score_ranking(
        [500, 501], frozenset({1, 2})
    )
    assert (recall5, recall10, precision5, success, reciprocal) == (0.0, 0.0, 0.0, False, 0.0)


def test_reciprocal_rank_reflects_the_first_relevant_position() -> None:
    _, _, _, _, reciprocal = _score_ranking([9, 8, 1], frozenset({1}))
    assert reciprocal == pytest.approx(1 / 3)


def test_the_sweep_spans_lexical_only_through_dense_only() -> None:
    alphas = [alpha for _, alpha in CONFIGURATIONS]
    assert 1.0 in alphas
    assert 0.0 in alphas
    assert len(alphas) >= 4


def test_the_retrieval_disclaimer_denies_it_is_an_authorization_metric() -> None:
    assert "authorization correctness" in NOT_A_SAFETY_METRIC
    assert "never" in NOT_A_SAFETY_METRIC or "nothing" in NOT_A_SAFETY_METRIC


# --------------------------------------------------------------------------
# Ranking metrics
# --------------------------------------------------------------------------


def test_roc_auc_is_one_for_a_perfect_separation_and_half_for_noise() -> None:
    assert _roc_auc([0.1, 0.2, 0.8, 0.9], [False, False, True, True]) == 1.0
    assert _roc_auc([0.5, 0.5, 0.5, 0.5], [False, False, True, True]) == 0.5
    assert _roc_auc([0.9, 0.8, 0.2, 0.1], [False, False, True, True]) == 0.0


def test_average_precision_and_recall_at_fpr_agree_on_a_clean_split() -> None:
    scores = [0.9, 0.8, 0.2, 0.1]
    labels = [True, True, False, False]
    assert _average_precision(scores, labels) == 1.0
    assert _recall_at_fpr(scores, labels, 0.05) == 1.0


def test_a_percentile_of_an_empty_sample_is_zero_not_an_exception() -> None:
    assert _percentile([], 0.95) == 0.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.5) in {2.0, 3.0}


# --------------------------------------------------------------------------
# Anomaly comparison protocol
# --------------------------------------------------------------------------


def test_the_evaluation_set_is_balanced_and_deterministic() -> None:
    catalog = build_catalog(
        tuple(build_product(source_product_id=f"p{index:03d}") for index in range(40))
    )
    first, digest = build_evaluation_set(catalog, sample=40)
    second, second_digest = build_evaluation_set(catalog, sample=40)
    assert digest == second_digest
    assert [row.defect for row in first] == [row.defect for row in second]
    assert sum(row.defective for row in first) == 20


def test_the_defect_catalog_includes_the_one_no_field_comparison_can_see() -> None:
    assert ML_ONLY_DEFECT in DEFECTS
    assert ML_ONLY_DEFECT == "CATEGORY_LAUNDERED"


def test_ablating_a_feature_lowers_the_score_it_contributed_to() -> None:
    from mandateguard.discovery.anomaly import ProposalContext, assess, build_price_profiles
    from mandateguard.discovery.intent import parse_intent
    from mandateguard.discovery.mismatch import MismatchSignal
    from mandateguard.discovery.classifier import CategoryPrediction

    product = build_product()
    prediction = CategoryPrediction(
        label="Clothing", margin=0.9, ranked=(("Clothing", 1.0),), matched_terms=4,
        model_id="test",
    )
    mismatch = MismatchSignal(
        listing_category="Home Decor & Festive Needs",
        predicted_category="Clothing",
        severity="HIGH",
        agrees=False,
        prediction=prediction,
        rationale="test",
    )
    assessment = assess(
        ProposalContext(
            product=product,
            intent=parse_intent("buy a lamp under Rs 2000"),
            price_profile=build_price_profiles(build_catalog()).get(product.top_category),
            mismatch=mismatch,
            trusted_evidence_count=2,
            consent_active=True,
        )
    )
    assert score_without(assessment, "category_listing_mismatch") < assessment.score


def test_the_material_improvement_threshold_is_not_zero() -> None:
    """A threshold of zero would keep any model that got lucky once."""

    assert MATERIAL_IMPROVEMENT > 0.0


def test_the_comparison_reports_a_decision_and_its_reason() -> None:
    catalog = build_catalog(
        tuple(
            build_product(source_product_id=f"p{index:03d}", price_minor=10_000 + index)
            for index in range(60)
        )
    )
    report = evaluate(catalog, sample=60)
    assert report["decision"] in {"KEEP_LEARNED_DETECTOR", "KEEP_DETERMINISTIC_BASELINE"}
    assert report["decision_reason"]
    assert report["evaluation_set_digest"]
    assert "circularity_note" in report
    assert "scope_limit" in report
    assert report["category_laundering_ablation"]["verdict"].startswith("CLASSIFIER_SIGNAL")


def test_the_seeds_are_fixed_so_the_evaluation_is_reproducible() -> None:
    assert isinstance(EVALUATION_SEED, int)
    assert isinstance(BENCHMARK_SEED, int)
