"""Transparent normalized lexical retrieval."""

from __future__ import annotations

from collections import Counter
import math
import re

from mandateguard.intelligence.models import RetrievalDocument


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> tuple[str, ...]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return tuple(token.lower() for token in _TOKEN_RE.findall(text))


def lexical_scores(
    query: str, documents: tuple[RetrievalDocument, ...]
) -> dict[str, float]:
    """Return TF-IDF overlap normalized to the best document in this corpus."""

    if not isinstance(query, str) or not query:
        raise ValueError("query must be non-empty")
    if not isinstance(documents, tuple) or not all(
        isinstance(item, RetrievalDocument) for item in documents
    ):
        raise TypeError("documents must be a tuple of RetrievalDocument")
    if not documents:
        return {}
    document_tokens = {item.document_id: tokenize(item.text) for item in documents}
    query_counts = Counter(tokenize(query))
    document_frequency = Counter(
        token
        for tokens in document_tokens.values()
        for token in frozenset(tokens)
    )
    corpus_size = len(documents)
    raw: dict[str, float] = {}
    for document in documents:
        counts = Counter(document_tokens[document.document_id])
        score = 0.0
        for token, query_frequency in query_counts.items():
            if counts[token] == 0:
                continue
            inverse_document_frequency = math.log(
                1.0 + (corpus_size + 1.0) / (document_frequency[token] + 1.0)
            )
            score += (
                min(counts[token], query_frequency)
                * inverse_document_frequency
            )
        raw[document.document_id] = score
    maximum = max(raw.values(), default=0.0)
    if maximum <= 0.0:
        return {document_id: 0.0 for document_id in raw}
    return {document_id: score / maximum for document_id, score in raw.items()}
