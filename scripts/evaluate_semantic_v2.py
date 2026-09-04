"""Evaluate pinned pretrained encoders on the frozen semantic-v2 query set."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandateguard.discovery.catalog import load_catalog  # noqa: E402
from mandateguard.discovery.index.lexical import load_lexical_index  # noqa: E402
from mandateguard.ml.semantic_v2_eval import (  # noqa: E402
    CandidateSpec,
    evaluate_candidate,
    query_file_sha256,
)


def rss_bytes() -> int:
    if os.name != "nt":
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return int(counters.WorkingSetSize)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "engineering" / "semantic-v2" / "evaluation.json")
    args = parser.parse_args(argv)

    query_path = ROOT / "data" / "eval" / "semantic-v2" / "queries.json"
    freeze = json.loads((query_path.parent / "FREEZE.json").read_text(encoding="utf-8"))
    if query_file_sha256(query_path) != freeze["query_artifact_sha256"]:
        raise ValueError("semantic-v2 query artifact no longer matches its freeze")
    query_set = json.loads(query_path.read_text(encoding="utf-8"))
    query_set["report_slices"] = freeze["report_slices"]
    catalog_started = perf_counter()
    catalog = load_catalog(ROOT / "data" / "processed")
    catalog_load_ms = (perf_counter() - catalog_started) * 1000.0
    if catalog.catalog_sha256 != freeze["catalog_sha256"]:
        raise ValueError("catalog no longer matches semantic-v2 freeze")
    lexical_started = perf_counter()
    lexical = load_lexical_index(
        ROOT / "data" / "models" / "lexical_index.mgdx",
        expected_catalog_sha256=catalog.catalog_sha256,
        expected_document_count=len(catalog),
    )
    lexical_load_ms = (perf_counter() - lexical_started) * 1000.0
    baseline_rss = rss_bytes()

    provenance_dir = ROOT / "data" / "provenance" / "semantic-models"
    paths = sorted(provenance_dir.glob("*.json"))
    if args.model:
        wanted = set(args.model)
        paths = [path for path in paths if json.loads(path.read_text(encoding="utf-8"))["model_id"] in wanted]
    if not paths:
        raise ValueError("no candidate provenance records selected")
    reports = []
    for path in paths:
        spec = CandidateSpec.from_provenance(path, ROOT / ".cache" / "semantic-v2")
        if not spec.model_path.exists() or not spec.tokenizer_path.exists():
            raise FileNotFoundError(f"candidate cache missing for {spec.model_id}")
        report = evaluate_candidate(
            spec=spec,
            catalog=catalog,
            lexical=lexical,
            query_set=query_set,
            cache_root=ROOT / ".cache" / "semantic-v2",
            rss_bytes=rss_bytes(),
        )
        report["resident_memory_bytes"] = rss_bytes()
        reports.append(report)
        print(
            spec.model_id,
            "paraphrase R@10",
            report["configurations"]["rrf"]["slices"]["paraphrase"]["recall_at_10"],
        )
    payload = {
        "schema_version": "semantic-v2-evaluation-v1",
        "query_artifact_sha256": freeze["query_artifact_sha256"],
        "freeze_payload_sha256": freeze["freeze_payload_sha256"],
        "catalog_sha256": catalog.catalog_sha256,
        "catalog_document_count": len(catalog),
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "process_count": 1,
        },
        "loads": {
            "catalog_ms": round(catalog_load_ms, 3),
            "lexical_index_ms": round(lexical_load_ms, 3),
            "baseline_resident_memory_bytes": baseline_rss,
        },
        "candidates": reports,
        "external_calls_during_inference": {"openai": 0, "hugging_face_api": 0, "razorpay_http": 0},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
