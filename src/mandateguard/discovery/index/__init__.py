"""Frozen retrieval indexes for the large discovery catalog."""

from mandateguard.discovery.index.analyzer import ANALYZER_VERSION, analyze, analyze_unique
from mandateguard.discovery.index.artifacts import ArtifactError, read_artifact, write_artifact
from mandateguard.discovery.index.embedding import (
    EmbeddingIndex,
    load_embedding_index,
    write_embedding_index,
)
from mandateguard.discovery.index.hybrid import (
    DEFAULT_ALPHA,
    DEFAULT_CANDIDATE_DEPTH,
    DEFAULT_TOP_K,
    HybridDiscoveryRetriever,
    RETRIEVAL_METHOD,
    RetrievalOutcome,
    RetrievedListing,
    StructuredFilter,
)
from mandateguard.discovery.index.lexical import (
    LexicalIndex,
    build_lexical_index,
    field_terms,
    load_lexical_index,
    write_lexical_index,
)

__all__ = [
    "ANALYZER_VERSION",
    "ArtifactError",
    "DEFAULT_ALPHA",
    "DEFAULT_CANDIDATE_DEPTH",
    "DEFAULT_TOP_K",
    "EmbeddingIndex",
    "HybridDiscoveryRetriever",
    "LexicalIndex",
    "RETRIEVAL_METHOD",
    "RetrievalOutcome",
    "RetrievedListing",
    "StructuredFilter",
    "analyze",
    "analyze_unique",
    "build_lexical_index",
    "field_terms",
    "load_embedding_index",
    "load_lexical_index",
    "read_artifact",
    "write_artifact",
    "write_embedding_index",
    "write_lexical_index",
]
