"""Frozen linear product-category classifier: standard-library inference.

The classifier is **advisory**. It predicts what a listing's category probably
is, which is useful for two things: routing a search, and noticing that a
listing's declared category disagrees with its own text. Neither of those is an
authorization decision, and the model has no path to one - see
``mandateguard.discovery.trust``.

Inference mirrors the trainer exactly: sublinear term frequency, the frozen IDF
vector, L2 normalization, then one dense dot product per class.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from mandateguard.discovery.index.analyzer import ANALYZER_VERSION, analyze
from mandateguard.discovery.index.artifacts import (
    ArtifactError,
    pack_string_table,
    read_artifact,
    unpack_string_table,
    validate_catalog_binding,
    write_artifact,
)
from mandateguard.discovery.trust import AdvisorySignal


MODEL_KIND = "linear-category-classifier-v1"
QUANTIZATION_SCALE = 127.0


def _signed(value: int) -> int:
    return value - 256 if value > 127 else value


@dataclass(frozen=True, slots=True)
class CategoryPrediction:
    """One advisory classification. Carries its own authority statement."""

    label: str
    margin: float
    ranked: tuple[tuple[str, float], ...]
    matched_terms: int
    model_id: str

    @property
    def confidence_band(self) -> str:
        """Coarse, honest banding. A margin is not a calibrated probability."""

        if self.matched_terms == 0:
            return "NO_SIGNAL"
        if self.margin >= 0.35:
            return "HIGH"
        if self.margin >= 0.15:
            return "MEDIUM"
        return "LOW"

    def as_signal(self) -> AdvisorySignal:
        return AdvisorySignal(
            signal_id="product_category_prediction",
            value=self.label,
            produced_by=self.model_id,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "predicted_category": self.label,
            "margin": round(self.margin, 6),
            "confidence_band": self.confidence_band,
            "ranked": [
                {"category": label, "score": round(score, 6)}
                for label, score in self.ranked
            ],
            "matched_terms": self.matched_terms,
            "model_id": self.model_id,
            "authorization_authority": "NONE",
        }


@dataclass(frozen=True, slots=True)
class CategoryClassifier:
    """A frozen one-vs-rest linear model over the shared analyzer's vocabulary."""

    model_id: str
    classes: tuple[str, ...]
    terms: tuple[str, ...]
    term_positions: Mapping[str, int]
    idf: array
    coefficients: bytes
    coefficient_scales: array
    intercepts: array
    dimensions: int
    document_count: int
    catalog_sha256: str
    artifact_bytes: int
    metrics: Mapping[str, Any]

    def decision_scores(self, text: str) -> tuple[list[float], int]:
        """Raw per-class decision values plus the number of matched terms."""

        counts: dict[str, int] = {}
        for token in analyze(text):
            counts[token] = counts.get(token, 0) + 1
        weights: list[tuple[int, float]] = []
        square = 0.0
        for token, count in counts.items():
            position = self.term_positions.get(token)
            if position is None:
                continue
            weight = (1.0 + math.log(count)) * self.idf[position]
            weights.append((position, weight))
            square += weight * weight
        if not weights:
            return [float(value) for value in self.intercepts], 0
        norm = math.sqrt(square) or 1.0
        scores = [float(value) for value in self.intercepts]
        for class_index in range(len(self.classes)):
            scale = self.coefficient_scales[class_index]
            if scale == 0.0:
                continue
            base = class_index * self.dimensions
            row = self.coefficients
            total = 0.0
            for position, weight in weights:
                total += _signed(row[base + position]) * weight
            scores[class_index] += total * scale / norm
        return scores, len(weights)

    def predict(self, text: str, *, top_n: int = 3) -> CategoryPrediction:
        scores, matched = self.decision_scores(text)
        ranked = sorted(
            zip(self.classes, scores, strict=True),
            key=lambda item: (-item[1], item[0]),
        )
        best = ranked[0][1]
        runner_up = ranked[1][1] if len(ranked) > 1 else best
        span = max(abs(best), 1e-9)
        return CategoryPrediction(
            label=ranked[0][0],
            margin=max(0.0, (best - runner_up) / span),
            ranked=tuple(ranked[: max(1, top_n)]),
            matched_terms=matched,
            model_id=self.model_id,
        )

    def top_k_labels(self, text: str, k: int) -> tuple[str, ...]:
        scores, _ = self.decision_scores(text)
        ranked = sorted(
            zip(self.classes, scores, strict=True),
            key=lambda item: (-item[1], item[0]),
        )
        return tuple(label for label, _ in ranked[:k])


def quantize_rows(
    rows: Sequence[Sequence[float]],
) -> tuple[bytes, array]:
    packed = bytearray()
    scales = array("f")
    for row in rows:
        peak = max((abs(float(value)) for value in row), default=0.0)
        if peak == 0.0:
            packed += bytes(len(row))
            scales.append(0.0)
            continue
        scale = peak / QUANTIZATION_SCALE
        for value in row:
            quantized = max(-127, min(127, int(round(float(value) / scale))))
            packed.append(quantized & 0xFF)
        scales.append(scale)
    return bytes(packed), scales


def write_classifier(
    path: Path,
    *,
    model_id: str,
    classes: Sequence[str],
    terms: Sequence[str],
    idf: Sequence[float],
    coefficients: Sequence[Sequence[float]],
    intercepts: Sequence[float],
    catalog_sha256: str,
    document_count: int,
    metrics: Mapping[str, Any],
    trainer: Mapping[str, Any],
) -> tuple[int, str]:
    if len(coefficients) != len(classes):
        raise ValueError("one coefficient row per class is required")
    if any(len(row) != len(terms) for row in coefficients):
        raise ValueError("coefficient rows must match the vocabulary length")
    packed, scales = quantize_rows(coefficients)
    joined, offsets = pack_string_table(list(terms))
    class_joined, class_offsets = pack_string_table(list(classes))
    header = {
        "kind": MODEL_KIND,
        "model_id": model_id,
        "analyzer_version": ANALYZER_VERSION,
        "catalog_sha256": catalog_sha256,
        "document_count": document_count,
        "class_count": len(classes),
        "vocabulary_size": len(terms),
        "quantization": "int8-symmetric-per-class",
        "metrics": dict(metrics),
        "trainer": dict(trainer),
        "authorization_authority": "NONE",
    }
    sections = {
        "terms": joined,
        "term_offsets": offsets,
        "classes": class_joined,
        "class_offsets": class_offsets,
        "idf": array("f", [float(value) for value in idf]).tobytes(),
        "coefficients": packed,
        "coefficient_scales": scales.tobytes(),
        "intercepts": array("f", [float(value) for value in intercepts]).tobytes(),
    }
    return write_artifact(path, header, sections)


def load_classifier(
    path: Path,
    *,
    expected_catalog_sha256: str | None = None,
    expected_document_count: int | None = None,
) -> CategoryClassifier:
    artifact = read_artifact(path)
    expected_sections = {
        "terms",
        "term_offsets",
        "classes",
        "class_offsets",
        "idf",
        "coefficients",
        "coefficient_scales",
        "intercepts",
    }
    if set(artifact.sections) != expected_sections:
        raise ArtifactError("classifier sections do not match its schema")
    if artifact.require("kind") != MODEL_KIND:
        raise ArtifactError(f"expected {MODEL_KIND}, found {artifact.header.get('kind')!r}")
    if artifact.require("analyzer_version") != ANALYZER_VERSION:
        raise ArtifactError("classifier was built by a different analyzer version")
    catalog_sha256, document_count = validate_catalog_binding(
        artifact,
        expected_catalog_sha256=expected_catalog_sha256,
        expected_document_count=expected_document_count,
    )
    terms = tuple(
        unpack_string_table(artifact.section("terms"), artifact.section("term_offsets"))
    )
    classes = tuple(
        unpack_string_table(
            artifact.section("classes"), artifact.section("class_offsets")
        )
    )
    idf = array("f")
    raw_idf = artifact.section("idf")
    raw_scales = artifact.section("coefficient_scales")
    raw_intercepts = artifact.section("intercepts")
    if len(raw_idf) % 4 or len(raw_scales) % 4 or len(raw_intercepts) % 4:
        raise ArtifactError("classifier floating-point table is truncated")
    idf.frombytes(raw_idf)
    scales = array("f")
    scales.frombytes(raw_scales)
    intercepts = array("f")
    intercepts.frombytes(raw_intercepts)
    coefficients = artifact.section("coefficients")
    if len(idf) != len(terms):
        raise ArtifactError("classifier idf table does not match its vocabulary")
    if len(coefficients) != len(classes) * len(terms):
        raise ArtifactError("classifier coefficient table has an unexpected size")
    if len(scales) != len(classes) or len(intercepts) != len(classes):
        raise ArtifactError("classifier per-class tables disagree in length")
    if int(artifact.require("class_count")) != len(classes):
        raise ArtifactError("classifier class count does not match its table")
    if int(artifact.require("vocabulary_size")) != len(terms):
        raise ArtifactError("classifier vocabulary count does not match its table")
    metrics = artifact.header.get("metrics")
    return CategoryClassifier(
        model_id=str(artifact.require("model_id")),
        classes=classes,
        terms=terms,
        term_positions={term: position for position, term in enumerate(terms)},
        idf=idf,
        coefficients=coefficients,
        coefficient_scales=scales,
        intercepts=intercepts,
        dimensions=len(terms),
        document_count=document_count,
        catalog_sha256=catalog_sha256,
        artifact_bytes=Path(path).stat().st_size,
        metrics=dict(metrics) if isinstance(metrics, Mapping) else {},
    )
