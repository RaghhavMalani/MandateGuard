from __future__ import annotations

from dataclasses import replace

from mandateguard.core.hashing import catalog_snapshot_sha256, transaction_body_sha256
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.execution import (
    ExecutionRefusal,
    ExecutionRefusalReason,
    HMACSHA256Signer,
    SQLiteExecutionLedger,
    SignedExecutionAuthorization,
    issue_execution_authorization,
)
from mandateguard.models.decision import DecisionAction, DeterministicDecision
from mandateguard.models.finding import (
    TaxonomyFamily,
    TierACheckResult,
    TierACheckStatus,
)
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.cache import InMemorySemanticCache
from mandateguard.semantic.models import AuthorizationResult
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier
from tests.execution_factories import (
    CAPABILITY_EXPIRES_AT,
    CONFIG,
    DECISION_NONCE,
    SIGNING_KEY_ID,
    SYNTHETIC_SIGNING_KEY,
    RecordingClient,
)
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_mandate,
    make_transaction,
)
from tests.semantic_factories import (
    ScriptedSemanticModel,
    make_constraints,
    make_semantic_evidence,
    make_semantic_mandate,
    model_output,
)


def _scenario(*, mandate=None, transaction=None) -> ReplayScenario:
    actual_mandate = mandate or make_mandate()
    actual_transaction = transaction or make_transaction()
    catalog = make_catalog()
    return ReplayScenario(
        mandate=actual_mandate,
        transaction=actual_transaction,
        catalog_snapshot=catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=make_commitments(actual_transaction, catalog),
        replay_seed=701,
        evaluated_at=SERVER_TIME,
    )


def _authorize(
    scenario: ReplayScenario,
    *,
    semantic_evidence=None,
    semantic_verifier=None,
    semantic_mode=SemanticMode.LIVE,
) -> AuthorizationResult:
    return authorize_transaction(
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
        semantic_mode=semantic_mode,
    )


def _issue(
    result: AuthorizationResult,
    scenario: ReplayScenario,
    *,
    semantic_evidence=None,
    semantic_verifier=None,
):
    return issue_execution_authorization(
        authorization_result=result,
        authorization_scenario=scenario,
        semantic_evidence=semantic_evidence,
        semantic_verifier=semantic_verifier,
        issued_at=SERVER_TIME,
        expires_at=CAPABILITY_EXPIRES_AT,
        decision_nonce=DECISION_NONCE,
        config=CONFIG,
        signer=HMACSHA256Signer(
            key_id=SIGNING_KEY_ID, key=SYNTHETIC_SIGNING_KEY
        ),
    )


def test_allow_result_from_mandate_a_cannot_issue_for_restrictive_mandate_b(
    tmp_path,
) -> None:
    transaction = make_transaction()
    scenario_a = _scenario(
        mandate=make_mandate(max_total_minor=20_000), transaction=transaction
    )
    result_a = _authorize(scenario_a)
    scenario_b = _scenario(
        mandate=make_mandate(max_total_minor=5_000), transaction=transaction
    )

    assert result_a.final_action is DecisionAction.ALLOW
    assert _authorize(scenario_b).final_action is DecisionAction.BLOCK

    outcome = _issue(result_a, scenario_b)
    ledger = SQLiteExecutionLedger(tmp_path / "context-mismatch.sqlite3")
    client = RecordingClient()

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_CONTEXT_MISMATCH
    )
    assert not isinstance(outcome, SignedExecutionAuthorization)
    assert ledger.get(DECISION_NONCE) is None
    assert client.calls == []


def test_fabricated_structurally_valid_allow_result_is_rejected() -> None:
    scenario = _scenario(mandate=make_mandate(max_total_minor=5_000))
    assert scenario.catalog_snapshot is not None
    fabricated_decision = DeterministicDecision(
        action=DecisionAction.ALLOW,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        transaction_sha256=transaction_body_sha256(scenario.transaction),
        catalog_snapshot_sha256=catalog_snapshot_sha256(
            scenario.catalog_snapshot
        ),
        tier_a_results=tuple(
            TierACheckResult(
                family=TaxonomyFamily(f"A{index}"),
                status=TierACheckStatus.PASS,
            )
            for index in range(1, 9)
        ),
        findings=(),
    )
    fabricated = AuthorizationResult(
        deterministic_decision=fabricated_decision,
        semantic_decision=None,
        final_action=DecisionAction.ALLOW,
        semantic_constraints_present=False,
    )

    assert _authorize(scenario).final_action is DecisionAction.BLOCK

    outcome = _issue(fabricated, scenario)

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_CONTEXT_MISMATCH
    )
    assert not isinstance(outcome, SignedExecutionAuthorization)


def test_allow_result_cannot_cross_transaction_contexts() -> None:
    transaction_a = make_transaction()
    scenario_a = _scenario(transaction=transaction_a)
    result_a = _authorize(scenario_a)
    transaction_b = make_transaction(
        payload=replace(transaction_a.payload, transaction_id="transaction-2")
    )
    scenario_b = _scenario(transaction=transaction_b)

    assert result_a.final_action is DecisionAction.ALLOW
    assert _authorize(scenario_b).final_action is DecisionAction.ALLOW

    outcome = _issue(result_a, scenario_b)

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_CONTEXT_MISMATCH
    )


def test_exact_nonsemantic_historical_context_rederives_without_model_call() -> None:
    scenario = _scenario()
    result = _authorize(scenario)
    model = ScriptedSemanticModel(response=model_output("PASS", "PASS"))
    verifier = SemanticVerifier(model=model, cache=InMemorySemanticCache())

    outcome = _issue(result, scenario, semantic_verifier=verifier)

    assert isinstance(outcome, SignedExecutionAuthorization)
    assert model.calls == []


def _live_semantic_result(
    scenario: ReplayScenario,
    *,
    evidence,
    statuses: tuple[str, str],
):
    model = ScriptedSemanticModel(response=model_output(*statuses))
    verifier = SemanticVerifier(model=model, cache=InMemorySemanticCache())
    result = _authorize(
        scenario,
        semantic_evidence=evidence,
        semantic_verifier=verifier,
    )
    assert len(model.calls) == 1
    model.calls.clear()
    return result, verifier, model


def test_exact_semantic_historical_context_issues_with_zero_replay_model_calls() -> None:
    scenario = _scenario(mandate=make_semantic_mandate())
    evidence = make_semantic_evidence()
    result, verifier, model = _live_semantic_result(
        scenario, evidence=evidence, statuses=("PASS", "PASS")
    )

    outcome = _issue(
        result,
        scenario,
        semantic_evidence=evidence,
        semantic_verifier=verifier,
    )

    assert isinstance(outcome, SignedExecutionAuthorization)
    assert model.calls == []


def test_semantic_result_cannot_cross_contexts_and_replay_calls_no_model() -> None:
    evidence = make_semantic_evidence()
    scenario_a = _scenario(mandate=make_semantic_mandate())
    result_a, _verifier_a, _model_a = _live_semantic_result(
        scenario_a, evidence=evidence, statuses=("PASS", "PASS")
    )
    changed_constraints = (
        replace(make_constraints()[0], text="The purchase must be for team training."),
        make_constraints()[1],
    )
    scenario_b = _scenario(
        mandate=make_semantic_mandate(constraints=changed_constraints)
    )
    result_b, verifier_b, model_b = _live_semantic_result(
        scenario_b, evidence=evidence, statuses=("VIOLATION", "PASS")
    )

    assert result_a.final_action is DecisionAction.ALLOW
    assert result_b.final_action is DecisionAction.BLOCK

    outcome = _issue(
        result_a,
        scenario_b,
        semantic_evidence=evidence,
        semantic_verifier=verifier_b,
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_CONTEXT_MISMATCH
    )
    assert model_b.calls == []


def test_missing_semantic_replay_record_refuses_without_model_call() -> None:
    scenario = _scenario(mandate=make_semantic_mandate())
    evidence = make_semantic_evidence()
    result, _historical_verifier, _historical_model = _live_semantic_result(
        scenario, evidence=evidence, statuses=("PASS", "PASS")
    )
    replay_model = ScriptedSemanticModel(response=model_output("PASS", "PASS"))
    empty_replay = SemanticVerifier(
        model=replay_model, cache=InMemorySemanticCache()
    )

    outcome = _issue(
        result,
        scenario,
        semantic_evidence=evidence,
        semantic_verifier=empty_replay,
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_CONTEXT_UNVERIFIABLE
    )
    assert replay_model.calls == []


def test_invalid_semantic_replay_record_refuses_without_model_call() -> None:
    scenario = _scenario(mandate=make_semantic_mandate())
    evidence = make_semantic_evidence()
    result, historical_verifier, _historical_model = _live_semantic_result(
        scenario, evidence=evidence, statuses=("PASS", "PASS")
    )
    cache_key = next(iter(historical_verifier.cache.records))
    invalid_cache = InMemorySemanticCache({cache_key: object()})
    replay_model = ScriptedSemanticModel(response=model_output("PASS", "PASS"))
    invalid_replay = SemanticVerifier(model=replay_model, cache=invalid_cache)

    outcome = _issue(
        result,
        scenario,
        semantic_evidence=evidence,
        semantic_verifier=invalid_replay,
    )

    assert outcome == ExecutionRefusal(
        ExecutionRefusalReason.AUTHORIZATION_CONTEXT_UNVERIFIABLE
    )
    assert replay_model.calls == []
