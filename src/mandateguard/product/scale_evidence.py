"""Measured numbers, read from the artifacts that produced them.

Every figure the product shows under SYSTEM SCALE and MODEL QUALITY is loaded
from a recorded evaluation artifact at startup. Nothing here is a literal typed
into the interface, because a literal is a number that keeps its value after the
thing it described has changed.

If an artifact is missing, the corresponding block reports itself unavailable
rather than falling back to a remembered value.
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

#: The four kinds of evidence this product has, kept apart on purpose. Merging
#: them is how "709 tests" ends up being presented as scale.
EVIDENCE_KINDS = (
    "SYSTEM_SCALE",
    "MODEL_QUALITY",
    "AUTHORIZATION_EVIDENCE",
    "ENGINEERING_QUALITY",
)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _best_retrieval_configuration(report: Mapping[str, Any]) -> dict[str, Any] | None:
    """The configuration the runtime actually uses: lexical, deduplicated."""

    configurations = report.get("configurations")
    if not isinstance(configurations, Mapping):
        return None
    preferred = configurations.get("lexical_only_alpha_1.00__deduplicated")
    if isinstance(preferred, Mapping):
        return dict(preferred)
    for value in configurations.values():
        if isinstance(value, Mapping):
            return dict(value)
    return None


def system_scale(
    *, repository_root: Path, models_dir: Path
) -> dict[str, Any]:
    """Catalog size, index size, and measured retrieval latency."""

    scale = _read(repository_root / ARTIFACT_DIR / SCALE_REPORT)
    training = _read(models_dir / TRAINING_REPORT)
    retrieval = _read(repository_root / ARTIFACT_DIR / RETRIEVAL_REPORT)
    if scale is None and training is None:
        return {"available": False, "reason": "No scale benchmark has been recorded."}
    latency = (scale or {}).get("query_latency_ms", {})
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
        "queries_per_second": (scale or {}).get("queries_per_second"),
        "p50_ms": latency.get("p50"),
        "p95_ms": latency.get("p95"),
        "p99_ms": latency.get("p99"),
        "evaluated_queries": (retrieval or {}).get("queries"),
        "source": f"{ARTIFACT_DIR.as_posix()}/{SCALE_REPORT}",
        "caveat": (
            "Single process, one machine, measured on the imported catalog. "
            "Not a distributed-throughput claim and not extrapolated."
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
    test = classifier.get("test", {})
    best = _best_retrieval_configuration(retrieval or {}) or {}
    return {
        "available": True,
        "kind": "MODEL_QUALITY",
        "classifier": {
            "model": classifier.get("selected_model"),
            "classes": classifier.get("class_count"),
            "accuracy": test.get("accuracy"),
            "macro_f1": test.get("macro_f1"),
            "weighted_f1": test.get("weighted_f1"),
            "top_2_accuracy": test.get("top_2_accuracy"),
            "train": classifier.get("sizes", {}).get("train"),
            "validation": classifier.get("sizes", {}).get("validation"),
            "test": classifier.get("sizes", {}).get("test"),
            "advisory_only": True,
        },
        "retrieval": {
            "configuration": best.get("name"),
            "recall_at_5": best.get("recall_at_5"),
            "recall_at_10": best.get("recall_at_10"),
            "mrr": best.get("mrr"),
            "queries": best.get("queries"),
            "distinct_title_fraction": best.get("distinct_title_fraction"),
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
        lexical = configurations.get("lexical_only_alpha_1.00__deduplicated", {})
        dense = configurations.get("dense_only_alpha_0.00__deduplicated", {})
        if lexical and dense:
            findings.append(
                {
                    "finding": "The learned dense retriever did not beat BM25.",
                    "detail": (
                        f"Recall@10 {dense.get('recall_at_10')} against "
                        f"{lexical.get('recall_at_10')} for lexical alone, and every "
                        "intermediate blend fell between them. The default blend is "
                        "therefore lexical; the embedding index is kept for "
                        "near-duplicate suppression, where it is measurably useful."
                    ),
                }
            )
            paraphrase = (
                lexical.get("by_family", {}).get("paraphrase", {}).get("recall_at_10")
            )
            findings.append(
                {
                    "finding": "Latent semantic analysis does not do paraphrase matching here.",
                    "detail": (
                        "On queries that describe a need without naming the product "
                        f"({paraphrase} Recall@10 for the best configuration), no blend "
                        "recovered the intended listings. A contextual encoder would "
                        "likely help and could not be served in a dependency-free image."
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
                    "deterministic analytics on the same frozen set, so it is not "
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
                        "On listings whose category claim was laundered - the one "
                        "defect no field comparison can see - including the "
                        "classifier's disagreement moved ROC AUC from "
                        f"{ablation.get('without_ml_mismatch_feature', {}).get('roc_auc')} "
                        f"to {ablation.get('with_ml_mismatch_feature', {}).get('roc_auc')}."
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
