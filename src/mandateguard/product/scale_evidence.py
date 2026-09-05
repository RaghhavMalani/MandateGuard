"""Measured numbers, read from the artifacts that produced them.

Every figure the product shows under SYSTEM SCALE and MODEL QUALITY is loaded
from a recorded evaluation artifact at startup. Nothing here is a literal typed
into the interface, because a literal is a number that keeps its value after the
thing it described has changed.

If an artifact is missing, the corresponding block reports itself unavailable
rather than falling back to a remembered value.

Two rules about naming, both learned the hard way:

*   A latency field named ``retrieval`` carries retrieval latency. The scale
    benchmark measures two different things - the retrieval call alone, and the
    whole discovery request including intent parsing, classification, mismatch,
    anomaly and transactability - and they are ~2 ms apart. They are surfaced as
    separate fields with separate labels; neither is loaded into the other.
*   A metric is named for what was computed. The diversity figure counts unique
    *titles*, so it is called a title metric, not "distinct products".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ARTIFACT_DIR = Path("artifacts") / "engineering" / "discovery"
RETRIEVAL_REPORT = "retrieval_evaluation.json"
ANOMALY_REPORT = "anomaly_evaluation.json"
SCALE_REPORT = "scale_benchmark.json"
TRAINING_REPORT = "training_report.json"
AUTHORIZATION_SCALE_ARTIFACT_DIR = (
    Path("artifacts") / "engineering" / "authorization-scale"
)
AUTHORIZATION_SCALE_REPORT = "benchmark.json"
AUTHORIZATION_SCALE_FREEZE_DIR = Path("data") / "eval" / "authorization-scale"
AUTHORIZATION_SCALE_FREEZE = "WORLD_FREEZE.json"

#: The four kinds of evidence this product has, kept apart on purpose. Merging
#: them is how "709 tests" ends up being presented as scale.
EVIDENCE_KINDS = (
    "SYSTEM_SCALE",
    "MODEL_QUALITY",
    "AUTHORIZATION_EVIDENCE",
    "ENGINEERING_QUALITY",
)

#: The configuration the runtime actually serves.
SHIPPED_CONFIGURATION = "lexical_only_alpha_1.00__deduplicated"

MEASUREMENT_SCOPE = (
    "Single process, one machine, no concurrency, no network, measured on the "
    "imported catalog. Not a distributed-throughput claim and not extrapolated."
)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _best_retrieval_configuration(report: Mapping[str, Any]) -> dict[str, Any] | None:
    """The configuration the runtime actually uses: BM25, deduplicated."""

    configurations = report.get("configurations")
    if not isinstance(configurations, Mapping):
        return None
    preferred = configurations.get(SHIPPED_CONFIGURATION)
    if isinstance(preferred, Mapping):
        return dict(preferred)
    for value in configurations.values():
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _authorization_scale(repository_root: Path) -> dict[str, Any]:
    """Load the measured primary rung and prove that it matches its freeze.

    The report also contains a larger exploratory rung.  It is deliberately not
    substituted for the preregistered primary count: the public claim is the
    measured rung that the frozen world names as primary.
    """

    report = _read(
        repository_root
        / AUTHORIZATION_SCALE_ARTIFACT_DIR
        / AUTHORIZATION_SCALE_REPORT
    )
    freeze = _read(
        repository_root
        / AUTHORIZATION_SCALE_FREEZE_DIR
        / AUTHORIZATION_SCALE_FREEZE
    )
    unavailable = {
        "available": False,
        "reason": "The frozen authorization-scale benchmark is unavailable.",
    }
    if report is None or freeze is None:
        return unavailable
    if (
        report.get("freeze_payload_sha256") != freeze.get("freeze_payload_sha256")
        or report.get("taxonomy_sha256") != freeze.get("taxonomy_sha256")
    ):
        return {
            "available": False,
            "reason": "The authorization-scale report does not match its frozen world.",
        }
    primary_count = freeze.get("primary_case_count")
    if isinstance(primary_count, bool) or not isinstance(primary_count, int):
        return unavailable
    frozen_rungs = freeze.get("scale_ladder")
    measured_rungs = report.get("ladder")
    if not isinstance(frozen_rungs, list) or not isinstance(measured_rungs, list):
        return unavailable
    frozen = next(
        (
            item
            for item in frozen_rungs
            if isinstance(item, Mapping) and item.get("case_count") == primary_count
        ),
        None,
    )
    measured = next(
        (
            item
            for item in measured_rungs
            if isinstance(item, Mapping) and item.get("case_count") == primary_count
        ),
        None,
    )
    if frozen is None or measured is None:
        return unavailable
    counters = measured.get("counters")
    if not isinstance(counters, Mapping):
        return unavailable
    if (
        measured.get("freeze_status") != "MATCHES_FREEZE"
        or measured.get("case_descriptor_stream_sha256")
        != frozen.get("case_descriptor_stream_sha256")
        or counters.get("total_cases") != primary_count
        or counters.get("target_invariant_agreement") != primary_count
    ):
        return {
            "available": False,
            "reason": "The frozen authorization-scale measurement failed validation.",
        }
    return {
        "available": True,
        "cases": primary_count,
        "target_invariant_agreement": counters.get(
            "target_invariant_agreement"
        ),
        "scope": (
            "Synthetic authorization-scale cases; one process, one machine, "
            "sequential, no concurrency, and no network."
        ),
        "source": (
            f"{AUTHORIZATION_SCALE_ARTIFACT_DIR.as_posix()}/"
            f"{AUTHORIZATION_SCALE_REPORT}"
        ),
        "freeze_source": (
            f"{AUTHORIZATION_SCALE_FREEZE_DIR.as_posix()}/"
            f"{AUTHORIZATION_SCALE_FREEZE}"
        ),
    }


def system_scale(
    *, repository_root: Path, models_dir: Path
) -> dict[str, Any]:
    """Catalog size, index size, and measured latency at both boundaries."""

    scale = _read(repository_root / ARTIFACT_DIR / SCALE_REPORT)
    training = _read(models_dir / TRAINING_REPORT)
    retrieval = _read(repository_root / ARTIFACT_DIR / RETRIEVAL_REPORT)
    if scale is None and training is None:
        return {"available": False, "reason": "No scale benchmark has been recorded."}
    # Two distinct measurements, kept in two distinct fields.
    retrieval_latency = (scale or {}).get("retrieval_latency_ms", {}) or {}
    request_latency = (scale or {}).get("query_latency_ms", {}) or {}
    return {
        "available": True,
        "kind": "SYSTEM_SCALE",
        "catalog_listings": (scale or {}).get("catalog_listings")
        or (training or {}).get("catalog", {}).get("listings"),
        "categories": (scale or {}).get("categories"),
        "index_bytes": (scale or {}).get("index_bytes"),
        "catalog_bytes": (scale or {}).get("catalog_bytes"),
        "cold_load_seconds": (scale or {}).get("cold_load_seconds"),
        "resident_memory_mb": (scale or {}).get("resident_memory_mb"),
        "queries_executed": (scale or {}).get("queries_executed"),
        "queries_timed": (scale or {}).get("queries_timed"),
        "queries_per_second": (scale or {}).get("queries_per_second"),
        "retrieval_queries_per_second": (scale or {}).get(
            "retrieval_queries_per_second"
        ),
        # Retrieval alone: structured filters, BM25 over the frozen index, and
        # near-duplicate suppression.
        "retrieval_p50_ms": retrieval_latency.get("p50"),
        "retrieval_p95_ms": retrieval_latency.get("p95"),
        "retrieval_p99_ms": retrieval_latency.get("p99"),
        # The whole discovery request: the above plus intent parsing,
        # classification, mismatch, anomaly, and transactability per candidate.
        "request_p50_ms": request_latency.get("p50"),
        "request_p95_ms": request_latency.get("p95"),
        "request_p99_ms": request_latency.get("p99"),
        "evaluated_queries": (retrieval or {}).get("queries"),
        "authorization_scale": _authorization_scale(repository_root),
        "source": f"{ARTIFACT_DIR.as_posix()}/{SCALE_REPORT}",
        "caveat": MEASUREMENT_SCOPE,
        "latency_note": (
            "Retrieval percentiles time the retrieval call. Request percentiles "
            "time the whole discovery request. They are different measurements "
            "and are never substituted for one another."
        ),
    }


def model_quality(*, repository_root: Path, models_dir: Path) -> dict[str, Any]:
    """Classifier and retrieval quality, kept separate from authorization."""

    training = _read(models_dir / TRAINING_REPORT)
    retrieval = _read(repository_root / ARTIFACT_DIR / RETRIEVAL_REPORT)
    anomaly = _read(repository_root / ARTIFACT_DIR / ANOMALY_REPORT)
    if training is None and retrieval is None:
        return {"available": False, "reason": "No model evaluation has been recorded."}
    classifier = (training or {}).get("category_classifier", {})
    # The headline is the grouped product-family evaluation. The row-wise number
    # is carried alongside, labelled, so the earlier claim stays visible.
    grouped = classifier.get("grouped_family", {})
    row_wise = classifier.get("row_wise", {})
    headline = grouped.get("test", {}) or classifier.get("test", {})
    best = _best_retrieval_configuration(retrieval or {}) or {}
    return {
        "available": True,
        "kind": "MODEL_QUALITY",
        "classifier": {
            "model": classifier.get("selected_model"),
            "classes": classifier.get("class_count"),
            "evaluation": classifier.get(
                "headline_evaluation", "grouped_product_family_holdout"
            ),
            "accuracy": headline.get("accuracy"),
            "macro_f1": headline.get("macro_f1"),
            "weighted_f1": headline.get("weighted_f1"),
            "top_2_accuracy": headline.get("top_2_accuracy"),
            "train": grouped.get("sizes", {}).get("train"),
            "validation": grouped.get("sizes", {}).get("validation"),
            "test": grouped.get("sizes", {}).get("test"),
            "family_groups": grouped.get("group_counts", {}).get("total"),
            "family_key_version": grouped.get("family_key_version"),
            "split_note": grouped.get("caveat"),
            "row_wise": {
                "accuracy": row_wise.get("test", {}).get("accuracy"),
                "macro_f1": row_wise.get("test", {}).get("macro_f1"),
                "weighted_f1": row_wise.get("test", {}).get("weighted_f1"),
                "caveat": row_wise.get("caveat"),
            },
            "advisory_only": True,
        },
        "retrieval": {
            "configuration": best.get("name"),
            "method": (
                "BM25 ranking, plus learned embedding-based near-duplicate "
                "suppression. Embeddings do not rerank search."
            ),
            "recall_at_5": best.get("recall_at_5"),
            "recall_at_10": best.get("recall_at_10"),
            "mrr": best.get("mrr"),
            "queries": best.get("queries"),
            "distinct_title_at_8": best.get("distinct_title_at_8"),
            "unique_title_fraction_at_10": best.get("unique_title_fraction_at_10"),
            "query_set_sha256": (retrieval or {}).get("query_set_sha256"),
            "ranking_finding": (retrieval or {}).get("ranking_finding"),
        },
        "negative_results": _negative_results(retrieval, anomaly),
        "boundary": (
            "Model quality is not authorization accuracy. No score in this block "
            "has ever changed a MandateGuard decision."
        ),
        "source": f"{ARTIFACT_DIR.as_posix()}/{RETRIEVAL_REPORT}",
    }


def _negative_results(
    retrieval: Mapping[str, Any] | None, anomaly: Mapping[str, Any] | None
) -> list[dict[str, str]]:
    """What was built, measured, and then not shipped."""

    findings: list[dict[str, str]] = []
    if retrieval:
        configurations = retrieval.get("configurations", {})
        lexical = configurations.get(SHIPPED_CONFIGURATION, {})
        dense = configurations.get("dense_only_alpha_0.00__deduplicated", {})
        if lexical and dense:
            findings.append(
                {
                    "finding": (
                        "A learned dense ranker was evaluated and did not improve "
                        "retrieval over BM25 on this corpus, so it is not used "
                        "for ranking."
                    ),
                    "detail": (
                        f"Recall@10 {dense.get('recall_at_10')} against "
                        f"{lexical.get('recall_at_10')} for BM25 alone, and every "
                        "intermediate blend fell between them. The shipped alpha "
                        "is 1.0, which means the embedding contributes nothing to "
                        "ranking. The embedding index is still loaded, and earns "
                        "its place in near-duplicate suppression, where it is "
                        "measurably useful."
                    ),
                }
            )
            paraphrase = (
                lexical.get("by_family", {}).get("paraphrase", {}).get("recall_at_10")
            )
            findings.append(
                {
                    "finding": (
                        "Latent semantic analysis does not do paraphrase matching "
                        "here."
                    ),
                    "detail": (
                        "On queries that describe a need without naming the product "
                        f"({paraphrase} Recall@10 for the shipped configuration), no "
                        "blend recovered the intended listings. A contextual encoder "
                        "would likely help and could not be served in a "
                        "dependency-free image."
                    ),
                }
            )
    if anomaly:
        findings.append(
            {
                "finding": "An unsupervised anomaly detector was rejected.",
                "detail": (
                    f"IsolationForest scored ROC AUC "
                    f"{anomaly.get('candidate', {}).get('roc_auc')} against "
                    f"{anomaly.get('baseline', {}).get('roc_auc')} for the "
                    "deterministic analytics on the same held-out rows - fitted "
                    "on an ordinary training control and scored on disjoint "
                    "ordinary controls plus the defective rows - so it is not "
                    "shipped."
                ),
            }
        )
        ablation = anomaly.get("category_laundering_ablation", {})
        if ablation:
            findings.append(
                {
                    "finding": "The supervised classifier did earn its place.",
                    "detail": (
                        "On listings whose category claim was laundered - title and "
                        "description replaced, every structured field left alone, "
                        "the one defect no field comparison can see - including the "
                        "classifier's disagreement moved ROC AUC from "
                        f"{ablation.get('without_ml_mismatch_feature', {}).get('roc_auc')} "
                        f"to {ablation.get('with_ml_mismatch_feature', {}).get('roc_auc')}. "
                        "It raises REVIEW. It cannot ALLOW."
                    ),
                }
            )
    return findings


def load_scale_evidence(*, repository_root: Path) -> dict[str, Any]:
    models_dir = repository_root / "data" / "models"
    return system_scale(repository_root=repository_root, models_dir=models_dir)


def load_model_quality(*, repository_root: Path) -> dict[str, Any]:
    models_dir = repository_root / "data" / "models"
    return model_quality(repository_root=repository_root, models_dir=models_dir)
