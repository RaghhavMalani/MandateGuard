"""Frozen dense embedding index with standard-library query-time inference.

Why not a runtime sentence-transformer
--------------------------------------
Arbitrary buyer intent means the *query* has to be encoded at request time, so a
transformer encoder could not be pushed offline the way document vectors can:
serving one would put PyTorch inside the public demo image. On the free
deployment tier that is a multi-gigabyte image and a cold start measured in tens
of seconds, for a page whose entire promise is "zero external calls, starts
instantly".

So the embedding model is trained offline (TF-IDF -> truncated SVD, i.e. latent
semantic analysis) and frozen into two tables:

* ``document_vectors`` - one L2-normalized int8 vector per listing;
* ``projection`` - for every vocabulary term, the int8 row ``idf[t] * V[t]``.

Encoding a query is then a sparse-by-dense product over only the query's own
terms: a few thousand multiply-adds, which pure Python does in well under a
millisecond. This is a real learned embedding fitted on the catalog, not a
hashing trick - and it is a linear model, not a contextual encoder. The
retrieval evaluation reports what that buys over lexical alone rather than
asserting it.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Mapping, Sequence

from mandateguard.discovery.index.analyzer import ANALYZER_VERSION, analyze
from mandateguard.discovery.index.artifacts import (
    ArtifactError,
    pack_string_table,
    read_artifact,
    unpack_string_table,
    validate_catalog_binding,
    write_artifact,
)


INDEX_KIND = "lsa-embedding-v1"
QUANTIZATION_SCALE = 127.0


def quantize(vector: Sequence[float]) -> tuple[bytes, float]:
    """Symmetric int8 quantization; returns ``(bytes, scale)``."""

    peak = max((abs(float(value)) for value in vector), default=0.0)
    if peak == 0.0:
        return bytes(len(vector)), 0.0
    scale = peak / QUANTIZATION_SCALE
    packed = bytearray(len(vector))
    for index, value in enumerate(vector):
        quantized = int(round(float(value) / scale))
        quantized = max(-127, min(127, quantized))
        packed[index] = quantized & 0xFF
    return bytes(packed), scale


def _signed(value: int) -> int:
    return value - 256 if value > 127 else value


@dataclass(frozen=True, slots=True)
class EmbeddingIndex:
    """Frozen LSA index: projection rows plus quantized document vectors."""

    dimensions: int
    terms: tuple[str, ...]
    term_positions: Mapping[str, int]
    projection: bytes
    projection_scales: array
    document_vectors: bytes
    document_count: int
    catalog_sha256: str
    index_bytes: int
    explained_variance: float

    def encode(self, text: str) -> list[float] | None:
        """Encode arbitrary query text into the frozen latent space."""

        return self.encode_terms(analyze(text))

    def encode_terms(self, tokens: Sequence[str]) -> list[float] | None:
        if not tokens:
            return None
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        vector = [0.0] * self.dimensions
        matched = False
        for token, count in counts.items():
            position = self.term_positions.get(token)
            if position is None:
                continue
            matched = True
            # Sublinear term frequency, matching the trainer's TF-IDF setting.
            weight = 1.0 + math.log(count)
            scale = self.projection_scales[position]
            if scale == 0.0:
                continue
            start = position * self.dimensions
            row = self.projection
            factor = weight * scale
            for offset in range(self.dimensions):
                vector[offset] += _signed(row[start + offset]) * factor
        if not matched:
            return None
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return None
        return [value / norm for value in vector]

    def similarity(self, query_vector: Sequence[float], document_id: int) -> float:
        """Cosine similarity against one stored document vector.

        Stored vectors are L2-normalized before quantization, so the int8 row's
        own norm is recomputed here rather than assumed to be exactly 1.
        """

        if not 0 <= document_id < self.document_count:
            raise IndexError("document_id is outside the embedding index")
        start = document_id * self.dimensions
        row = self.document_vectors
        total = 0.0
        square = 0
        for offset in range(self.dimensions):
            value = _signed(row[start + offset])
            total += query_vector[offset] * value
            square += value * value
        if square == 0:
            return 0.0
        return total / math.sqrt(square)


    def document_similarity(self, left: int, right: int) -> float:
        """Cosine between two stored listings.

        This is the direction the frozen LSA space is actually good at: both
        sides are in-vocabulary product text of similar length. The retrieval
        evaluation shows the same space does *not* help query-to-document
        ranking on this corpus, so it is used here and reported there.
        """

        if not (
            0 <= left < self.document_count and 0 <= right < self.document_count
        ):
            raise IndexError("document id is outside the embedding index")
        row = self.document_vectors
        dimensions = self.dimensions
        left_start = left * dimensions
        right_start = right * dimensions
        total = 0
        left_square = 0
        right_square = 0
        for offset in range(dimensions):
            a = _signed(row[left_start + offset])
            b = _signed(row[right_start + offset])
            total += a * b
            left_square += a * a
            right_square += b * b
        if left_square == 0 or right_square == 0:
            return 0.0
        return total / math.sqrt(left_square * right_square)


def write_embedding_index(
    path: Path,
    *,
    dimensions: int,
    terms: Sequence[str],
    projection_rows: Sequence[Sequence[float]],
    document_vectors: Sequence[Sequence[float]],
    catalog_sha256: str,
    explained_variance: float,
    trainer: Mapping[str, object],
) -> tuple[int, str]:
    if len(terms) != len(projection_rows):
        raise ValueError("projection rows and terms disagree in length")
    projection = bytearray()
    scales = array("f")
    for row in projection_rows:
        if len(row) != dimensions:
            raise ValueError("projection row has the wrong dimensionality")
        packed, scale = quantize(row)
        projection += packed
        scales.append(scale)
    documents = bytearray()
    for vector in document_vectors:
        if len(vector) != dimensions:
            raise ValueError("document vector has the wrong dimensionality")
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        unit = [float(value) / norm for value in vector] if norm else list(vector)
        packed, _ = quantize(unit)
        documents += packed
    joined, offsets = pack_string_table(list(terms))
    header = {
        "kind": INDEX_KIND,
        "analyzer_version": ANALYZER_VERSION,
        "catalog_sha256": catalog_sha256,
        "dimensions": int(dimensions),
        "vocabulary_size": len(terms),
        "document_count": len(document_vectors),
        "quantization": "int8-symmetric",
        "explained_variance": float(explained_variance),
        "trainer": dict(trainer),
    }
    scale_bytes = scales.tobytes()
    sections = {
        "terms": joined,
        "term_offsets": offsets,
        "projection": bytes(projection),
        "projection_scales": scale_bytes,
        "document_vectors": bytes(documents),
    }
    return write_artifact(path, header, sections)


def load_embedding_index(
    path: Path,
    *,
    expected_catalog_sha256: str | None = None,
    expected_document_count: int | None = None,
) -> EmbeddingIndex:
    artifact = read_artifact(path)
    expected_sections = {
        "terms",
        "term_offsets",
        "projection",
        "projection_scales",
        "document_vectors",
    }
    if set(artifact.sections) != expected_sections:
        raise ArtifactError("embedding index sections do not match its schema")
    if artifact.require("kind") != INDEX_KIND:
        raise ArtifactError(f"expected {INDEX_KIND}, found {artifact.header.get('kind')!r}")
    if artifact.require("analyzer_version") != ANALYZER_VERSION:
        raise ArtifactError(
            "embedding index was built by a different analyzer version; rebuild"
        )
    catalog_sha256, document_count = validate_catalog_binding(
        artifact,
        expected_catalog_sha256=expected_catalog_sha256,
        expected_document_count=expected_document_count,
    )
    dimensions_value = artifact.require("dimensions")
    if (
        isinstance(dimensions_value, bool)
        or not isinstance(dimensions_value, int)
        or not 1 <= dimensions_value <= 4096
    ):
        raise ArtifactError("embedding dimensions are invalid")
    dimensions = dimensions_value
    terms = tuple(
        unpack_string_table(artifact.section("terms"), artifact.section("term_offsets"))
    )
    scales = array("f")
    raw_scales = artifact.section("projection_scales")
    if len(raw_scales) % 4:
        raise ArtifactError("embedding projection scales are truncated")
    scales.frombytes(raw_scales)
    if len(scales) != len(terms):
        raise ArtifactError("projection scales and vocabulary disagree in length")
    if int(artifact.require("vocabulary_size")) != len(terms):
        raise ArtifactError("embedding vocabulary count does not match its table")
    projection = artifact.section("projection")
    if len(projection) != len(terms) * dimensions:
        raise ArtifactError("embedding projection table has an unexpected size")
    document_vectors = artifact.section("document_vectors")
    if len(document_vectors) != document_count * dimensions:
        raise ArtifactError("document vector table has an unexpected size")
    return EmbeddingIndex(
        dimensions=dimensions,
        terms=terms,
        term_positions={term: position for position, term in enumerate(terms)},
        projection=projection,
        projection_scales=scales,
        document_vectors=document_vectors,
        document_count=document_count,
        catalog_sha256=catalog_sha256,
        index_bytes=Path(path).stat().st_size,
        explained_variance=float(artifact.require("explained_variance")),
    )
