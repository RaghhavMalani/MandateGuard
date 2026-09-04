"""Measure the generated Playground's cold build, search and memory footprint.

    python scripts/benchmark_judge_playground.py

The report is descriptive of one local process. It is not a production SLO and
does not extrapolate to Render or concurrent traffic.
"""

from __future__ import annotations

import json
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter
import tracemalloc


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.sandbox.intent import read_intent  # noqa: E402
from mandateguard.sandbox.search import SandboxSearch  # noqa: E402
from mandateguard.sandbox.store import build_sandbox_store  # noqa: E402
from mandateguard.sandbox.universe import build_universe, universe_manifest  # noqa: E402


QUERY_PATH = REPOSITORY_ROOT / "fixtures" / "playground" / "judge_queries.json"
OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "data"
    / "eval"
    / "judge-playground"
    / "RUNTIME_REPORT.json"
)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(
        len(ordered) - 1,
        max(0, int(round(fraction * (len(ordered) - 1)))),
    )
    return round(ordered[index], 3)


def _bytes(paths: list[Path]) -> int:
    return sum(path.stat().st_size for path in paths if path.is_file())


def main() -> int:
    # Time the actual cold components without the considerable instrumentation
    # overhead of tracemalloc: generate, validate into the trusted store, and
    # construct the search index.
    started = perf_counter()
    universe = build_universe()
    _store = build_sandbox_store(universe)
    search = SandboxSearch(universe)
    cold_load_ms = (perf_counter() - started) * 1000.0

    # Measure a separate equivalent build. The first build was not traced, so
    # these figures describe the allocations made by one world rather than the
    # interpreter and imports that happen to precede it.
    tracemalloc.start()
    measured_universe = build_universe()
    _measured_store = build_sandbox_store(measured_universe)
    _measured_search = SandboxSearch(measured_universe)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    query_set = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    search_times: list[float] = []
    result_counts: list[int] = []
    for entry in query_set["queries"]:
        started = perf_counter()
        result = search.search(read_intent(entry["text"]), limit=8)
        search_times.append((perf_counter() - started) * 1000.0)
        result_counts.append(len(result.candidates))

    sandbox_source = sorted(
        (REPOSITORY_ROOT / "src" / "mandateguard" / "sandbox").glob("*.py")
    )
    product_source = [REPOSITORY_ROOT / "src" / "mandateguard" / "product" / "playground.py"]
    frozen_artifacts = [
        QUERY_PATH,
        REPOSITORY_ROOT
        / "data"
        / "eval"
        / "judge-playground"
        / "SANDBOX_FREEZE.json",
        REPOSITORY_ROOT
        / "data"
        / "eval"
        / "judge-playground"
        / "JUDGE_QUERY_REPORT.json",
    ]
    static_assets = [
        REPOSITORY_ROOT / "src" / "mandateguard" / "product" / "static" / name
        for name in ("index.html", "app.css", "app.js")
    ]
    manifest = universe_manifest(universe)
    report = {
        "scope": "ONE_LOCAL_PROCESS_DESCRIPTIVE_NOT_A_PRODUCTION_SLO",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "world_version": manifest["world_version"],
        "products_sha256": manifest["products_sha256"],
        "product_count": manifest["product_count"],
        "cold_playground_build_ms": round(cold_load_ms, 3),
        "python_allocations_after_build_bytes": current_bytes,
        "python_peak_allocations_during_build_bytes": peak_bytes,
        "search": {
            "queries": len(search_times),
            "candidate_found_rate": round(
                sum(count > 0 for count in result_counts) / len(result_counts), 4
            ),
            "latency_ms": {
                "p50": _percentile(search_times, 0.50),
                "p95": _percentile(search_times, 0.95),
                "max": round(max(search_times), 3),
                "median": round(median(search_times), 3),
            },
        },
        "artifact_bytes": {
            # The catalogue itself is generated in memory and is not committed
            # as a 3,060-row JSON fixture.
            "materialized_catalog_on_disk": 0,
            "generated_catalog_text": manifest["text_bytes"],
            "sandbox_python_source": _bytes(sandbox_source + product_source),
            "frozen_query_and_reports": _bytes(frozen_artifacts),
            "full_static_ui": _bytes(static_assets),
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
