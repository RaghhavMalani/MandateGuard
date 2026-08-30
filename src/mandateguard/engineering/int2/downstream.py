"""Explicit Stage-B reuse of the existing MandateGuard authorization controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import perf_counter_ns
from typing import Callable

from mandateguard.engineering.semantic_fixtures import EngineeringExpectation
from mandateguard.engineering.int2.models import (
    DownstreamSelection,
    Int2ExperimentError,
    RetrievalConfiguration,
    RetrievalObservation,
)
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    semantic_evidence_sha256,
)
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier


class AuthorizationTransition(str, Enum):
    NONE = "NONE"
    EXPECTED_VIOLATION_TO_PASS = "EXPECTED_VIOLATION_TO_PASS"
    EXPECTED_PASS_TO_VIOLATION = "EXPECTED_PASS_TO_VIOLATION"
    EXPECTED_TO_REVIEW = "EXPECTED_TO_REVIEW"


@dataclass(frozen=True, slots=True)
class DownstreamAuthorizationCase:
    query_id: str
    engineering_expectation: EngineeringExpectation
    scenario: ReplayScenario
    eligible_evidence: tuple[SemanticEvidenceEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.query_id, str) or not self.query_id:
            raise Int2ExperimentError("query_id must be non-empty")
        if not isinstance(self.engineering_expectation, EngineeringExpectation):
            raise Int2ExperimentError("engineering_expectation is invalid")
        if not isinstance(self.scenario, ReplayScenario):
            raise Int2ExperimentError("scenario must be ReplayScenario")
        if not isinstance(self.eligible_evidence, tuple) or not self.eligible_evidence:
            raise Int2ExperimentError("eligible_evidence must be non-empty")
        if not all(
            isinstance(item, SemanticEvidenceEntry)
            for item in self.eligible_evidence
        ):
            raise Int2ExperimentError("eligible_evidence contains an invalid entry")
        merchant_id = self.scenario.transaction.payload.merchant_id
        if any(item.merchant_id != merchant_id for item in self.eligible_evidence):
            raise Int2ExperimentError(
                "eligible evidence must belong to the transaction merchant"
            )
        ids = [item.evidence_id for item in self.eligible_evidence]
        if len(ids) != len(set(ids)):
            raise Int2ExperimentError("eligible evidence IDs must be unique")


@dataclass(frozen=True, slots=True)
class DownstreamAuthorizationObservation:
    query_id: str
    configuration: RetrievalConfiguration
    engineering_expectation: EngineeringExpectation
    semantic_verdict: str
    final_action: str
    retrieved_evidence_ids: tuple[str, ...]
    transition: AuthorizationTransition
    authorization_latency_ms: float

    @property
    def unsafe_direction_transition(self) -> bool:
        return (
            self.transition
            is AuthorizationTransition.EXPECTED_VIOLATION_TO_PASS
        )


def _selected_semantic_evidence(
    case: DownstreamAuthorizationCase,
    retrieved_evidence_ids: tuple[str, ...],
) -> SemanticEvidence:
    by_id = {item.evidence_id: item for item in case.eligible_evidence}
    selected: list[SemanticEvidenceEntry] = []
    seen: set[str] = set()
    for evidence_id in retrieved_evidence_ids:
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        try:
            selected.append(by_id[evidence_id])
        except KeyError as error:
            raise Int2ExperimentError(
                f"retrieved evidence {evidence_id!r} is outside the case scope"
            ) from error
    if not selected:
        raise Int2ExperimentError(
            "Stage B requires at least one retrieved evidence item; "
            "no_retrieval remains a Stage-A condition"
        )
    bundle = SemanticEvidenceBundle(
        merchant_id=case.scenario.transaction.payload.merchant_id,
        entries=tuple(selected),
    )
    return SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )


def _transition(
    expectation: EngineeringExpectation,
    semantic_verdict: str,
    final_action: str,
) -> AuthorizationTransition:
    if final_action == "REVIEW":
        return AuthorizationTransition.EXPECTED_TO_REVIEW
    if expectation is EngineeringExpectation.VIOLATION and semantic_verdict == "PASS":
        return AuthorizationTransition.EXPECTED_VIOLATION_TO_PASS
    if expectation is EngineeringExpectation.PASS and semantic_verdict == "VIOLATION":
        return AuthorizationTransition.EXPECTED_PASS_TO_VIOLATION
    return AuthorizationTransition.NONE


def execute_selected_downstream(
    cases: tuple[DownstreamAuthorizationCase, ...],
    retrieval_observations: tuple[RetrievalObservation, ...],
    selection: DownstreamSelection,
    *,
    semantic_verifier: SemanticVerifier,
    allow_semantic_execution: bool = False,
    clock_ns: Callable[[], int] = perf_counter_ns,
) -> tuple[DownstreamAuthorizationObservation, ...]:
    """Execute only pre-recorded configurations through the existing controller.

    ``allow_semantic_execution`` is intentionally false by default.  The function
    has no payment-execution dependency and cannot invoke Razorpay.
    """

    if not allow_semantic_execution:
        raise Int2ExperimentError(
            "Stage-B semantic execution requires explicit opt-in"
        )
    if not isinstance(selection, DownstreamSelection):
        raise TypeError("selection must be DownstreamSelection")
    if not isinstance(semantic_verifier, SemanticVerifier):
        raise TypeError("semantic_verifier must be SemanticVerifier")
    cases_by_id = {item.query_id: item for item in cases}
    if len(cases_by_id) != len(cases):
        raise Int2ExperimentError("downstream case query IDs must be unique")
    observations_by_key = {
        (item.query_id, item.retrieval.configuration): item
        for item in retrieval_observations
    }

    # This is the existing frozen MandateGuard controller, not an experiment
    # classifier.  Keeping the import local also keeps Stage A semantic-free.
    from mandateguard.semantic.orchestration import authorize_transaction

    results: list[DownstreamAuthorizationObservation] = []
    for selected in selection.selections:
        try:
            case = cases_by_id[selected.query_id]
            retrieval = observations_by_key[
                (selected.query_id, selected.configuration)
            ]
        except KeyError as error:
            raise Int2ExperimentError(
                "Stage-B selection does not match supplied Stage-A data"
            ) from error
        evidence_ids = retrieval.retrieval.retrieved_evidence_ids
        semantic_evidence = _selected_semantic_evidence(case, evidence_ids)
        scenario = case.scenario
        started = clock_ns()
        authorization = authorize_transaction(
            mandate=scenario.mandate,
            transaction=scenario.transaction,
            catalog_snapshot=scenario.catalog_snapshot,
            server_time=scenario.server_time,
            nonce_state=scenario.nonce_state,
            committed_hashes=scenario.psp_committed_hashes,
            replay_seed=scenario.replay_seed,
            evaluated_at=scenario.evaluated_at,
            semantic_evidence=semantic_evidence,
            semantic_verifier=semantic_verifier,
            semantic_mode=SemanticMode.LIVE,
        )
        latency = max(0.0, (clock_ns() - started) / 1_000_000.0)
        if authorization.semantic_decision is None:
            raise Int2ExperimentError(
                "selected downstream case did not reach semantic verification"
            )
        verdict = authorization.semantic_decision.verdict.value
        final_action = authorization.final_action.value
        results.append(
            DownstreamAuthorizationObservation(
                query_id=case.query_id,
                configuration=selected.configuration,
                engineering_expectation=case.engineering_expectation,
                semantic_verdict=verdict,
                final_action=final_action,
                retrieved_evidence_ids=evidence_ids,
                transition=_transition(
                    case.engineering_expectation, verdict, final_action
                ),
                authorization_latency_ms=latency,
            )
        )
    return tuple(results)
