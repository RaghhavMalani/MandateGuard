"""Offline training and honest evaluation of the product category classifier.

Baseline first: TF-IDF over title + description, then a linear classifier. The
split is frozen before the test partition is scored, the validation partition
chooses the model, and the test partition is scored exactly once per split.

Two evaluations are produced, and the difference between them is the point.

**Grouped product-family held-out** is the headline. A marketplace crawl lists
one product many times, so a row-wise split puts a listing's near-identical twin
in the training set and the test score partly measures memorization. The grouped
split assigns whole product families (``mandateguard.ml.splits.family_key``) to
one partition, which closes that path. The shipped artifact is the model trained
under this split, so the headline number describes the model that actually runs.

**Row-wise** is the original construction, refit and rescored here so the earlier
claim is not quietly overwritten. It is reported alongside, labelled, with its
leak named.

Both results are advisory. Nothing here can authorize a payment.
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
from mandateguard.ml.splits import (
    FAMILY_KEY_VERSION,
    FrozenSplit,
    family_key,
    freeze_split,
    grouped_split,
    stratified_split,
    verify_frozen_split,
)


MODEL_ID = "discovery-category-linear-v1"
DEFAULT_MAX_FEATURES = 20_000
DEFAULT_MIN_DOCUMENT_FREQUENCY = 3
#: A label the source uses fewer times than this is not a category, it is noise.
MIN_LABEL_SUPPORT = 40
#: Rows the importer could not place in the source taxonomy are excluded from
#: the supervised problem rather than being taught as a real class.
EXCLUDED_LABELS = frozenset({"Uncategorized"})

#: The evaluation whose numbers the product surface is allowed to headline.
HEADLINE_EVALUATION = "grouped_product_family_holdout"

ROW_WISE_CAVEAT = (
    "Row-wise split. The catalog repeats one product across sizes, colours, "
    "sellers, and re-postings, so a listing's near-identical twin can sit in the "
    "training partition. This number is therefore an upper bound that includes "
    "memorization; the grouped product-family result is the one to quote."
)
GROUPED_CAVEAT = (
    "Whole product families are assigned to one partition, so no test listing has "
    "a near-identical twin in training. Advisory classification quality only: it "
    "is not authorization accuracy, and no score here has ever changed a "
    "MandateGuard decision."
)


@dataclass(frozen=True, slots=True)
class SplitEvaluation:
    """One split, fitted and scored end to end."""

    name: str
    split_version: str
    sizes: dict[str, int]
    validation: dict[str, Any]
    test: dict[str, Any]
    per_category: dict[str, Any]
    candidates: dict[str, Any]
    selected_model: str
    test_id_digest: str
    fit_seconds: float
    caveat: str
    group_counts: dict[str, int] | None = None
    family_key_version: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evaluation": self.name,
            "split_version": self.split_version,
            "selected_model": self.selected_model,
            "sizes": dict(self.sizes),
            "validation": dict(self.validation),
            "test": dict(self.test),
            "per_category": dict(self.per_category),
            "candidates": dict(self.candidates),
            "test_id_digest": self.test_id_digest,
            "fit_seconds": round(self.fit_seconds, 3),
            "caveat": self.caveat,
        }
        if self.group_counts is not None:
            payload["group_counts"] = dict(self.group_counts)
        if self.family_key_version is not None:
            payload["family_key_version"] = self.family_key_version
        return payload


@dataclass(frozen=True, slots=True)
class ClassifierReport:
    model_id: str
    classes: tuple[str, ...]
    labelled_rows: int
    dropped_rows: int
    grouped: SplitEvaluation
    row_wise: SplitEvaluation
    confusion_path: str
    artifact_bytes: int
    artifact_sha256: str

    def to_mapping(self) -> dict[str, Any]:
        # The top-level `test`, `validation`, `sizes`, `per_category` and
        # `selected_model` keys mirror the headline evaluation, so a reader who
        # does not know both splits exist still gets the conservative number.
        return {
            "model_id": self.model_id,
            "headline_evaluation": HEADLINE_EVALUATION,
            "selected_model": self.grouped.selected_model,
            "classes": list(self.classes),
            "class_count": len(self.classes),
            "sizes": {
                **self.grouped.sizes,
                "labelled_rows": self.labelled_rows,
                "dropped_rows": self.dropped_rows,
            },
            "validation": dict(self.grouped.validation),
            "test": dict(self.grouped.test),
            "per_category": dict(self.grouped.per_category),
            "grouped_family": self.grouped.to_mapping(),
            "row_wise": self.row_wise.to_mapping(),
            "confusion_matrix_artifact": self.confusion_path,
            "artifact_bytes": self.artifact_bytes,
            "artifact_sha256": self.artifact_sha256,
            "fit_seconds": round(self.grouped.fit_seconds, 3),
            "advisory_only": True,
            "authorization_authority": "NONE",
        }


def classifier_text(title: str, description: str) -> str:
    """The exact feature text, used identically at train and inference time."""

    return f"{title}\n{description}".strip()


def labelled_rows(
    catalog: DiscoveryCatalog,
) -> tuple[list[str], list[str], list[str], list[str], dict[str, int]]:
    """Return ``(texts, labels, ids, families, dropped_counts)``."""

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
    families: list[str] = []
    dropped: dict[str, int] = {}
    for product in catalog:
        label = product.top_category
        if label not in usable:
            dropped[label] = dropped.get(label, 0) + 1
            continue
        texts.append(classifier_text(product.title, product.description))
        labels.append(label)
        ids.append(product.catalog_product_id)
        families.append(family_key(title=product.title, brand=product.brand))
    return texts, labels, ids, families, dropped


def _metrics(
    true: Sequence[str], predicted: Sequence[str], classes: Sequence[str]
) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, f1_score

    return {
        "accuracy": round(float(accuracy_score(true, predicted)), 6),
        "macro_f1": round(
            float(
                f1_score(
                    true, predicted, average="macro", labels=list(classes), zero_division=0
                )
            ),
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


def _fit_and_score(
    *,
    name: str,
    split: FrozenSplit,
    texts: Sequence[str],
    labels: Sequence[str],
    classes: Sequence[str],
    split_manifest_path: Path,
    catalog_sha256: str,
    max_features: int,
    min_document_frequency: int,
    random_state: int,
    caveat: str,
) -> tuple[SplitEvaluation, Any, Any, list[str], Any, list[str]]:
    """Freeze the split, select on validation, then score the test partition once.

    Returns the evaluation plus the fitted vectorizer, model, model classes, the
    test predictions, and the test labels, so the caller can serialize whichever
    model it decided to ship.
    """

    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report
    from sklearn.svm import LinearSVC

    # Frozen before any test-partition score exists.
    freeze_split(split, split_manifest_path, catalog_sha256=catalog_sha256)

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
    for candidate_name, model in candidates.items():
        model.fit(train_matrix, train_label)
        predicted = model.predict(validation_matrix)
        result = _metrics(validation_label, predicted, classes)
        scored[candidate_name] = result
        if result["macro_f1"] > best_score:
            best_score = result["macro_f1"]
            best_name = candidate_name
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

    evaluation = SplitEvaluation(
        name=name,
        split_version=split.split_version,
        sizes=split.sizes(),
        validation=scored[best_name],
        test=test_metrics,
        per_category=per_category,
        candidates=scored,
        selected_model=best_name,
        test_id_digest=split.test_digest,
        fit_seconds=fit_seconds,
        caveat=caveat,
        group_counts=dict(split.group_counts) if split.group_counts else None,
        family_key_version=split.family_key_version,
    )
    return evaluation, vectorizer, best_model, model_classes, test_predicted, test_label


def train_classifier(
    catalog: DiscoveryCatalog,
    *,
    model_path: Path,
    split_manifest_path: Path,
    grouped_split_manifest_path: Path,
    confusion_path: Path,
    repository_root: Path | None = None,
    max_features: int = DEFAULT_MAX_FEATURES,
    min_document_frequency: int = DEFAULT_MIN_DOCUMENT_FREQUENCY,
    random_state: int = 20260903,
) -> ClassifierReport:
    import numpy as np
    from sklearn.metrics import confusion_matrix

    texts, labels, ids, families, dropped = labelled_rows(catalog)
    if len(texts) < 100:
        raise RuntimeError("not enough labelled listings to train a classifier")
    classes = tuple(sorted(set(labels)))

    # The shipped model is the grouped one, so it is fitted first and its test
    # partition is the one the confusion matrix describes.
    grouped, vectorizer, best_model, model_classes, test_predicted, test_label = (
        _fit_and_score(
            name=HEADLINE_EVALUATION,
            split=grouped_split(labels, ids, families),
            texts=texts,
            labels=labels,
            classes=classes,
            split_manifest_path=grouped_split_manifest_path,
            catalog_sha256=catalog.catalog_sha256,
            max_features=max_features,
            min_document_frequency=min_document_frequency,
            random_state=random_state,
            caveat=GROUPED_CAVEAT,
        )
    )

    # Refit under the original row-wise split so the earlier claim stays visible
    # and comparable rather than being silently replaced.
    row_wise, _, _, _, _, _ = _fit_and_score(
        name="row_wise_holdout",
        split=stratified_split(labels, ids),
        texts=texts,
        labels=labels,
        classes=classes,
        split_manifest_path=split_manifest_path,
        catalog_sha256=catalog.catalog_sha256,
        max_features=max_features,
        min_document_frequency=min_document_frequency,
        random_state=random_state,
        caveat=ROW_WISE_CAVEAT,
    )

    matrix = confusion_matrix(test_label, test_predicted, labels=list(classes))
    confusion_path.parent.mkdir(parents=True, exist_ok=True)
    confusion_path.write_text(
        json.dumps(
            {
                "model_id": MODEL_ID,
                "evaluation": HEADLINE_EVALUATION,
                "selected_model": grouped.selected_model,
                "labels": list(classes),
                "rows_are_true_labels": True,
                "matrix": matrix.tolist(),
                "catalog_sha256": catalog.catalog_sha256,
                "test_id_digest": grouped.test_id_digest,
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
        document_count=len(catalog),
        metrics={
            "headline_evaluation": HEADLINE_EVALUATION,
            "validation": grouped.validation,
            "test": grouped.test,
            "selected_model": grouped.selected_model,
            "row_wise_test": row_wise.test,
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
            "split": grouped.split_version,
            "family_key_version": FAMILY_KEY_VERSION,
        },
    )
    return ClassifierReport(
        model_id=MODEL_ID,
        classes=classes,
        labelled_rows=len(texts),
        dropped_rows=sum(dropped.values()),
        grouped=grouped,
        row_wise=row_wise,
        # Repository-relative: this string is served to the public model-quality
        # surface, and a build machine's directory layout is not public data.
        confusion_path=_relative_path(confusion_path, repository_root),
        artifact_bytes=artifact_bytes,
        artifact_sha256=artifact_sha,
    )


def _relative_path(path: Path, repository_root: Path | None) -> str:
    """Repository-relative POSIX path, or the bare filename if it is outside."""

    resolved = Path(path).resolve()
    if repository_root is not None:
        try:
            return resolved.relative_to(Path(repository_root).resolve()).as_posix()
        except ValueError:
            pass
    return resolved.name
