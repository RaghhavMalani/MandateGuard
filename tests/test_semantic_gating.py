from __future__ import annotations

from dataclasses import replace

import pytest

from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.decision import DecisionAction
from mandateguard.semantic.cache import InMemorySemanticCache
from mandateguard.semantic.models import ConstraintStatus, SemanticVerdict
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier
from tests.factories import SERVER_TIME, make_catalog, make_commitments, make_mandate, make_transaction
from tests.semantic_factories import (
    ScriptedSemanticModel,
    make_semantic_evidence,
    make_semantic_mandate,
    model_output,
    valid_authorization_inputs,
)


def _verifier(response: object) -> tuple[SemanticVerifier, ScriptedSemanticModel]:
    model = ScriptedSemanticModel(response=response)
    return SemanticVerifier(model=model, cache=InMemorySemanticCache()), model


def test_deterministic_block_with_semantics_makes_zero_model_calls() -> None:
    mandate = make_semantic_mandate()
    transaction = make_transaction()
    catalog = make_catalog()
    verifier, model = _verifier(model_output("PASS", "PASS"))

    result = authorize_transaction(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=mandate.payload.expires_at,
        nonce_state=NonceLedgerState(),
        committed_hashes=make_commitments(transaction, catalog),
        replay_seed=1,
        evaluated_at=SERVER_TIME,
        semantic_verifier=verifier,
    )

    assert result.deterministic_decision.action is DecisionAction.BLOCK
    assert result.final_action is DecisionAction.BLOCK
    assert result.semantic_decision is None
    assert len(model.calls) == 0


def test_deterministic_review_with_semantics_makes_zero_model_calls() -> None:
    verifier, model = _verifier(model_output("PASS", "PASS"))
    inputs = valid_authorization_inputs()
    inputs["nonce_state"] = None

    result = authorize_transaction(**inputs, semantic_verifier=verifier)

    assert result.deterministic_decision.action is DecisionAction.REVIEW
    assert result.final_action is DecisionAction.REVIEW
    assert result.semantic_decision is None
    assert len(model.calls) == 0


def test_deterministic_allow_without_semantics_makes_zero_model_calls() -> None:
    verifier, model = _verifier(model_output("PASS", "PASS"))
    inputs = valid_authorization_inputs(mandate=make_mandate())

    result = authorize_transaction(**inputs, semantic_verifier=verifier)

    assert result.deterministic_decision.action is DecisionAction.ALLOW
    assert result.final_action is DecisionAction.ALLOW
    assert result.semantic_decision is None
    assert len(model.calls) == 0


@pytest.mark.parametrize(
    ("statuses", "verdict", "action"),
    [
        pytest.param(("PASS", "PASS"), SemanticVerdict.PASS, DecisionAction.ALLOW, id="all-pass"),
        pytest.param(("VIOLATION", "PASS"), SemanticVerdict.VIOLATION, DecisionAction.BLOCK, id="violation-pass"),
        pytest.param(("ABSTAIN", "PASS"), SemanticVerdict.ABSTAIN, DecisionAction.REVIEW, id="abstain-pass"),
        pytest.param(("PASS", "VIOLATION"), SemanticVerdict.VIOLATION, DecisionAction.BLOCK, id="pass-violation"),
        pytest.param(("PASS", "ABSTAIN"), SemanticVerdict.ABSTAIN, DecisionAction.REVIEW, id="pass-abstain"),
        pytest.param(("VIOLATION", "ABSTAIN"), SemanticVerdict.VIOLATION, DecisionAction.BLOCK, id="violation-wins"),
    ],
)
def test_semantic_reducer_is_local_and_deterministic(
    statuses: tuple[str, str], verdict: SemanticVerdict, action: DecisionAction
) -> None:
    verifier, model = _verifier(model_output(*statuses))

    result = authorize_transaction(
        **valid_authorization_inputs(),
        semantic_evidence=make_semantic_evidence(),
        semantic_verifier=verifier,
    )

    assert result.deterministic_decision.action is DecisionAction.ALLOW
    assert result.semantic_decision.verdict is verdict
    assert result.final_action is action
    assert len(model.calls) == 1


def test_semantic_cache_hit_skips_an_additional_model_call() -> None:
    verifier, model = _verifier(model_output("PASS", "PASS"))
    kwargs = {
        **valid_authorization_inputs(),
        "semantic_evidence": make_semantic_evidence(),
        "semantic_verifier": verifier,
    }

    first = authorize_transaction(**kwargs)
    second = authorize_transaction(**kwargs)
    replayed = authorize_transaction(**kwargs, semantic_mode=SemanticMode.REPLAY)

    assert first == second == replayed
    assert len(model.calls) == 1
    assert tuple(
        result.status for result in replayed.semantic_decision.constraint_results
    ) == (ConstraintStatus.PASS, ConstraintStatus.PASS)
