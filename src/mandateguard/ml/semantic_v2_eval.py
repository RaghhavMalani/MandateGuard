"""Frozen semantic-v2 evaluation with independent BM25 and dense generation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from mandateguard.discovery.catalog import DiscoveryCatalog
from mandateguard.discovery.index.analyzer import analyze_unique
from mandateguard.discovery.index.lexical import LexicalIndex
from mandateguard.discovery.schema import DiscoveryProduct


RRF_K = 60
BM25_WEIGHT = 0.5
DENSE_WEIGHT = 0.5
CANDIDATE_LIMIT = 100
FINAL_LIMIT = 10
EVALUATION_SEQUENCE_LENGTH = 128
ONNX_CPU_THREADS = 4


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 0.50), 3),
        "p95": round(percentile(values, 0.95), 3),
        "p99": round(percentile(values, 0.99), 3),
    }


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    model_id: str
    revision: str
    model_path: Path
    tokenizer_path: Path
    dimension: int
    maximum_sequence_length: int
    pooling: str
    query_prefix: str
    document_prefix: str
    model_sha256: str
    tokenizer_sha256: str

    @classmethod
    def from_provenance(cls, provenance_path: Path, cache_root: Path) -> CandidateSpec:
        value = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
        local = cache_root / value["model_id"].replace("/", "--")
        return cls(
            model_id=value["model_id"],
            revision=value["revision"],
            model_path=local / value["onnx_file"],
            tokenizer_path=local / "tokenizer.json",
            dimension=int(value["dimension"]),
            maximum_sequence_length=int(value["maximum_sequence_length"]),
            pooling=value["pooling"],
            query_prefix=value["query_prefix"],
            document_prefix=value["document_prefix"],
            model_sha256=value["model_sha256"],
            tokenizer_sha256=value["tokenizer_identity"]["tokenizer_json_sha256"],
        )


class OnnxSentenceEncoder:
    """Local sentence encoder; imports neither torch nor transformers."""

    def __init__(self, spec: CandidateSpec) -> None:
        self.spec = spec
        started = perf_counter()
        self.tokenizer = Tokenizer.from_file(str(spec.tokenizer_path))
        self.tokenizer.enable_truncation(
            max_length=min(spec.maximum_sequence_length, EVALUATION_SEQUENCE_LENGTH)
        )
        self.tokenizer.enable_padding()
        self.tokenizer_load_ms = (perf_counter() - started) * 1000.0
        started = perf_counter()
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = ONNX_CPU_THREADS
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(spec.model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.model_load_ms = (perf_counter() - started) * 1000.0
        self.input_names = {item.name for item in self.session.get_inputs()}

    def embed(
        self, texts: Sequence[str], *, kind: str
    ) -> tuple[np.ndarray, float, float]:
        if kind not in {"query", "document"}:
            raise ValueError("kind must be query or document")
        prefix = self.spec.query_prefix if kind == "query" else self.spec.document_prefix
        started = perf_counter()
        encoded = self.tokenizer.encode_batch([prefix + text for text in texts])
        tokenization_ms = (perf_counter() - started) * 1000.0
        ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
        masks = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
        type_ids = np.asarray([item.type_ids for item in encoded], dtype=np.int64)
        feeds: dict[str, np.ndarray] = {"input_ids": ids}
        if "attention_mask" in self.input_names:
            feeds["attention_mask"] = masks
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = type_ids
        started = perf_counter()
        token_vectors = self.session.run(None, feeds)[0]
        if self.spec.pooling == "CLS token":
            vectors = token_vectors[:, 0, :]
        else:
            expanded = masks[:, :, None].astype(np.float32)
            vectors = (token_vectors * expanded).sum(axis=1) / np.maximum(
                expanded.sum(axis=1), 1e-9
            )
        vectors = np.asarray(vectors, dtype=np.float32)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        inference_ms = (perf_counter() - started) * 1000.0
        if vectors.shape[1] != self.spec.dimension:
            raise ValueError("model output dimension disagrees with provenance")
        return vectors, tokenization_ms, inference_ms


def embed_catalog(
    encoder: OnnxSentenceEncoder,
    catalog: DiscoveryCatalog,
    cache_path: Path,
    *,
    batch_size: int = 64,
) -> tuple[np.ndarray, float]:
    if cache_path.exists():
        started = perf_counter()
        vectors = np.load(cache_path, allow_pickle=False)
        elapsed = (perf_counter() - started) * 1000.0
        if vectors.shape != (len(catalog), encoder.spec.dimension):
            raise ValueError("cached candidate embeddings have the wrong shape")
        return np.asarray(vectors, dtype=np.float32), elapsed
    blocks = []
    started = perf_counter()
    for offset in range(0, len(catalog), batch_size):
        products = catalog.products[offset : offset + batch_size]
        texts = [document_embedding_text(product) for product in products]
        vectors, _, _ = encoder.embed(texts, kind="document")
        blocks.append(vectors)
    result = np.concatenate(blocks, axis=0)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, result, allow_pickle=False)
    return result, (perf_counter() - started) * 1000.0


def document_embedding_text(product: DiscoveryProduct) -> str:
    """Frozen served representation: stable identity text, not noisy crawl prose."""

    return "\n".join(
        part for part in (product.title, product.brand or "", product.top_category) if part
    )


def _haystack(product: DiscoveryProduct) -> str:
    return (
        f"{product.title} {product.category_text} {product.brand or ''} "
        f"{product.description}"
    ).casefold()


def relevant_documents(
    catalog: DiscoveryCatalog, predicate: Mapping[str, Any]
) -> frozenset[int]:
    return frozenset(
        document_id
        for document_id, product in enumerate(catalog)
        if _predicate_passes(product, predicate, relevance=True)
    )


def allowed_documents(
    catalog: DiscoveryCatalog, filters: Mapping[str, Any]
) -> np.ndarray:
    return np.fromiter(
        (
            document_id
            for document_id, product in enumerate(catalog)
            if _predicate_passes(product, filters, relevance=False)
        ),
        dtype=np.int64,
    )


def _predicate_passes(
    product: DiscoveryProduct, predicate: Mapping[str, Any], *, relevance: bool
) -> bool:
    categories = predicate.get("categories", ()) or ()
    brands = predicate.get("brands", ()) or ()
    ceiling = predicate.get("max_price_minor")
    if categories and product.top_category not in categories:
        return False
    if brands and (product.brand or "").casefold() not in {
        str(item).casefold() for item in brands
    }:
        return False
    if ceiling is not None and (
        product.price_minor is None or product.price_minor > int(ceiling)
    ):
        return False
    blob = _haystack(product)
    if any(str(term).casefold() in blob for term in predicate.get("exclude_terms", ())):
        return False
    if not relevance:
        return True
    title = product.title.casefold()
    required_title = predicate.get("require_title_any", ()) or ()
    required_any = predicate.get("require_any_terms", ()) or ()
    required_all = predicate.get("require_all_terms", ()) or ()
    if required_title and not any(str(term).casefold() in title for term in required_title):
        return False
    if required_any and not any(str(term).casefold() in blob for term in required_any):
        return False
    return all(str(term).casefold() in blob for term in required_all)


def _top_dense(
    scores: np.ndarray, allowed: np.ndarray, limit: int
) -> list[tuple[int, float]]:
    if not len(allowed):
        return []
    allowed_scores = scores[allowed]
    take = min(limit, len(allowed))
    if take < len(allowed):
        positions = np.argpartition(allowed_scores, -take)[-take:]
    else:
        positions = np.arange(len(allowed), dtype=np.int64)
    ranked = [(int(allowed[pos]), float(allowed_scores[pos])) for pos in positions]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked[:take]


def _top_bm25(
    lexical: LexicalIndex, text: str, allowed: np.ndarray, limit: int
) -> list[tuple[int, float]]:
    allowed_set = set(int(item) for item in allowed)
    ranked = lexical.score(analyze_unique(text), limit=lexical.document_count)
    return [item for item in ranked if item[0] in allowed_set][:limit]


def _minmax(ranked: Sequence[tuple[int, float]]) -> dict[int, float]:
    if not ranked:
        return {}
    values = [score for _, score in ranked]
    low, high = min(values), max(values)
    if high - low <= 1e-12:
        return {document_id: 1.0 for document_id, _ in ranked}
    return {document_id: (score - low) / (high - low) for document_id, score in ranked}


def fuse_rrf(
    bm25: Sequence[tuple[int, float]], dense: Sequence[tuple[int, float]]
) -> list[int]:
    scores: dict[int, float] = {}
    for ranked in (bm25, dense):
        for rank, (document_id, _) in enumerate(ranked, start=1):
            scores[document_id] = scores.get(document_id, 0.0) + 1.0 / (RRF_K + rank)
    return [item[0] for item in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def fuse_weighted(
    bm25: Sequence[tuple[int, float]], dense: Sequence[tuple[int, float]]
) -> list[int]:
    left, right = _minmax(bm25), _minmax(dense)
    union = left.keys() | right.keys()
    return sorted(
        union,
        key=lambda document_id: (
            -(BM25_WEIGHT * left.get(document_id, 0.0) + DENSE_WEIGHT * right.get(document_id, 0.0)),
            document_id,
        ),
    )


def deduplicate(ranking: Iterable[int], catalog: DiscoveryCatalog, limit: int) -> list[int]:
    """Conservatively suppress exact-title duplicates with matching offer identity."""

    chosen: list[int] = []
    identities: set[tuple[str, str, str, str, int | None, str]] = set()
    for document_id in ranking:
        product = catalog[document_id]
        identity = (
            product.source,
            (product.brand or "").strip().casefold(),
            product.top_category,
            product.currency,
            product.price_minor,
            " ".join(product.title.casefold().split()),
        )
        if product.source != "mandateguard" and identity in identities:
            continue
        identities.add(identity)
        chosen.append(document_id)
        if len(chosen) >= limit:
            break
    return chosen


def _metrics(ranking: Sequence[int], relevant: frozenset[int]) -> dict[str, float]:
    def recall(k: int) -> float:
        return sum(item in relevant for item in ranking[:k]) / min(k, len(relevant))

    reciprocal = next(
        (1.0 / rank for rank, item in enumerate(ranking, start=1) if item in relevant),
        0.0,
    )
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(ranking[:10], start=1)
        if item in relevant
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(10, len(relevant)) + 1)
    )
    return {
        "recall_at_1": recall(1),
        "recall_at_5": recall(5),
        "recall_at_10": recall(10),
        "mrr": reciprocal,
        "ndcg_at_10": dcg / ideal,
    }


def _configuration_summary(
    per_query: Sequence[Mapping[str, Any]], slice_names: Sequence[str]
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for slice_name in slice_names:
        selected = [
            item for item in per_query
            if slice_name == "all" or slice_name in item["groups"]
        ]
        output[slice_name] = {
            "queries": len(selected),
            **{
                metric: round(sum(item[metric] for item in selected) / len(selected), 6)
                for metric in ("recall_at_1", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")
            },
        }
    return {"slices": output, "per_query": list(per_query)}


def evaluate_candidate(
    *,
    spec: CandidateSpec,
    catalog: DiscoveryCatalog,
    lexical: LexicalIndex,
    query_set: Mapping[str, Any],
    cache_root: Path,
    rss_bytes: int,
) -> dict[str, Any]:
    encoder = OnnxSentenceEncoder(spec)
    cache_path = cache_root / "embeddings" / f"{spec.model_id.replace('/', '--')}.f32.npy"
    documents, document_index_ms = embed_catalog(encoder, catalog, cache_path)
    configurations: dict[str, list[dict[str, Any]]] = {
        "bm25": [], "dense": [], "rrf": [], "weighted_fusion": []
    }
    timings: dict[str, list[float]] = {
        "query_tokenization_ms": [],
        "query_embedding_ms": [],
        "bm25_retrieval_ms": [],
        "dense_retrieval_ms": [],
        "fusion_ms": [],
        "full_discovery_request_ms": [],
    }
    relevant_sizes: dict[str, int] = {}
    queries = query_set["queries"]
    for query in queries:
        full_started = perf_counter()
        relevant = relevant_documents(catalog, query["relevance"])
        if not relevant:
            raise ValueError(f"query has no relevant documents: {query['query_id']}")
        relevant_sizes[query["query_id"]] = len(relevant)
        allowed = allowed_documents(catalog, query["hard_filters"])
        vector, tokenization_ms, embedding_ms = encoder.embed([query["text"]], kind="query")
        started = perf_counter()
        bm25 = _top_bm25(lexical, query["text"], allowed, CANDIDATE_LIMIT)
        bm25_ms = (perf_counter() - started) * 1000.0
        started = perf_counter()
        dense_scores = documents @ vector[0]
        dense = _top_dense(dense_scores, allowed, CANDIDATE_LIMIT)
        dense_ms = (perf_counter() - started) * 1000.0
        started = perf_counter()
        rankings = {
            "bm25": [item[0] for item in bm25],
            "dense": [item[0] for item in dense],
            "rrf": fuse_rrf(bm25, dense),
            "weighted_fusion": fuse_weighted(bm25, dense),
        }
        fusion_ms = (perf_counter() - started) * 1000.0
        full_ms = (perf_counter() - full_started) * 1000.0
        timings["query_tokenization_ms"].append(tokenization_ms)
        timings["query_embedding_ms"].append(embedding_ms)
        timings["bm25_retrieval_ms"].append(bm25_ms)
        timings["dense_retrieval_ms"].append(dense_ms)
        timings["fusion_ms"].append(fusion_ms)
        timings["full_discovery_request_ms"].append(full_ms)
        for name, ranking in rankings.items():
            final = deduplicate(ranking, catalog, FINAL_LIMIT)
            configurations[name].append({
                "query_id": query["query_id"],
                "groups": query["groups"],
                "relevant": len(relevant),
                **{key: round(value, 6) for key, value in _metrics(final, relevant).items()},
            })
    slices = query_set.get("report_slices") or [
        "all", "literal", "paraphrase", "category", "brand-constrained", "budget-constrained"
    ]
    return {
        "model_id": spec.model_id,
        "revision": spec.revision,
        "model_sha256": spec.model_sha256,
        "tokenizer_sha256": spec.tokenizer_sha256,
        "dimension": spec.dimension,
        "evaluation_sequence_length": min(
            spec.maximum_sequence_length, EVALUATION_SEQUENCE_LENGTH
        ),
        "onnx_cpu_threads": ONNX_CPU_THREADS,
        "normalization": "L2",
        "pooling": spec.pooling,
        "cold_load": {
            "tokenizer_ms": round(encoder.tokenizer_load_ms, 3),
            "model_ms": round(encoder.model_load_ms, 3),
            "document_index_ms": round(document_index_ms, 3),
        },
        "artifact_bytes": {
            "model": spec.model_path.stat().st_size,
            "tokenizer": spec.tokenizer_path.stat().st_size,
            "float32_document_embeddings": int(documents.nbytes),
        },
        "resident_memory_bytes": rss_bytes,
        "timings": {name: latency_summary(values) for name, values in timings.items()},
        "configurations": {
            name: _configuration_summary(values, slices)
            for name, values in configurations.items()
        },
        "relevant_set_sizes": relevant_sizes,
        "external_model_calls": 0,
    }


def query_file_sha256(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()
