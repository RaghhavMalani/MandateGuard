"""Hybrid decision-retrieval interfaces."""

from mandateguard.intelligence.retrieval.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingBatch,
    EmbeddingProvider,
    HashingEmbeddingProvider,
    MappingEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from mandateguard.intelligence.retrieval.hybrid import (
    DEFAULT_ALPHA,
    DEFAULT_TOP_K,
    HybridRetriever,
    RetrievalMode,
)
from mandateguard.intelligence.retrieval.lexical import lexical_scores, tokenize
from mandateguard.intelligence.retrieval.query import (
    build_retrieval_query,
    mandate_documents,
)

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_TOP_K",
    "EmbeddingBatch",
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "HybridRetriever",
    "MappingEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "RetrievalMode",
    "build_retrieval_query",
    "lexical_scores",
    "mandate_documents",
    "tokenize",
]
