from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect
import json

import pytest

from mandateguard.engineering.int2.artifacts import (
    write_cache_experiment,
    write_downstream_results,
    write_downstream_selection,
)
from mandateguard.engineering.int2.cache import CacheExperimentHarness
from mandateguard.engineering.int2.downstream import (
    AuthorizationTransition,
    DownstreamAuthorizationCase,
    execute_selected_downstream,
)
from mandateguard.engineering.int2.models import (
    CostRates,
    DownstreamSelection,
    Int2ExperimentError,
    RelevanceAnnotation,
    RetrievalConfiguration,
    RetrievalStrategy,
    SelectedRetrievalConfiguration,
    TokenUsage,
    estimate_api_cost,
)
from mandateguard.engineering.int2.models import ExperimentQuery
from mandateguard.engineering.int2.retrieval import (
    ExperimentRetriever,
    compute_retrieval_metrics,
)
from mandateguard.engineering.int2.models import RetrievalObservation
from mandateguard.engineering.semantic_fixtures import EngineeringExpectation
from mandateguard.intelligence.models import RetrievalDocument, RetrievalSource
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.cache import InMemorySemanticCache
from mandateguard.semantic.verifier import SemanticVerifier
from tests.semantic_factories import (
    ScriptedSemanticModel,
    make_semantic_evidence,
    model_output,
    valid_authorization_inputs,
)


def _case(expectation=EngineeringExpectation.PASS):
    inputs = valid_authorization_inputs()
    semantic_evidence = make_semantic_evidence()
    return DownstreamAuthorizationCase(
        query_id="INT2-Q-DOWNSTREAM",
        engineering_expectation=expectation,
        scenario=ReplayScenario(
            mandate=inputs["mandate"],
            transaction=inputs["transaction"],
            catalog_snapshot=inputs["catalog_snapshot"],
            server_time=inputs["server_time"],
            nonce_state=inputs["nonce_state"],
            psp_committed_hashes=inputs["committed_hashes"],
            replay_seed=inputs["replay_seed"],
            evaluated_at=inputs["evaluated_at"],
        ),
        eligible_evidence=semantic_evidence.bundle.entries,
    )


def _retrieval(case, *, configuration=None):
    configuration = configuration or RetrievalConfiguration(
        strategy=RetrievalStrategy.LEXICAL_ONLY, top_k=2
    )
    documents = tuple(
        RetrievalDocument(
            document_id=f"evidence.{entry.evidence_id}",
            source_type=RetrievalSource.MERCHANT_EVIDENCE,
            text=entry.text,
            merchant_id=entry.merchant_id,
            sku=entry.sku,
            evidence_id=entry.evidence_id,
        )
        for entry in case.eligible_evidence
        if entry.sku in {None, case.scenario.transaction.payload.lines[0].sku}
    )
    query = ExperimentQuery.from_text(
        query_id=case.query_id,
        query="one-time individual study",
        documents=documents,
    )
    result = ExperimentRetriever(None).retrieve(query, configuration)
    annotation = RelevanceAnnotation(
        query_id=case.query_id,
        relevant_evidence_ids=result.retrieved_evidence_ids,
        required_evidence_ids=result.retrieved_evidence_ids,
    )
    return RetrievalObservation(
        query_id=case.query_id,
        retrieval=result,
        metrics=compute_retrieval_metrics(
            result.retrieved_evidence_ids,
            annotation,
            top_k=configuration.top_k,
        ),
    )


def _selection(observation):
    return DownstreamSelection(
        selection_id="int2-selection-1",
        recorded_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        selections=(
            SelectedRetrievalConfiguration(
                query_id=observation.query_id,
                configuration=observation.retrieval.configuration,
            ),
        ),
        rationale="Explicitly selected after reviewing Stage-A records.",
    )


def test_downstream_mode_requires_explicit_opt_in():
    case = _case()
    observation = _retrieval(case)
    verifier = SemanticVerifier(
        model=ScriptedSemanticModel(model_output("PASS", "PASS")),
        cache=InMemorySemanticCache(),
    )
    with pytest.raises(Int2ExperimentError, match="explicit opt-in"):
        execute_selected_downstream(
            (case,),
            (observation,),
            _selection(observation),
            semantic_verifier=verifier,
        )


@pytest.mark.parametrize(
    ("expectation", "statuses", "transition"),
    [
        (
            EngineeringExpectation.VIOLATION,
            ("PASS", "PASS"),
            AuthorizationTransition.EXPECTED_VIOLATION_TO_PASS,
        ),
        (
            EngineeringExpectation.PASS,
            ("VIOLATION", "PASS"),
            AuthorizationTransition.EXPECTED_PASS_TO_VIOLATION,
        ),
        (
            EngineeringExpectation.PASS,
            ("ABSTAIN", "PASS"),
            AuthorizationTransition.EXPECTED_TO_REVIEW,
        ),
    ],
)
def test_downstream_reuses_existing_controller_and_records_engineering_transitions(
    monkeypatch, expectation, statuses, transition
):
    import mandateguard.semantic.orchestration as orchestration

    case = _case(expectation)
    observation = _retrieval(case)
    model = ScriptedSemanticModel(model_output(*statuses))
    verifier = SemanticVerifier(model=model, cache=InMemorySemanticCache())
    original = orchestration.authorize_transaction
    calls = []

    def recording_controller(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(
        orchestration, "authorize_transaction", recording_controller
    )
    results = execute_selected_downstream(
        (case,),
        (observation,),
        _selection(observation),
        semantic_verifier=verifier,
        allow_semantic_execution=True,
    )
    assert len(calls) == len(results) == len(model.calls) == 1
    assert results[0].transition is transition
    assert results[0].retrieved_evidence_ids == observation.retrieval.retrieved_evidence_ids
    assert {
        item.evidence_id for item in model.calls[0].selected_evidence
    }.issubset(results[0].retrieved_evidence_ids)


def test_no_retrieval_cannot_be_sent_to_semantic_provider():
    case = _case()
    observation = _retrieval(
        case,
        configuration=RetrievalConfiguration(
            strategy=RetrievalStrategy.NO_RETRIEVAL, top_k=1
        ),
    )
    verifier = SemanticVerifier(
        model=ScriptedSemanticModel(model_output("PASS", "PASS")),
        cache=InMemorySemanticCache(),
    )
    with pytest.raises(Int2ExperimentError, match="Stage B requires"):
        execute_selected_downstream(
            (case,),
            (observation,),
            _selection(observation),
            semantic_verifier=verifier,
            allow_semantic_execution=True,
        )


def test_cache_experiment_has_one_miss_call_zero_hit_calls_and_all_mutation_misses():
    case = _case()
    model = ScriptedSemanticModel(model_output("PASS", "PASS"))
    model.last_input_tokens = 20
    model.last_output_tokens = 5
    result = CacheExperimentHarness(
        model,
        cost_rates=CostRates(
            semantic_input_cost_per_token=0.01,
            semantic_output_cost_per_token=0.02,
        ),
    ).run(
        case,
        evidence_ids=tuple(item.evidence_id for item in case.eligible_evidence),
    )
    assert result.cold_miss.cache_status == "MISS"
    assert result.cold_miss.semantic_provider_calls == 1
    assert result.cold_miss.cost.estimated_api_cost == pytest.approx(0.3)
    assert result.exact_hit.cache_status == "HIT"
    assert result.exact_hit.semantic_provider_calls == 0
    assert result.exact_hit.semantic_latency_ms == 0.0
    assert result.exact_hit.cost.estimated_api_cost == 0.0
    assert result.total_semantic_provider_calls == len(model.calls) == 1
    assert result.razorpay_calls == 0
    assert {item.input_name for item in result.mutation_checks} == {
        "evidence",
        "mandate",
        "transaction",
        "model",
        "prompt",
    }
    assert all(item.cache_status == "MISS" for item in result.mutation_checks)
    assert all(
        item.semantic_provider_calls == 0 for item in result.mutation_checks
    )


def test_cache_experiment_source_has_no_razorpay_execution_path():
    from mandateguard.engineering.int2 import cache

    source = inspect.getsource(cache)
    assert "execute_razorpay_order" not in source
    assert "mandateguard.execution" not in source


def test_cost_model_keeps_raw_counts_and_identifies_unpriced_usage():
    usage = TokenUsage(
        buyer_input_tokens=10,
        embedding_tokens=20,
        semantic_input_tokens=30,
    )
    estimate = estimate_api_cost(
        usage, CostRates(embedding_cost_per_token=0.001)
    )
    assert usage.embedding_tokens == 20
    assert estimate.estimated_api_cost == pytest.approx(0.02)
    assert estimate.priced_categories == ("embedding",)
    assert estimate.unpriced_categories == ("buyer_input", "semantic_input")


def test_selection_downstream_and_cache_artifacts_are_machine_readable(tmp_path):
    case = _case(EngineeringExpectation.VIOLATION)
    observation = _retrieval(case)
    selection = _selection(observation)
    selection_path = write_downstream_selection(
        selection,
        tmp_path / "artifacts" / "engineering" / "int2" / "selection.json",
        repository_root=tmp_path,
    )
    verifier = SemanticVerifier(
        model=ScriptedSemanticModel(model_output("PASS", "PASS")),
        cache=InMemorySemanticCache(),
    )
    downstream = execute_selected_downstream(
        (case,),
        (observation,),
        selection,
        semantic_verifier=verifier,
        allow_semantic_execution=True,
    )
    downstream_paths = write_downstream_results(
        downstream,
        tmp_path / "artifacts" / "engineering" / "int2",
        selection=selection,
        repository_root=tmp_path,
    )
    cache_result = CacheExperimentHarness(
        ScriptedSemanticModel(model_output("PASS", "PASS"))
    ).run(
        replace(case, query_id="INT2-Q-CACHE"),
        evidence_ids=tuple(item.evidence_id for item in case.eligible_evidence),
    )
    cache_paths = write_cache_experiment(
        cache_result,
        tmp_path / "artifacts" / "engineering" / "int2",
        repository_root=tmp_path,
    )
    assert json.loads(selection_path.read_text(encoding="utf-8"))["selection_id"]
    assert len(downstream_paths) == 3
    decoded_cache = json.loads(cache_paths[0].read_text(encoding="utf-8"))
    assert decoded_cache["razorpay_calls"] == 0
