"""Embedding provider boundary with deterministic offline implementations."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import re
from typing import Mapping, Protocol, runtime_checkable


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.vectors, tuple) or not all(
            isinstance(vector, tuple) and vector
            for vector in self.vectors
        ):
            raise ValueError("vectors must be a tuple of non-empty tuples")
        dimensions = {len(vector) for vector in self.vectors}
        if len(dimensions) > 1:
            raise ValueError("all vectors must have the same dimensions")
        if not all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for vector in self.vectors
            for value in vector
        ):
            raise ValueError("embedding vectors must contain finite numbers")
        if self.input_tokens is not None and (
            isinstance(self.input_tokens, bool)
            or not isinstance(self.input_tokens, int)
            or self.input_tokens < 0
        ):
            raise ValueError("input_tokens must be non-negative or null")


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_id: str

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch: ...


@dataclass(frozen=True, slots=True)
class HashingEmbeddingProvider:
    """Network-free token hashing for tests and local product demonstrations."""

    dimensions: int = 64
    model_id: str = "offline-hashing-embedding-v1"

    def __post_init__(self) -> None:
        if (
            isinstance(self.dimensions, bool)
            or not isinstance(self.dimensions, int)
            or not 8 <= self.dimensions <= 4096
        ):
            raise ValueError("dimensions must be between 8 and 4096")

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        if not isinstance(texts, tuple) or not texts or not all(
            isinstance(text, str) for text in texts
        ):
            raise TypeError("texts must be a non-empty tuple of strings")
        vectors: list[tuple[float, ...]] = []
        token_count = 0
        for text in texts:
            vector = [0.0] * self.dimensions
            tokens = tuple(token.lower() for token in _TOKEN_RE.findall(text))
            token_count += len(tokens)
            for token in tokens:
                digest = sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[index] += 1.0
            vectors.append(tuple(vector))
        return EmbeddingBatch(vectors=tuple(vectors), input_tokens=token_count)


@dataclass(frozen=True, slots=True)
class MappingEmbeddingProvider:
    """Exact fake vectors for unit tests."""

    vectors: Mapping[str, tuple[float, ...]]
    model_id: str = "fake-mapping-embedding-v1"

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        try:
            vectors = tuple(self.vectors[text] for text in texts)
        except KeyError as exc:
            raise ValueError("fake embedding vector is not configured") from exc
        return EmbeddingBatch(vectors=vectors)


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingProvider:
    """Injected OpenAI embeddings adapter; no client is created at import time."""

    client: object
    model_id: str = DEFAULT_EMBEDDING_MODEL

    def __post_init__(self) -> None:
        if self.client is None:
            raise TypeError("client must be injected")
        if not isinstance(self.model_id, str) or not self.model_id or len(self.model_id) > 256:
            raise ValueError("model_id must be a bounded non-empty string")

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        if not isinstance(texts, tuple) or not texts or not all(
            isinstance(text, str) and text for text in texts
        ):
            raise TypeError("texts must be a non-empty tuple of strings")
        response = self.client.embeddings.create(
            model=self.model_id,
            input=list(texts),
        )
        data = getattr(response, "data", None)
        if not isinstance(data, (list, tuple)) or len(data) != len(texts):
            raise ValueError("embedding response has invalid cardinality")
        indexed: list[tuple[int, tuple[float, ...]]] = []
        for fallback_index, item in enumerate(data):
            index = item.get("index") if isinstance(item, dict) else getattr(item, "index", fallback_index)
            vector = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("embedding response index is invalid")
            if not isinstance(vector, (list, tuple)):
                raise ValueError("embedding response vector is invalid")
            indexed.append((index, tuple(float(value) for value in vector)))
        indexed.sort(key=lambda item: item[0])
        usage = getattr(response, "usage", None)
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else getattr(usage, "prompt_tokens", None)
        if isinstance(prompt_tokens, bool) or not isinstance(prompt_tokens, int):
            prompt_tokens = None
        return EmbeddingBatch(
            vectors=tuple(vector for _, vector in indexed),
            input_tokens=prompt_tokens,
        )
