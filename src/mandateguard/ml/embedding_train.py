"""Offline training of the frozen LSA embedding index.

Runs in the development environment only: it needs scikit-learn and NumPy, which
never enter the public runtime image. The output is a single artifact the
standard-library retriever can read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.index.analyzer import ANALYZER_VERSION, analyze
from mandateguard.discovery.index.embedding import write_embedding_index


DEFAULT_DIMENSIONS = 192
DEFAULT_MAX_FEATURES = 30_000
DEFAULT_MIN_DOCUMENT_FREQUENCY = 3
TRAINER_ID = "tfidf-truncated-svd-lsa-v1"


@dataclass(frozen=True, slots=True)
class EmbeddingTrainingReport:
    dimensions: int
    vocabulary_size: int
    document_count: int
    explained_variance: float
    fit_seconds: float
    artifact_bytes: int
    artifact_sha256: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "trainer": TRAINER_ID,
            "dimensions": self.dimensions,
            "vocabulary_size": self.vocabulary_size,
            "document_count": self.document_count,
            "explained_variance": round(self.explained_variance, 6),
            "fit_seconds": round(self.fit_seconds, 3),
            "artifact_bytes": self.artifact_bytes,
            "artifact_sha256": self.artifact_sha256,
        }


def train_embedding_index(
    catalog: DiscoveryCatalog,
    path: Path,
    *,
    dimensions: int = DEFAULT_DIMENSIONS,
    max_features: int = DEFAULT_MAX_FEATURES,
    min_document_frequency: int = DEFAULT_MIN_DOCUMENT_FREQUENCY,
    random_state: int = 20260903,
) -> EmbeddingTrainingReport:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts: Sequence[str] = [product.indexed_text() for product in catalog]
    started = perf_counter()
    vectorizer = TfidfVectorizer(
        analyzer=analyze,
        sublinear_tf=True,
        min_df=min_document_frequency,
        max_features=max_features,
        norm="l2",
    )
    matrix = vectorizer.fit_transform(texts)
    components = min(dimensions, min(matrix.shape) - 1)
    svd = TruncatedSVD(n_components=components, random_state=random_state)
    reduced = svd.fit_transform(matrix)
    fit_seconds = perf_counter() - started

    vocabulary = vectorizer.vocabulary_
    terms = sorted(vocabulary)
    idf = vectorizer.idf_
    # Row t of the projection is idf[t] * V[:, t], so the runtime encoder only
    # has to weight it by the query's sublinear term frequency.
    loadings = svd.components_.T
    projection_rows = [
        (loadings[vocabulary[term]] * float(idf[vocabulary[term]])).tolist()
        for term in terms
    ]
    artifact_bytes, artifact_sha = write_embedding_index(
        path,
        dimensions=components,
        terms=terms,
        projection_rows=projection_rows,
        document_vectors=reduced.tolist(),
        catalog_sha256=catalog.catalog_sha256,
        explained_variance=float(svd.explained_variance_ratio_.sum()),
        trainer={
            "id": TRAINER_ID,
            "analyzer_version": ANALYZER_VERSION,
            "max_features": max_features,
            "min_document_frequency": min_document_frequency,
            "sublinear_tf": True,
            "random_state": random_state,
            "note": (
                "Document vectors are precomputed. Query encoding at runtime is "
                "a sparse-by-dense product over the query's own terms, which is "
                "why no transformer runtime is required."
            ),
        },
    )
    return EmbeddingTrainingReport(
        dimensions=components,
        vocabulary_size=len(terms),
        document_count=reduced.shape[0],
        explained_variance=float(svd.explained_variance_ratio_.sum()),
        fit_seconds=fit_seconds,
        artifact_bytes=artifact_bytes,
        artifact_sha256=artifact_sha,
    )
