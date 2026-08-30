"""Exact-input semantic MISS/HIT experiments with no execution-provider path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter_ns
from typing import Callable

from mandateguard.engineering.int2.downstream import (
    DownstreamAuthorizationCase,
    _selected_semantic_evidence,
)
from mandateguard.engineering.int2.models import (
    CostEstimate,
    CostRates,
    Int2ExperimentError,
    TokenUsage,
    estimate_api_cost,
)
from mandateguard.semantic.cache import InMemorySemanticCache, SemanticCache
from mandateguard.semantic.models import SemanticRequest
from mandateguard.semantic.verifier import (
    SEMANTIC_DETECTOR_VERSION,
    SEMANTIC_PROMPT_VERSION,
    SemanticMode,
    SemanticModel,
    SemanticVerifier,
)


@dataclass(frozen=True, slots=True)
class CacheRunObservation:
    cache_status: str
    semantic_provider_calls: int
    semantic_latency_ms: float
    authorization_latency_ms: float
    total_latency_ms: float
    token_usage: TokenUsage
    cost: CostEstimate
    semantic_verdict: str
    final_action: str


@dataclass(frozen=True, slots=True)
class CacheMutationCheck:
    input_name: str
    cache_status: str
    semantic_provider_calls: int


@dataclass(frozen=True, slots=True)
class CacheExperimentResult:
    case_id: str
    semantic_model_id: str
    prompt_version: str
    detector_version: str
    cold_miss: CacheRunObservation
    exact_hit: CacheRunObservation
    mutation_checks: tuple[CacheMutationCheck, ...]
    total_semantic_provider_calls: int
    razorpay_calls: int = 0


class _ObservedSemanticModel:
    __slots__ = (
        "delegate",
        "model_id",
        "clock_ns",
        "call_count",
        "last_latency_ms",
        "last_input_tokens",
        "last_output_tokens",
    )

    def __init__(
        self,
        delegate: SemanticModel,
        clock_ns: Callable[[], int],
    ) -> None:
        if not isinstance(delegate, SemanticModel):
            raise TypeError("delegate must implement SemanticModel")
        self.delegate = delegate
        self.model_id = delegate.model_id
        self.clock_ns = clock_ns
        self.call_count = 0
        self.last_latency_ms = 0.0
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    def evaluate(self, request: SemanticRequest) -> object:
        started = self.clock_ns()
        self.call_count += 1
        try:
            return self.delegate.evaluate(request)
        finally:
            self.last_latency_ms = max(
                0.0, (self.clock_ns() - started) / 1_000_000.0
            )
            self.last_input_tokens = getattr(
                self.delegate, "last_input_tokens", None
            )
            self.last_output_tokens = getattr(
                self.delegate, "last_output_tokens", None
            )


def _different_digest(value: str) -> str:
    prefix = "0" if value[0] != "0" else "1"
    return prefix + value[1:]


@dataclass(frozen=True, slots=True)
class CacheExperimentHarness:
    semantic_model: SemanticModel
    cache: SemanticCache
    cost_rates: CostRates | None = None
    prompt_version: str = SEMANTIC_PROMPT_VERSION
    detector_version: str = SEMANTIC_DETECTOR_VERSION
    clock_ns: Callable[[], int] = perf_counter_ns

    def __init__(
        self,
        semantic_model: SemanticModel,
        *,
        cache: SemanticCache | None = None,
        cost_rates: CostRates | None = None,
        prompt_version: str = SEMANTIC_PROMPT_VERSION,
        detector_version: str = SEMANTIC_DETECTOR_VERSION,
        clock_ns: Callable[[], int] = perf_counter_ns,
    ) -> None:
        object.__setattr__(self, "semantic_model", semantic_model)
        object.__setattr__(self, "cache", cache or InMemorySemanticCache())
        object.__setattr__(self, "cost_rates", cost_rates)
        object.__setattr__(self, "prompt_version", prompt_version)
        object.__setattr__(self, "detector_version", detector_version)
        object.__setattr__(self, "clock_ns", clock_ns)
        if not isinstance(self.semantic_model, SemanticModel):
            raise TypeError("semantic_model must implement SemanticModel")
        if not isinstance(self.cache, SemanticCache):
            raise TypeError("cache must implement SemanticCache")

    def run(
        self,
        case: DownstreamAuthorizationCase,
        *,
        evidence_ids: tuple[str, ...],
    ) -> CacheExperimentResult:
        if not isinstance(case, DownstreamAuthorizationCase):
            raise TypeError("case must be DownstreamAuthorizationCase")
        evidence = _selected_semantic_evidence(case, evidence_ids)
        observed_model = _ObservedSemanticModel(
            self.semantic_model, self.clock_ns
        )
        verifier = SemanticVerifier(
            model=observed_model,
            cache=self.cache,
            prompt_version=self.prompt_version,
            detector_version=self.detector_version,
        )
        scenario = case.scenario
        request = verifier.make_request(
            mandate=scenario.mandate,
            transaction=scenario.transaction,
            catalog_snapshot=scenario.catalog_snapshot,
            semantic_evidence=evidence,
        )
        if self.cache.get(request) is not None:
            raise Int2ExperimentError(
                "cache experiment requires a cold exact-input cache"
            )

        # Reuse the existing verifier and authorization controller.  There is no
        # import of the execution package in this experiment module.
        from mandateguard.semantic.orchestration import authorize_transaction

        def run_once(expected_status: str) -> CacheRunObservation:
            calls_before = observed_model.call_count
            total_started = self.clock_ns()
            authorization_started = self.clock_ns()
            authorization = authorize_transaction(
                mandate=scenario.mandate,
                transaction=scenario.transaction,
                catalog_snapshot=scenario.catalog_snapshot,
                server_time=scenario.server_time,
                nonce_state=scenario.nonce_state,
                committed_hashes=scenario.psp_committed_hashes,
                replay_seed=scenario.replay_seed,
                evaluated_at=scenario.evaluated_at,
                semantic_evidence=evidence,
                semantic_verifier=verifier,
                semantic_mode=SemanticMode.LIVE,
            )
            authorization_finished = self.clock_ns()
            total_finished = self.clock_ns()
            calls = observed_model.call_count - calls_before
            expected_calls = 1 if expected_status == "MISS" else 0
            if calls != expected_calls:
                raise Int2ExperimentError(
                    f"expected semantic cache {expected_status}, observed {calls} calls"
                )
            if authorization.semantic_decision is None:
                raise Int2ExperimentError(
                    "cache case did not reach semantic verification"
                )
            usage = TokenUsage(
                semantic_input_tokens=(
                    observed_model.last_input_tokens if calls else 0
                ),
                semantic_output_tokens=(
                    observed_model.last_output_tokens if calls else 0
                ),
            )
            return CacheRunObservation(
                cache_status=expected_status,
                semantic_provider_calls=calls,
                semantic_latency_ms=(observed_model.last_latency_ms if calls else 0.0),
                authorization_latency_ms=max(
                    0.0,
                    (authorization_finished - authorization_started)
                    / 1_000_000.0,
                ),
                total_latency_ms=max(
                    0.0, (total_finished - total_started) / 1_000_000.0
                ),
                token_usage=usage,
                cost=estimate_api_cost(usage, self.cost_rates),
                semantic_verdict=authorization.semantic_decision.verdict.value,
                final_action=authorization.final_action.value,
            )

        cold = run_once("MISS")
        hit = run_once("HIT")
        mutations = (
            ("evidence", replace(
                request,
                semantic_evidence_sha256=_different_digest(
                    request.semantic_evidence_sha256
                ),
            )),
            ("mandate", replace(
                request,
                mandate_payload_sha256=_different_digest(
                    request.mandate_payload_sha256
                ),
            )),
            ("transaction", replace(
                request,
                transaction_body_sha256=_different_digest(
                    request.transaction_body_sha256
                ),
            )),
            ("model", replace(request, model_id=f"{request.model_id}.mutation")),
            ("prompt", replace(request, prompt_version="int2-mutation")),
        )
        checks: list[CacheMutationCheck] = []
        for input_name, mutated in mutations:
            if self.cache.get(mutated) is not None:
                raise Int2ExperimentError(
                    f"{input_name} mutation unexpectedly reused a cache record"
                )
            checks.append(
                CacheMutationCheck(
                    input_name=input_name,
                    cache_status="MISS",
                    semantic_provider_calls=0,
                )
            )
        return CacheExperimentResult(
            case_id=case.query_id,
            semantic_model_id=verifier.model_id,
            prompt_version=verifier.prompt_version,
            detector_version=verifier.detector_version,
            cold_miss=cold,
            exact_hit=hit,
            mutation_checks=tuple(checks),
            total_semantic_provider_calls=observed_model.call_count,
        )
