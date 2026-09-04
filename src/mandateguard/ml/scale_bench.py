"""Reproducible single-process scale benchmark for the discovery catalog.

Measures what can actually be measured on one machine: how big the catalog and
its indexes are, how long a cold start takes, and what a frozen query workload
costs at the 50th, 95th, and 99th percentile.

It does not measure distributed throughput, and it does not extrapolate. A
number here is a number this process produced on this hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
import gc
from hashlib import sha256
import platform
import random
import sys
from time import perf_counter
from typing import Any, Sequence

from mandateguard.discovery.index.hybrid import (
    DEFAULT_ALPHA,
    DEFAULT_CANDIDATE_DEPTH,
    StructuredFilter,
)
from mandateguard.discovery.search import DiscoveryEngine


BENCHMARK_VERSION = "discovery-scale-benchmark-v1"
BENCHMARK_SEED = 20260903
DEFAULT_QUERY_COUNT = 500
WARMUP_QUERIES = 25

#: Query templates. Deliberately varied in selectivity: a benchmark made only of
#: narrow queries measures the fast path and calls it the system.
_TEMPLATES: tuple[str, ...] = (
    "{term} under Rs {price}",
    "buy {term} below {price}",
    "{brand} {term} under Rs {price}",
    "{term} for {audience} under {price}",
    "{term}",
    "{term} and {second} under Rs {price}, one-time payment only",
    "cheap {term} no subscriptions",
    "{adjective} {term} under Rs {price}",
)
_AUDIENCES = ("men", "women", "kids", "home", "office", "travel")
_ADJECTIVES = ("printed", "cotton", "wireless", "stainless steel", "leather", "portable")
_PRICES = (300, 500, 800, 1200, 1500, 2000, 3000, 5000, 10000, 25000)


@dataclass(frozen=True, slots=True)
class BenchmarkQuery:
    query_id: str
    text: str


def build_query_workload(
    engine: DiscoveryEngine, *, count: int = DEFAULT_QUERY_COUNT
) -> tuple[list[BenchmarkQuery], str]:
    """Generate a frozen workload from the catalog's own vocabulary.

    Terms come from real listing titles, so the workload exercises the terms the
    index actually contains rather than words chosen to look fast.
    """

    rng = random.Random(BENCHMARK_SEED)
    titles = [product.title for product in engine.catalog]
    brands = list(engine.brands) or ["generic"]
    terms: list[str] = []
    for title in titles:
        words = [word for word in title.split() if len(word) > 3 and word.isalpha()]
        if len(words) >= 2:
            terms.append(" ".join(words[:2]).casefold())
        elif words:
            terms.append(words[0].casefold())
    terms = sorted(set(terms))
    queries: list[BenchmarkQuery] = []
    for index in range(count):
        template = _TEMPLATES[index % len(_TEMPLATES)]
        text = template.format(
            term=rng.choice(terms),
            second=rng.choice(terms),
            brand=rng.choice(brands),
            audience=rng.choice(_AUDIENCES),
            adjective=rng.choice(_ADJECTIVES),
            price=rng.choice(_PRICES),
        )
        queries.append(BenchmarkQuery(query_id=f"B{index:04d}", text=text))
    digest = sha256(
        "\n".join(f"{item.query_id}:{item.text}" for item in queries).encode("utf-8")
    ).hexdigest()
    return queries, digest


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[position]


def _resident_memory_mb() -> float | None:
    """Best-effort RSS, without adding a dependency to read it."""

    try:  # Linux and most containers
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    if sys.platform != "win32":
        return None
    try:  # Windows working set, via the kernel32-hosted PSAPI entry point.
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # The prototypes are declared rather than inferred: the default int
        # restype truncates the 64-bit pseudo-handle and the call silently fails.
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        query = kernel32.K32GetProcessMemoryInfo
        query.restype = wintypes.BOOL
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / (1024.0 * 1024.0), 1)
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    return None


def run_benchmark(
    *,
    processed_dir: Any,
    models_dir: Any,
    query_count: int = DEFAULT_QUERY_COUNT,
    top_k: int = 8,
    with_embedding: bool = True,
) -> dict[str, Any]:
    """Cold-load the engine, then execute the frozen workload and time it."""

    gc.collect()
    baseline_memory = _resident_memory_mb()
    started = perf_counter()
    engine = DiscoveryEngine.load(
        processed_dir=processed_dir,
        models_dir=models_dir,
        with_embedding=with_embedding,
    )
    cold_load_seconds = perf_counter() - started
    loaded_memory = _resident_memory_mb()

    queries, digest = build_query_workload(engine, count=query_count)
    for item in queries[:WARMUP_QUERIES]:
        engine.search(item.text, top_k=top_k)

    retrieval_latencies: list[float] = []
    full_latencies: list[float] = []
    empty_results = 0
    wall_started = perf_counter()
    for item in queries:
        intent = engine.parse(item.text)
        structured = StructuredFilter(
            max_unit_price_minor=intent.max_unit_price_minor,
            currency=intent.currency,
            exclusion_terms=tuple(term.casefold() for term in intent.exclusions),
        )
        retrieval_started = perf_counter()
        outcome = engine.retriever.retrieve(
            query=intent.search_text or item.text,
            structured=structured,
            alpha=DEFAULT_ALPHA,
            top_k=top_k,
            candidate_depth=DEFAULT_CANDIDATE_DEPTH,
        )
        retrieval_latencies.append((perf_counter() - retrieval_started) * 1000.0)
        if not outcome.listings:
            empty_results += 1
    retrieval_wall = perf_counter() - wall_started

    wall_started = perf_counter()
    for item in queries:
        engine.search(item.text, top_k=top_k)
    full_wall = perf_counter() - wall_started
    for item in queries:
        started_one = perf_counter()
        engine.search(item.text, top_k=top_k)
        full_latencies.append((perf_counter() - started_one) * 1000.0)

    statistics = engine.statistics()
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "workload_digest": digest,
        # Binds this measurement to the exact catalog it was taken on, the same
        # way every frozen index is bound. A benchmark that does not name its
        # corpus is a number without a subject.
        "catalog_sha256": engine.catalog.catalog_sha256,
        "document_count": len(engine.catalog),
        "queries_executed": len(queries) * 3 + WARMUP_QUERIES,
        "queries_timed": len(queries),
        "top_k": top_k,
        "catalog_listings": statistics["catalog_listings"],
        "categories": statistics["top_level_categories"],
        "category_paths": statistics["distinct_category_paths"],
        "catalog_bytes": statistics["catalog_bytes"],
        "index_bytes": statistics["index_bytes"],
        "embedding_dimensions": statistics["embedding_dimensions"],
        "lexical_terms": statistics["lexical_terms"],
        "cold_load_seconds": round(cold_load_seconds, 4),
        "resident_memory_mb": loaded_memory,
        "resident_memory_before_load_mb": baseline_memory,
        "memory_attributable_to_engine_mb": (
            round(loaded_memory - baseline_memory, 1)
            if loaded_memory is not None and baseline_memory is not None
            else None
        ),
        "retrieval_latency_ms": {
            "p50": round(_percentile(retrieval_latencies, 0.50), 3),
            "p95": round(_percentile(retrieval_latencies, 0.95), 3),
            "p99": round(_percentile(retrieval_latencies, 0.99), 3),
            "max": round(max(retrieval_latencies), 3),
        },
        "query_latency_ms": {
            "p50": round(_percentile(full_latencies, 0.50), 3),
            "p95": round(_percentile(full_latencies, 0.95), 3),
            "p99": round(_percentile(full_latencies, 0.99), 3),
            "max": round(max(full_latencies), 3),
        },
        "queries_per_second": round(len(queries) / full_wall, 2),
        "retrieval_queries_per_second": round(len(queries) / retrieval_wall, 2),
        "empty_result_queries": empty_results,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "process_count": 1,
        },
        "scope_limit": (
            "One process, one machine, no concurrency, no network. This is not a "
            "distributed-throughput measurement and nothing here is extrapolated "
            "to a larger corpus, to a container, or to any other hardware."
        ),
        "latency_note": (
            "retrieval_latency_ms times the retrieval call alone. "
            "query_latency_ms times the whole discovery request, which also "
            "parses the intent and runs classification, mismatch, anomaly and "
            "transactability per candidate. They are not interchangeable."
        ),
    }
