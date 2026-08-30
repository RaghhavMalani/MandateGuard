"""Machine-readable INT-2 artifact and plot-data writers."""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import asdict
from datetime import timezone
import json
from pathlib import Path
from statistics import fmean
from typing import Iterable

from mandateguard.engineering.int2.cache import CacheExperimentResult
from mandateguard.engineering.int2.embeddings import EmbeddingSnapshot
from mandateguard.engineering.int2.downstream import (
    DownstreamAuthorizationObservation,
)
from mandateguard.engineering.int2.models import (
    CostRates,
    DownstreamSelection,
    RetrievalObservation,
    SUPPORTED_ALPHAS,
    SUPPORTED_TOP_K,
    TokenUsage,
    estimate_api_cost,
)


def require_engineering_output(path: Path, *, repository_root: Path) -> Path:
    if not isinstance(path, Path) or not isinstance(repository_root, Path):
        raise TypeError("path and repository_root must be pathlib.Path")
    resolved = path.resolve()
    benchmark = (repository_root / "benchmark").resolve()
    if resolved == benchmark or benchmark in resolved.parents:
        raise ValueError("INT-2 engineering artifacts cannot be written under benchmark/")
    return resolved


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _configuration_record(observation: RetrievalObservation) -> dict[str, object]:
    configuration = observation.retrieval.configuration
    return {
        "configuration_id": configuration.configuration_id,
        "retrieval_strategy": configuration.strategy.value,
        "alpha": configuration.alpha,
        "top_k": configuration.top_k,
    }


def retrieval_observation_record(
    observation: RetrievalObservation,
) -> dict[str, object]:
    """Serialize retrieval facts and scores without any authorization verdict.

    Embedding calls, tokens, and precompute latency belong to the experiment
    run, not to an observation, so they are reported once in the summary.
    """

    retrieval = observation.retrieval
    return {
        "schema_version": "1.0",
        "experiment_stage": "retrieval_only",
        "query_id": observation.query_id,
        **_configuration_record(observation),
        "retrieved_evidence_ids": list(retrieval.retrieved_evidence_ids),
        "ranked_documents": [
            {
                "rank": rank,
                "document_id": item.document.document_id,
                "evidence_id": item.document.evidence_id,
                "lexical_score": item.score.lexical_score,
                "semantic_score": item.score.semantic_score,
                "combined_score": item.score.hybrid_score,
            }
            for rank, item in enumerate(retrieval.ranked_documents, start=1)
        ],
        "metrics": {
            "recall_at_k": observation.metrics.recall_at_k,
            "precision_at_k": observation.metrics.precision_at_k,
            "reciprocal_rank": observation.metrics.reciprocal_rank,
            "all_required_retrieved": observation.metrics.all_required_retrieved,
            "rank_of_first_required": observation.metrics.rank_of_first_required,
        },
        "timings": {
            "retrieval_latency_ms": retrieval.retrieval_latency_ms,
        },
        "embedding_source": retrieval.embedding_source.value,
    }


def embedding_experiment_record(
    snapshot: EmbeddingSnapshot,
    *,
    cost_rates: CostRates | None = None,
) -> dict[str, object]:
    """Serialize the run-level embedding accounting exactly once."""

    if not isinstance(snapshot, EmbeddingSnapshot):
        raise TypeError("snapshot must be EmbeddingSnapshot")
    cost = estimate_api_cost(
        TokenUsage(embedding_tokens=snapshot.input_token_count), cost_rates
    )
    return {
        "embedding_model": snapshot.model_id,
        "vector_dimension": snapshot.vector_dimension,
        "unique_document_texts": snapshot.unique_document_texts,
        "unique_query_texts": snapshot.unique_query_texts,
        "unique_texts_total": snapshot.unique_text_count,
        "embedding_api_calls": snapshot.provider_call_count,
        "embedding_input_tokens": snapshot.input_token_count,
        "embedding_precompute_latency_ms": snapshot.precompute_latency_ms,
        "estimated_api_cost": cost.estimated_api_cost,
        "priced_categories": list(cost.priced_categories),
        "unpriced_categories": list(cost.unpriced_categories),
    }


def _summary(
    observations: tuple[RetrievalObservation, ...],
    embedding_snapshot: EmbeddingSnapshot | None,
    cost_rates: CostRates | None,
) -> dict[str, object]:
    grouped: dict[str, list[RetrievalObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.retrieval.configuration.configuration_id].append(
            observation
        )
    by_configuration: list[dict[str, object]] = []
    for configuration_id in sorted(grouped):
        values = grouped[configuration_id]
        configuration = values[0].retrieval.configuration
        reciprocal_ranks = [
            item.metrics.reciprocal_rank
            for item in values
            if item.metrics.reciprocal_rank is not None
        ]
        by_configuration.append(
            {
                "configuration_id": configuration_id,
                "retrieval_strategy": configuration.strategy.value,
                "alpha": configuration.alpha,
                "top_k": configuration.top_k,
                "query_count": len(values),
                "mean_recall_at_k": fmean(
                    item.metrics.recall_at_k for item in values
                ),
                "mean_precision_at_k": fmean(
                    item.metrics.precision_at_k for item in values
                ),
                "mean_reciprocal_rank": (
                    fmean(reciprocal_ranks) if reciprocal_ranks else None
                ),
                "all_required_rate": fmean(
                    float(item.metrics.all_required_retrieved)
                    for item in values
                ),
                "mean_retrieval_latency_ms": fmean(
                    item.retrieval.retrieval_latency_ms for item in values
                ),
                "embedding_source": values[0].retrieval.embedding_source.value,
            }
        )
    return {
        "schema_version": "1.0",
        "experiment_stage": "retrieval_only",
        "quality_claim": "NONE_EXPERIMENT_NOT_YET_INTERPRETED",
        "query_count": len({item.query_id for item in observations}),
        "observation_count": len(observations),
        "sweep_dimensions": {
            "retrieval_strategies": [
                "no_retrieval",
                "lexical_only",
                "semantic_only",
                "hybrid",
            ],
            "hybrid_alphas": list(SUPPORTED_ALPHAS),
            "top_k": list(SUPPORTED_TOP_K),
        },
        "by_configuration": by_configuration,
        "embedding": (
            None
            if embedding_snapshot is None
            else embedding_experiment_record(
                embedding_snapshot, cost_rates=cost_rates
            )
        ),
    }


def _write_csv(path: Path, fields: tuple[str, ...], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_retrieval_artifacts(
    observations: tuple[RetrievalObservation, ...],
    output_directory: Path,
    *,
    repository_root: Path,
    embedding_snapshot: EmbeddingSnapshot | None = None,
    cost_rates: CostRates | None = None,
) -> tuple[Path, ...]:
    if not isinstance(observations, tuple) or not observations:
        raise ValueError("observations must be a non-empty tuple")
    output = require_engineering_output(
        output_directory, repository_root=repository_root
    )
    output.mkdir(parents=True, exist_ok=True)
    sweep_path = output / "retrieval_sweep.jsonl"
    with sweep_path.open("w", encoding="utf-8", newline="\n") as stream:
        for observation in observations:
            stream.write(
                json.dumps(
                    retrieval_observation_record(observation),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
    summary_path = output / "retrieval_summary.json"
    _write_json(
        summary_path, _summary(observations, embedding_snapshot, cost_rates)
    )

    common_rows = [
        {
            "query_id": item.query_id,
            "retrieval_strategy": item.retrieval.configuration.strategy.value,
            "alpha": item.retrieval.configuration.alpha,
            "top_k": item.retrieval.configuration.top_k,
            "recall_at_k": item.metrics.recall_at_k,
            "reciprocal_rank": item.metrics.reciprocal_rank,
            "retrieval_latency_ms": item.retrieval.retrieval_latency_ms,
        }
        for item in observations
    ]
    recall_k = output / "recall_at_k_vs_k.csv"
    _write_csv(
        recall_k,
        ("query_id", "retrieval_strategy", "alpha", "top_k", "recall_at_k"),
        common_rows,
    )
    recall_alpha = output / "recall_at_k_vs_alpha.csv"
    _write_csv(
        recall_alpha,
        ("query_id", "alpha", "top_k", "recall_at_k"),
        (row for row in common_rows if row["retrieval_strategy"] == "hybrid"),
    )
    mrr = output / "mrr_vs_strategy.csv"
    _write_csv(
        mrr,
        ("query_id", "retrieval_strategy", "alpha", "top_k", "reciprocal_rank"),
        common_rows,
    )
    latency = output / "latency_vs_strategy.csv"
    _write_csv(
        latency,
        (
            "query_id",
            "retrieval_strategy",
            "alpha",
            "top_k",
            "retrieval_latency_ms",
        ),
        common_rows,
    )
    review = output / "review_rate_vs_retrieval_configuration.csv"
    _write_csv(
        review,
        (
            "configuration_id",
            "retrieval_strategy",
            "alpha",
            "top_k",
            "scenario_count",
            "review_rate",
        ),
        (),
    )
    unsafe = output / "unsafe_direction_transitions_vs_configuration.csv"
    _write_csv(
        unsafe,
        (
            "configuration_id",
            "retrieval_strategy",
            "alpha",
            "top_k",
            "scenario_count",
            "unsafe_direction_transition_count",
        ),
        (),
    )
    visualization_path = output / "visualization_data.json"
    _write_json(
        visualization_path,
        {
            "recall_at_k_vs_k": common_rows,
            "recall_at_k_vs_alpha": [
                row for row in common_rows if row["retrieval_strategy"] == "hybrid"
            ],
            "mrr_vs_strategy": common_rows,
            "latency_vs_strategy": common_rows,
            "review_rate_vs_retrieval_configuration": [],
            "unsafe_direction_transitions_vs_configuration": [],
            "cache_miss_vs_hit_latency_cost": [],
        },
    )
    return (
        sweep_path,
        summary_path,
        recall_k,
        recall_alpha,
        mrr,
        latency,
        review,
        unsafe,
        visualization_path,
    )


def write_downstream_selection(
    selection: DownstreamSelection,
    output_path: Path,
    *,
    repository_root: Path,
) -> Path:
    require_engineering_output(output_path, repository_root=repository_root)
    _write_json(
        output_path,
        {
            "schema_version": "1.0",
            "selection_id": selection.selection_id,
            "recorded_at": selection.recorded_at.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "rationale": selection.rationale,
            "selections": [
                {
                    "query_id": item.query_id,
                    "configuration_id": item.configuration.configuration_id,
                    "retrieval_strategy": item.configuration.strategy.value,
                    "alpha": item.configuration.alpha,
                    "top_k": item.configuration.top_k,
                }
                for item in selection.selections
            ],
        },
    )
    return output_path


def write_downstream_results(
    results: tuple[DownstreamAuthorizationObservation, ...],
    output_directory: Path,
    *,
    selection: DownstreamSelection,
    repository_root: Path,
) -> tuple[Path, ...]:
    if not results:
        raise ValueError("results must be non-empty")
    output = require_engineering_output(
        output_directory, repository_root=repository_root
    )
    result_path = output / "downstream_authorization.jsonl"
    output.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "schema_version": "1.0",
            "selection_id": selection.selection_id,
            "query_id": item.query_id,
            "configuration_id": item.configuration.configuration_id,
            "retrieval_strategy": item.configuration.strategy.value,
            "alpha": item.configuration.alpha,
            "top_k": item.configuration.top_k,
            "engineering_expectation": item.engineering_expectation.value,
            "deterministic_action": item.deterministic_action,
            "semantic_status": item.semantic_status,
            "semantic_verdict": item.semantic_verdict,
            "final_action": item.final_action,
            "reason_code": item.reason_code,
            "semantic_api_calls": item.semantic_api_calls,
            "retrieved_evidence_ids": list(item.retrieved_evidence_ids),
            "engineering_authorization_transition": item.transition.value,
            "authorization_latency_ms": item.authorization_latency_ms,
        }
        for item in results
    ]
    with result_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    review_path = output / "review_rate_vs_retrieval_configuration.csv"
    unsafe_path = output / "unsafe_direction_transitions_vs_configuration.csv"
    grouped: dict[str, list[DownstreamAuthorizationObservation]] = defaultdict(list)
    for result in results:
        grouped[result.configuration.configuration_id].append(result)
    review_rows: list[dict[str, object]] = []
    unsafe_rows: list[dict[str, object]] = []
    for configuration_id, values in sorted(grouped.items()):
        configuration = values[0].configuration
        common = {
            "configuration_id": configuration_id,
            "retrieval_strategy": configuration.strategy.value,
            "alpha": configuration.alpha,
            "top_k": configuration.top_k,
            "scenario_count": len(values),
        }
        review_rows.append(
            {
                **common,
                "review_rate": sum(item.final_action == "REVIEW" for item in values)
                / len(values),
            }
        )
        unsafe_rows.append(
            {
                **common,
                "unsafe_direction_transition_count": sum(
                    item.unsafe_direction_transition for item in values
                ),
            }
        )
    _write_csv(
        review_path,
        (
            "configuration_id",
            "retrieval_strategy",
            "alpha",
            "top_k",
            "scenario_count",
            "review_rate",
        ),
        review_rows,
    )
    _write_csv(
        unsafe_path,
        (
            "configuration_id",
            "retrieval_strategy",
            "alpha",
            "top_k",
            "scenario_count",
            "unsafe_direction_transition_count",
        ),
        unsafe_rows,
    )
    return result_path, review_path, unsafe_path


def _cache_run_record(value: object) -> dict[str, object]:
    record = asdict(value)
    return record


def write_cache_experiment(
    result: CacheExperimentResult,
    output_directory: Path,
    *,
    repository_root: Path,
) -> tuple[Path, Path]:
    if not isinstance(result, CacheExperimentResult):
        raise TypeError("result must be CacheExperimentResult")
    output = require_engineering_output(
        output_directory, repository_root=repository_root
    )
    json_path = output / "cache_experiment.json"
    _write_json(
        json_path,
        {
            "schema_version": "1.0",
            "case_id": result.case_id,
            "semantic_model_id": result.semantic_model_id,
            "prompt_version": result.prompt_version,
            "detector_version": result.detector_version,
            "cold_semantic_miss": _cache_run_record(result.cold_miss),
            "exact_input_semantic_hit": _cache_run_record(result.exact_hit),
            "mutation_checks": [
                asdict(item) for item in result.mutation_checks
            ],
            "total_semantic_provider_calls": result.total_semantic_provider_calls,
            "razorpay_calls": result.razorpay_calls,
        },
    )
    csv_path = output / "cache_miss_vs_hit_latency_cost.csv"
    rows = []
    for run in (result.cold_miss, result.exact_hit):
        rows.append(
            {
                "cache_status": run.cache_status,
                "semantic_provider_calls": run.semantic_provider_calls,
                "semantic_latency_ms": run.semantic_latency_ms,
                "authorization_latency_ms": run.authorization_latency_ms,
                "total_latency_ms": run.total_latency_ms,
                "semantic_input_tokens": run.token_usage.semantic_input_tokens,
                "semantic_output_tokens": run.token_usage.semantic_output_tokens,
                "estimated_api_cost": run.cost.estimated_api_cost,
            }
        )
    _write_csv(
        csv_path,
        (
            "cache_status",
            "semantic_provider_calls",
            "semantic_latency_ms",
            "authorization_latency_ms",
            "total_latency_ms",
            "semantic_input_tokens",
            "semantic_output_tokens",
            "estimated_api_cost",
        ),
        rows,
    )
    return json_path, csv_path
