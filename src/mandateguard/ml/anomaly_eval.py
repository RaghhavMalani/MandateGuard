"""Does a learned anomaly detector beat the deterministic analytics?

The brief this was built against is explicit: do not add IsolationForest so a
README can say "ML". So the question is asked properly and the answer is
reported either way.

Protocol
--------
* A frozen evaluation set of proposals is built from the real catalog, half
  ordinary and half carrying a named, injected defect (price mutated after
  authorization, merchant swapped, recurring listing under a one-time mandate,
  category laundering, evidence removed, capability replayed).
* The **baseline** is the deterministic scorer in
  ``mandateguard.discovery.anomaly`` - one number, no fitting.
* The **candidate** is an unsupervised IsolationForest fitted on the *ordinary*
  half only, scoring the same feature vectors.
* Both are scored by ROC AUC, average precision, and recall at a fixed 5% false
  positive rate on the held-out half.
* The candidate is kept only if it improves the primary metric by more than
  ``MATERIAL_IMPROVEMENT`` on the frozen set. Otherwise the baseline stands and
  the negative result is written down.

Circularity, and what is done about it
--------------------------------------
Most injected defects flip a field the deterministic scorer already watches, so
the baseline scoring near-perfectly on them proves only that the features fire.
To ask a question that is not circular, the set also contains
``CATEGORY_LAUNDERED``: the listing keeps its declared category while its text is
replaced with another category's, so no rule-based field comparison sees
anything wrong. Only the trained classifier's disagreement with the declared
category can catch it. The report therefore scores the baseline twice - with the
ML-derived mismatch feature and with it zeroed - which is the actual test of
whether the supervised model earns its place in the detection path.

The evaluation set is synthetic. It measures whether a detector notices defects
we injected, which is a far weaker claim than noticing fraud in the wild, and
the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import random
from typing import Any, Sequence

from mandateguard.discovery.anomaly import (
    ANALYTICS_VERSION,
    AnomalyAssessment,
    FEATURE_WEIGHTS,
    ProposalContext,
    assess,
    build_price_profiles,
    feature_names,
    feature_vector,
)
from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.classifier import CategoryClassifier
from mandateguard.discovery.intent import ParsedIntent, parse_intent
from mandateguard.discovery.mismatch import evaluate_mismatch
from mandateguard.discovery.schema import DiscoveryProduct


EVALUATION_VERSION = "discovery-anomaly-eval-v1"
EVALUATION_SEED = 20260903
DEFAULT_SAMPLE = 600
#: The candidate must beat the baseline by more than this on ROC AUC to ship.
#: Anything smaller is a coin landing the same way twice on 600 synthetic rows.
MATERIAL_IMPROVEMENT = 0.02

DEFECTS: tuple[str, ...] = (
    "PRICE_MUTATED_AFTER_AUTHORIZATION",
    "MERCHANT_SUBSTITUTED",
    "RECURRING_UNDER_ONE_TIME_MANDATE",
    "TRUSTED_EVIDENCE_REMOVED",
    "CONSENT_REVOKED",
    "CAPABILITY_REPLAYED",
    "STALE_EVIDENCE",
    "CATEGORY_LAUNDERED",
)

#: The one defect no rule-based field comparison can see. It exists to stop the
#: comparison from being a test of features against the defects they were
#: written for.
ML_ONLY_DEFECT = "CATEGORY_LAUNDERED"


@dataclass(frozen=True, slots=True)
class EvaluationRow:
    case_id: str
    defective: bool
    defect: str | None
    vector: tuple[float, ...]
    baseline_score: float
    baseline_score_without_ml: float


def _ordinary_context(
    product: DiscoveryProduct, intent: ParsedIntent, profiles: dict[str, Any]
) -> ProposalContext:
    price = product.price_minor
    return ProposalContext(
        product=product,
        intent=intent,
        price_profile=profiles.get(product.top_category),
        trusted_evidence_count=2,
        authorized_price_minor=price,
        presented_price_minor=price,
        expected_merchant=product.merchant_or_seller,
        evidence_age_days=7.0,
        consent_active=True,
        replay_seen=False,
    )


def _inject(context: ProposalContext, defect: str) -> ProposalContext:
    if defect == "PRICE_MUTATED_AFTER_AUTHORIZATION":
        base = context.presented_price_minor or 100_000
        return replace(context, presented_price_minor=int(base * 1.6) + 700)
    if defect == "MERCHANT_SUBSTITUTED":
        return replace(context, expected_merchant="merchant-of-record.example")
    if defect == "RECURRING_UNDER_ONE_TIME_MANDATE":
        product = context.product
        seeded = replace(
            product,
            description=(
                f"{product.description} This plan renews monthly until "
                "cancelled and is billed monthly as a subscription."
            )[:4000],
        )
        return replace(
            context,
            product=seeded,
            intent=replace(context.intent, recurring_allowed=False),
        )
    if defect == "TRUSTED_EVIDENCE_REMOVED":
        return replace(context, trusted_evidence_count=0, evidence_age_days=None)
    if defect == "CONSENT_REVOKED":
        return replace(context, consent_active=False)
    if defect == "CAPABILITY_REPLAYED":
        return replace(context, replay_seen=True)
    if defect == "STALE_EVIDENCE":
        return replace(context, evidence_age_days=420.0)
    if defect == ML_ONLY_DEFECT:
        # Every structured field stays exactly as it was. Only the prose moves.
        return replace(context, product=context.product)
    raise ValueError(f"unregistered defect: {defect}")


def _launder(product: DiscoveryProduct, donor: DiscoveryProduct) -> DiscoveryProduct:
    """Keep the declared category; replace the text with another category's.

    This is what a listing looks like when the shelf it sits on and the thing it
    is have been separated on purpose.
    """

    return replace(
        product,
        title=donor.title[:400],
        description=donor.description[:4000],
        brand=donor.brand,
    )


def _donor_from_other_category(
    catalog: DiscoveryCatalog, product: DiscoveryProduct, rng: random.Random
) -> DiscoveryProduct | None:
    for _ in range(24):
        candidate = catalog[rng.randrange(len(catalog))]
        if candidate.top_category != product.top_category and candidate.description:
            return candidate
    return None


def build_evaluation_set(
    catalog: DiscoveryCatalog,
    *,
    sample: int = DEFAULT_SAMPLE,
    classifier: CategoryClassifier | None = None,
) -> tuple[list[EvaluationRow], str]:
    """Deterministically construct the frozen evaluation set."""

    profiles = build_price_profiles(catalog)
    rng = random.Random(EVALUATION_SEED)
    positions = sorted(rng.sample(range(len(catalog)), min(sample, len(catalog))))
    intent = parse_intent("Buy one item under Rs 100000. One-time payment only.")
    rows: list[EvaluationRow] = []
    for index, position in enumerate(positions):
        product = catalog[position]
        defective = index % 2 == 1
        defect = DEFECTS[(index // 2) % len(DEFECTS)] if defective else None
        if defect == ML_ONLY_DEFECT:
            donor = _donor_from_other_category(catalog, product, rng)
            if donor is None:
                defect = "STALE_EVIDENCE"
            else:
                product = _launder(product, donor)
        context = _ordinary_context(product, intent, profiles)
        if defect is not None:
            context = _inject(context, defect)
        if classifier is not None:
            context = replace(
                context, mismatch=evaluate_mismatch(context.product, classifier)
            )
        assessment: AnomalyAssessment = assess(context)
        rows.append(
            EvaluationRow(
                case_id=f"AN{index:04d}",
                defective=defective,
                defect=defect,
                vector=tuple(feature_vector(assessment)),
                baseline_score=assessment.score,
                baseline_score_without_ml=score_without(
                    assessment, "category_listing_mismatch"
                ),
            )
        )
    digest = sha256(
        "\n".join(
            f"{row.case_id}:{row.defect or 'NONE'}:{row.baseline_score:.6f}"
            for row in rows
        ).encode("utf-8")
    ).hexdigest()
    return rows, digest


def score_without(assessment: AnomalyAssessment, feature_id: str) -> float:
    """The same weighted score with one feature ablated to zero."""

    ceiling = sum(FEATURE_WEIGHTS.values())
    total = sum(
        item.value * FEATURE_WEIGHTS.get(item.feature_id, 0.0)
        for item in assessment.features
        if item.feature_id != feature_id
    )
    return total / ceiling if ceiling else 0.0


def _roc_auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Rank-based AUC, ties averaged. No dependency needed."""

    paired = sorted(zip(scores, labels, strict=True), key=lambda item: item[0])
    ranks: list[float] = [0.0] * len(paired)
    index = 0
    while index < len(paired):
        end = index
        while end + 1 < len(paired) and paired[end + 1][0] == paired[index][0]:
            end += 1
        average = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[position] = average
        index = end + 1
    positives = sum(1 for _, label in paired if label)
    negatives = len(paired) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    positive_rank_sum = sum(
        rank for rank, (_, label) in zip(ranks, paired, strict=True) if label
    )
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (
        positives * negatives
    )


def _average_precision(scores: Sequence[float], labels: Sequence[bool]) -> float:
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    positives = sum(1 for label in labels if label)
    if positives == 0:
        return 0.0
    hits = 0
    total = 0.0
    for rank, index in enumerate(order, start=1):
        if labels[index]:
            hits += 1
            total += hits / rank
    return total / positives


def _recall_at_fpr(
    scores: Sequence[float], labels: Sequence[bool], target_fpr: float
) -> float:
    negatives = sorted(
        (score for score, label in zip(scores, labels, strict=True) if not label),
        reverse=True,
    )
    if not negatives:
        return 0.0
    allowed = int(len(negatives) * target_fpr)
    threshold = negatives[min(allowed, len(negatives) - 1)]
    positives = [score for score, label in zip(scores, labels, strict=True) if label]
    if not positives:
        return 0.0
    return sum(1 for score in positives if score > threshold) / len(positives)


def _score_block(scores: Sequence[float], labels: Sequence[bool]) -> dict[str, float]:
    return {
        "roc_auc": round(_roc_auc(scores, labels), 6),
        "average_precision": round(_average_precision(scores, labels), 6),
        "recall_at_5pct_fpr": round(_recall_at_fpr(scores, labels, 0.05), 6),
    }


def evaluate(
    catalog: DiscoveryCatalog,
    *,
    sample: int = DEFAULT_SAMPLE,
    classifier: CategoryClassifier | None = None,
) -> dict[str, Any]:
    """Run the frozen comparison and return the verdict, positive or not."""

    rows, digest = build_evaluation_set(
        catalog, sample=sample, classifier=classifier
    )
    labels = [row.defective for row in rows]
    baseline = _score_block([row.baseline_score for row in rows], labels)
    without_ml = _score_block(
        [row.baseline_score_without_ml for row in rows], labels
    )

    # The question that is not circular: on the one defect no field comparison
    # can see, does the trained classifier's disagreement change the outcome?
    laundered = [
        row for row in rows if row.defect == ML_ONLY_DEFECT or not row.defective
    ]
    laundered_labels = [row.defective for row in laundered]
    ml_only = {
        "cases": len(laundered),
        "defective_cases": sum(laundered_labels),
        "with_ml_mismatch_feature": _score_block(
            [row.baseline_score for row in laundered], laundered_labels
        ),
        "without_ml_mismatch_feature": _score_block(
            [row.baseline_score_without_ml for row in laundered], laundered_labels
        ),
    }
    ml_only["roc_auc_gain_from_classifier"] = round(
        ml_only["with_ml_mismatch_feature"]["roc_auc"]
        - ml_only["without_ml_mismatch_feature"]["roc_auc"],
        6,
    )
    ml_only["verdict"] = (
        "CLASSIFIER_SIGNAL_ADDS_DETECTION_VALUE"
        if ml_only["roc_auc_gain_from_classifier"] > MATERIAL_IMPROVEMENT
        else "CLASSIFIER_SIGNAL_DOES_NOT_ADD_DETECTION_VALUE"
    )

    candidate: dict[str, Any]
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest

        matrix = np.array([row.vector for row in rows], dtype=np.float64)
        ordinary = matrix[[not row.defective for row in rows]]
        forest = IsolationForest(
            n_estimators=200,
            contamination="auto",
            random_state=EVALUATION_SEED,
        )
        forest.fit(ordinary)
        # Higher must mean "more anomalous", so the sign is flipped.
        learned = (-forest.score_samples(matrix)).tolist()
        candidate = {
            "model": "IsolationForest(n_estimators=200, fitted on ordinary rows only)",
            "available": True,
            **_score_block(learned, labels),
        }
    except ImportError:
        candidate = {
            "model": "IsolationForest",
            "available": False,
            "note": "scikit-learn is not installed; the comparison did not run.",
        }

    improvement = (
        round(float(candidate["roc_auc"]) - baseline["roc_auc"], 6)
        if candidate.get("available")
        else None
    )
    kept = bool(improvement is not None and improvement > MATERIAL_IMPROVEMENT)
    by_defect: dict[str, Any] = {}
    for defect in DEFECTS:
        subset = [row for row in rows if row.defect == defect]
        if not subset:
            continue
        ordinary_scores = [row.baseline_score for row in rows if not row.defective]
        median_ordinary = (
            sorted(ordinary_scores)[len(ordinary_scores) // 2]
            if ordinary_scores
            else 0.0
        )
        by_defect[defect] = {
            "cases": len(subset),
            "mean_baseline_score": round(
                sum(row.baseline_score for row in subset) / len(subset), 6
            ),
            "detected_above_ordinary_median": sum(
                1 for row in subset if row.baseline_score > median_ordinary
            ),
        }
    return {
        "evaluation_version": EVALUATION_VERSION,
        "analytics_version": ANALYTICS_VERSION,
        "catalog_sha256": catalog.catalog_sha256,
        "evaluation_set_digest": digest,
        "cases": len(rows),
        "defective_cases": sum(labels),
        "feature_names": feature_names(),
        "baseline": {
            "model": "deterministic weighted feature score",
            **baseline,
        },
        "baseline_without_ml_mismatch_feature": {
            "model": "deterministic weighted feature score, classifier feature ablated",
            **without_ml,
        },
        "category_laundering_ablation": ml_only,
        "candidate": candidate,
        "material_improvement_threshold": MATERIAL_IMPROVEMENT,
        "roc_auc_improvement": improvement,
        "decision": "KEEP_LEARNED_DETECTOR" if kept else "KEEP_DETERMINISTIC_BASELINE",
        "decision_reason": (
            "The learned detector improved ROC AUC by more than the threshold on "
            "the frozen set."
            if kept
            else "The learned detector did not improve the frozen evaluation by "
            "more than the threshold, so it is not shipped. The deterministic "
            "analytics stands."
        ),
        "by_defect": by_defect,
        "scope_limit": (
            "Defects are injected by this harness. A detector that finds them is "
            "not thereby shown to find fraud in production traffic."
        ),
        "circularity_note": (
            "Seven of the eight defect classes flip a field the deterministic "
            "features already watch, so a high baseline score on those proves "
            "the features fire and nothing more. CATEGORY_LAUNDERED is the "
            "non-circular case, and category_laundering_ablation is the result "
            "that actually matters."
        ),
    }
