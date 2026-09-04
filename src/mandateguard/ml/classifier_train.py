"""Offline training and honest evaluation of the product category classifier.

Baseline first: TF-IDF over title + description, then a linear classifier. The
split is frozen before the test partition is scored, the validation partition
chooses the model, and the test partition is scored exactly once per run.

The result is advisory. Nothing here can authorize a payment.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.classifier import write_classifier
from mandateguard.discovery.index.analyzer import ANALYZER_VERSION, analyze
from mandateguard.ml.splits import FrozenSplit, freeze_split, stratified_split, verify_frozen_split


MODEL_ID = "discovery-category-linear-v1"
DEFAULT_MAX_FEATURES = 20_000
DEFAULT_MIN_DOCUMENT_FREQUENCY = 3
#: A label the source uses fewer times than this is not a category, it is noise.
MIN_LABEL_SUPPORT = 40
#: Rows the importer could not place in the source taxonomy are excluded from
#: the supervised problem rather than being taught as a real class.
EXCLUDED_LABELS = frozenset({"Uncategorized"})


@dataclass(frozen=True, slots=True)
class ClassifierReport:
    model_id: str
    classes: tuple[str, ...]
    sizes: dict[str, int]
    validation: dict[str, Any]
    test: dict[str, Any]
    per_category: dict[str, Any]
    confusion_path: str
    artifact_bytes: int
    artifact_sha256: str
    fit_seconds: float
    selected_model: str
    candidates: dict[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "selected_model": self.selected_model,
            "classes": list(self.classes),
            "class_count": len(self.classes),
            "sizes": dict(self.sizes),
            "validation": dict(self.validation),
            "test": dict(self.test),
            "per_category": dict(self.per_category),
            "confusion_matrix_artifact": self.confusion_path,
            "artifact_bytes": self.artifact_bytes,
            "artifact_sha256": self.artifact_sha256,
            "fit_seconds": round(self.fit_seconds, 3),
            "advisory_only": True,
            "authorization_authority": "NONE",
        }


def classifier_text(title: str, description: str) -> str:
    """The exact feature text, used identically at train and inference time."""

    return f"{title}\n{description}".strip()


def labelled_rows(
    catalog: DiscoveryCatalog,
) -> tuple[list[str], list[str], list[str], dict[str, int]]:
    """Return ``(texts, labels, ids, dropped_counts)`` for the supervised task."""

    support: dict[str, int] = {}
    for product in catalog:
        support[product.top_category] = support.get(product.top_category, 0) + 1
    usable = {
        label
        for label, count in support.items()
        if count >= MIN_LABEL_SUPPORT and label not in EXCLUDED_LABELS
    }
    texts: list[str] = []
    labels: list[str] = []
    ids: list[str] = []
    dropped: dict[str, int] = {}
    for product in catalog:
        label = product.top_category
        if label not in usable:
            dropped[label] = dropped.get(label, 0) + 1
            continue
        texts.append(classifier_text(product.title, product.description))
        labels.append(label)
        ids.append(product.catalog_product_id)
    return texts, labels, ids, dropped


def _metrics(true: Sequence[str], predicted: Sequence[str], classes: Sequence[str]) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, f1_score

    return {
        "accuracy": round(float(accuracy_score(true, predicted)), 6),
        "macro_f1": round(
            float(f1_score(true, predicted, average="macro", labels=list(classes), zero_division=0)),
            6,
        ),
        "weighted_f1": round(
            float(
                f1_score(
                    true, predicted, average="weighted", labels=list(classes), zero_division=0
                )
            ),
            6,
        ),
        "samples": len(true),
    }


def train_classifier(
    catalog: DiscoveryCatalog,
    *,
    model_path: Path,
    split_manifest_path: Path,
    confusion_path: Path,
    max_features: int = DEFAULT_MAX_FEATURES,
    min_document_frequency: int = DEFAULT_MIN_DOCUMENT_FREQUENCY,
    random_state: int = 20260903,
) -> ClassifierReport:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report, confusion_matrix
    from sklearn.svm import LinearSVC

    texts, labels, ids, dropped = labelled_rows(catalog)
    if len(texts) < 100:
        raise RuntimeError("not enough labelled listings to train a classifier")
    split: FrozenSplit = stratified_split(labels, ids)
    # Frozen before any test-partition score exists.
    freeze_split(split, split_manifest_path, catalog_sha256=catalog.catalog_sha256)

    classes = tuple(sorted(set(labels)))
    train_text = [texts[position] for position in split.train]
    train_label = [labels[position] for position in split.train]
    validation_text = [texts[position] for position in split.validation]
    validation_label = [labels[position] for position in split.validation]
    test_text = [texts[position] for position in split.test]
    test_label = [labels[position] for position in split.test]

    started = perf_counter()
    vectorizer = TfidfVectorizer(
        analyzer=analyze,
        sublinear_tf=True,
        min_df=min_document_frequency,
        max_features=max_features,
        norm="l2",
    )
    train_matrix = vectorizer.fit_transform(train_text)
    validation_matrix = vectorizer.transform(validation_text)

    candidates = {
        "linear_svc": LinearSVC(C=1.0, random_state=random_state),
        "logistic_regression": LogisticRegression(
            C=4.0, max_iter=2000, random_state=random_state, n_jobs=None
        ),
    }
    scored: dict[str, Any] = {}
    best_name = ""
    best_score = -1.0
    best_model = None
    for name, model in candidates.items():
        model.fit(train_matrix, train_label)
        predicted = model.predict(validation_matrix)
        result = _metrics(validation_label, predicted, classes)
        scored[name] = result
        if result["macro_f1"] > best_score:
            best_score = result["macro_f1"]
            best_name = name
            best_model = model
    fit_seconds = perf_counter() - started
    assert best_model is not None

    # The test partition is touched only now, after model selection is settled.
    verify_frozen_split(split, split_manifest_path)
    test_matrix = vectorizer.transform(test_text)
    test_predicted = best_model.predict(test_matrix)
    test_metrics = _metrics(test_label, test_predicted, classes)

    decision = best_model.decision_function(test_matrix)
    if decision.ndim == 1:
        decision = np.column_stack([-decision, decision])
    order = np.argsort(-decision, axis=1)
    model_classes = list(best_model.classes_)
    for k in (2, 3):
        hits = 0
        for row, actual in zip(order, test_label, strict=True):
            if actual in [model_classes[index] for index in row[:k]]:
                hits += 1
        test_metrics[f"top_{k}_accuracy"] = round(hits / len(test_label), 6)

    report = classification_report(
        test_label,
        test_predicted,
        labels=list(classes),
        output_dict=True,
        zero_division=0,
    )
    per_category = {
        label: {
            "precision": round(float(values["precision"]), 6),
            "recall": round(float(values["recall"]), 6),
            "f1": round(float(values["f1-score"]), 6),
            "test_support": int(values["support"]),
            "train_support": split.label_support.get(label, {}).get("train", 0),
        }
        for label, values in report.items()
        if label in set(classes)
    }
    matrix = confusion_matrix(test_label, test_predicted, labels=list(classes))
    confusion_path.parent.mkdir(parents=True, exist_ok=True)
    confusion_path.write_text(
        json.dumps(
            {
                "model_id": MODEL_ID,
                "selected_model": best_name,
                "labels": list(classes),
                "rows_are_true_labels": True,
                "matrix": matrix.tolist(),
                "catalog_sha256": catalog.catalog_sha256,
                "test_id_digest": split.test_digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    vocabulary = vectorizer.vocabulary_
    terms = sorted(vocabulary)
    positions = [vocabulary[term] for term in terms]
    idf = [float(vectorizer.idf_[position]) for position in positions]
    coefficients_raw = np.atleast_2d(best_model.coef_)
    if coefficients_raw.shape[0] == 1 and len(model_classes) == 2:
        coefficients_raw = np.vstack([-coefficients_raw[0], coefficients_raw[0]])
    intercept_raw = np.atleast_1d(best_model.intercept_)
    if intercept_raw.shape[0] == 1 and len(model_classes) == 2:
        intercept_raw = np.array([-intercept_raw[0], intercept_raw[0]])
    by_class = {label: index for index, label in enumerate(model_classes)}
    coefficients = [
        coefficients_raw[by_class[label]][positions].tolist() for label in classes
    ]
    intercepts = [float(intercept_raw[by_class[label]]) for label in classes]

    artifact_bytes, artifact_sha = write_classifier(
        model_path,
        model_id=MODEL_ID,
        classes=classes,
        terms=terms,
        idf=idf,
        coefficients=coefficients,
        intercepts=intercepts,
        catalog_sha256=catalog.catalog_sha256,
        metrics={
            "validation": scored[best_name],
            "test": test_metrics,
            "selected_model": best_name,
        },
        trainer={
            "analyzer_version": ANALYZER_VERSION,
            "features": "title + description, TF-IDF, sublinear tf, L2",
            "max_features": max_features,
            "min_document_frequency": min_document_frequency,
            "random_state": random_state,
            "excluded_labels": sorted(EXCLUDED_LABELS),
            "min_label_support": MIN_LABEL_SUPPORT,
            "dropped_rows_by_label": dict(sorted(dropped.items())),
        },
    )
    return ClassifierReport(
        model_id=MODEL_ID,
        classes=classes,
        sizes={**split.sizes(), "labelled_rows": len(texts), "dropped_rows": sum(dropped.values())},
        validation=scored[best_name],
        test=test_metrics,
        per_category=per_category,
        confusion_path=str(confusion_path),
        artifact_bytes=artifact_bytes,
        artifact_sha256=artifact_sha,
        fit_seconds=fit_seconds,
        selected_model=best_name,
        candidates=scored,
    )
