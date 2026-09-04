"""Frozen BM25 inverted index: built offline, queried with the standard library.

Fields are weighted at build time by repeating a token's contribution, so the
runtime scorer stays a plain BM25 over one term-frequency stream. Postings are
delta-varint encoded and term frequencies are capped at 255, which keeps the
whole index a few megabytes for a 17k-listing catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from mandateguard.discovery.index.analyzer import ANALYZER_VERSION, analyze
from mandateguard.discovery.index.artifacts import (
    Artifact,
    ArtifactError,
    pack_string_table,
    pack_varints,
    read_artifact,
    unpack_string_table,
    unpack_varints,
    validate_catalog_binding,
    write_artifact,
)


INDEX_KIND = "lexical-bm25-v1"
K1 = 1.2
B = 0.75
MAX_TERM_FREQUENCY = 255

#: Field weights. A title match should outrank the same word buried in prose.
FIELD_WEIGHTS: Mapping[str, int] = {
    "title": 4,
    "brand": 3,
    "category": 2,
    "description": 1,
}


def field_terms(
    *, title: str, brand: str | None, category: str, description: str
) -> list[str]:
    """Expand a listing's fields into one weighted token stream."""

    stream: list[str] = []
    for name, text in (
        ("title", title),
        ("brand", brand or ""),
        ("category", category),
        ("description", description),
    ):
        if not text:
            continue
        tokens = analyze(text)
        stream.extend(tokens * FIELD_WEIGHTS[name])
    return stream


@dataclass(frozen=True, slots=True)
class LexicalIndex:
    """A loaded BM25 index over ``document_count`` listings."""

    terms: tuple[str, ...]
    postings: bytes
    term_offsets: tuple[int, ...]
    term_counts: tuple[int, ...]
    document_lengths: tuple[int, ...]
    average_length: float
    document_count: int
    catalog_sha256: str
    index_bytes: int

    def __post_init__(self) -> None:
        if self.document_count <= 0:
            raise ArtifactError("lexical index contains no documents")

    def term_index(self, term: str) -> int | None:
        low, high = 0, len(self.terms) - 1
        while low <= high:
            middle = (low + high) // 2
            candidate = self.terms[middle]
            if candidate == term:
                return middle
            if candidate < term:
                low = middle + 1
            else:
                high = middle - 1
        return None

    def postings_for(self, term: str) -> tuple[list[int], list[int]] | None:
        """Return ``(document_ids, term_frequencies)`` for ``term``."""

        position = self.term_index(term)
        if position is None:
            return None
        count = self.term_counts[position]
        cursor = self.term_offsets[position]
        deltas, cursor = unpack_varints(self.postings, cursor, count)
        frequencies, _ = unpack_varints(self.postings, cursor, count)
        documents: list[int] = []
        running = 0
        for delta in deltas:
            running += delta
            documents.append(running)
        return documents, frequencies

    def score(self, query_terms: Sequence[str], *, limit: int) -> list[tuple[int, float]]:
        """BM25-score the query and return the ``limit`` best documents."""

        accumulator: dict[int, float] = {}
        corpus = self.document_count
        for term in query_terms:
            found = self.postings_for(term)
            if found is None:
                continue
            documents, frequencies = found
            document_frequency = len(documents)
            idf = math.log(
                1.0 + (corpus - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            if idf <= 0.0:
                continue
            for document_id, frequency in zip(documents, frequencies, strict=True):
                length = self.document_lengths[document_id]
                denominator = frequency + K1 * (
                    1.0 - B + B * length / self.average_length
                )
                accumulator[document_id] = accumulator.get(document_id, 0.0) + idf * (
                    frequency * (K1 + 1.0) / denominator
                )
        if not accumulator:
            return []
        ranked = sorted(accumulator.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:limit]

    def matched_terms(self, query_terms: Sequence[str], document_id: int) -> list[str]:
        """Which query terms this document actually contains. Used to explain."""

        hits: list[str] = []
        for term in query_terms:
            found = self.postings_for(term)
            if found is None:
                continue
            documents, _ = found
            low, high = 0, len(documents) - 1
            while low <= high:
                middle = (low + high) // 2
                if documents[middle] == document_id:
                    hits.append(term)
                    break
                if documents[middle] < document_id:
                    low = middle + 1
                else:
                    high = middle - 1
        return hits


def build_lexical_index(
    documents: Iterable[Sequence[str]], *, minimum_document_frequency: int = 1
) -> dict[str, object]:
    """Build the in-memory structures for ``documents`` (weighted token streams)."""

    postings: dict[str, dict[int, int]] = {}
    lengths: list[int] = []
    for document_id, tokens in enumerate(documents):
        lengths.append(len(tokens))
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            postings.setdefault(token, {})[document_id] = min(
                count, MAX_TERM_FREQUENCY
            )
    if minimum_document_frequency > 1:
        postings = {
            term: entry
            for term, entry in postings.items()
            if len(entry) >= minimum_document_frequency
        }
    return {
        "postings": postings,
        "document_lengths": lengths,
        "document_count": len(lengths),
    }


def write_lexical_index(
    built: Mapping[str, object], path: Path, *, catalog_sha256: str
) -> tuple[int, str]:
    postings: dict[str, dict[int, int]] = built["postings"]  # type: ignore[assignment]
    lengths: list[int] = built["document_lengths"]  # type: ignore[assignment]
    terms = sorted(postings)
    stream = bytearray()
    offsets: list[int] = []
    counts: list[int] = []
    for term in terms:
        entry = postings[term]
        document_ids = sorted(entry)
        deltas: list[int] = []
        previous = 0
        for document_id in document_ids:
            deltas.append(document_id - previous)
            previous = document_id
        offsets.append(len(stream))
        counts.append(len(document_ids))
        stream += pack_varints(deltas)
        stream += pack_varints([entry[document_id] for document_id in document_ids])
    joined, table_offsets = pack_string_table(terms)
    header = {
        "kind": INDEX_KIND,
        "analyzer_version": ANALYZER_VERSION,
        "catalog_sha256": catalog_sha256,
        "document_count": len(lengths),
        "term_count": len(terms),
        "k1": K1,
        "b": B,
        "field_weights": dict(FIELD_WEIGHTS),
        "average_length": (sum(lengths) / len(lengths)) if lengths else 0.0,
    }
    sections = {
        "terms": joined,
        "term_offsets": table_offsets,
        "postings": bytes(stream),
        "posting_offsets": b"".join(
            value.to_bytes(5, "big") for value in offsets
        ),
        "posting_counts": b"".join(value.to_bytes(4, "big") for value in counts),
        "document_lengths": b"".join(
            min(value, 0xFFFFFFFF).to_bytes(4, "big") for value in lengths
        ),
    }
    return write_artifact(path, header, sections)


def load_lexical_index(
    path: Path,
    *,
    expected_catalog_sha256: str | None = None,
    expected_document_count: int | None = None,
) -> LexicalIndex:
    artifact = read_artifact(path)
    _validate(artifact)
    catalog_sha256, document_count = validate_catalog_binding(
        artifact,
        expected_catalog_sha256=expected_catalog_sha256,
        expected_document_count=expected_document_count,
    )
    terms = unpack_string_table(
        artifact.section("terms"), artifact.section("term_offsets")
    )
    raw_offsets = artifact.section("posting_offsets")
    raw_counts = artifact.section("posting_counts")
    raw_lengths = artifact.section("document_lengths")
    offsets = tuple(
        int.from_bytes(raw_offsets[index : index + 5], "big")
        for index in range(0, len(raw_offsets), 5)
    )
    counts = tuple(
        int.from_bytes(raw_counts[index : index + 4], "big")
        for index in range(0, len(raw_counts), 4)
    )
    lengths = tuple(
        int.from_bytes(raw_lengths[index : index + 4], "big")
        for index in range(0, len(raw_lengths), 4)
    )
    if not (len(terms) == len(offsets) == len(counts)):
        raise ArtifactError("lexical index term tables disagree in length")
    if len(raw_offsets) % 5 or len(raw_counts) % 4 or len(raw_lengths) % 4:
        raise ArtifactError("lexical index numeric table is truncated")
    if len(lengths) != document_count:
        raise ArtifactError("lexical index document lengths disagree with document count")
    if int(artifact.require("term_count")) != len(terms):
        raise ArtifactError("lexical index term count does not match its tables")
    postings = artifact.section("postings")
    for offset, count in zip(offsets, counts, strict=True):
        if offset > len(postings):
            raise ArtifactError("lexical posting offset is out of bounds")
        deltas, cursor = unpack_varints(postings, offset, count)
        frequencies, _ = unpack_varints(postings, cursor, count)
        running = 0
        for delta, frequency in zip(deltas, frequencies, strict=True):
            running += delta
            if running >= document_count or frequency <= 0:
                raise ArtifactError("lexical posting contains an invalid document or frequency")
    return LexicalIndex(
        terms=tuple(terms),
        postings=postings,
        term_offsets=offsets,
        term_counts=counts,
        document_lengths=lengths,
        average_length=float(artifact.require("average_length")) or 1.0,
        document_count=document_count,
        catalog_sha256=catalog_sha256,
        index_bytes=Path(path).stat().st_size,
    )


def _validate(artifact: Artifact) -> None:
    expected_sections = {
        "terms",
        "term_offsets",
        "postings",
        "posting_offsets",
        "posting_counts",
        "document_lengths",
    }
    if set(artifact.sections) != expected_sections:
        raise ArtifactError("lexical index sections do not match its schema")
    if artifact.require("kind") != INDEX_KIND:
        raise ArtifactError(f"expected {INDEX_KIND}, found {artifact.header.get('kind')!r}")
    if artifact.require("analyzer_version") != ANALYZER_VERSION:
        raise ArtifactError(
            "index was built by a different analyzer version; rebuild the artifacts"
        )
