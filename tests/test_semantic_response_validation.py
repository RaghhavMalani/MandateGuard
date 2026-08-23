from __future__ import annotations

import pytest

from mandateguard.models.decision import DecisionAction
from mandateguard.semantic.cache import InMemorySemanticCache
from mandateguard.semantic.models import ConstraintStatus
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import (
    MODEL_INCOMPLETE,
    MODEL_OUTPUT_INVALID,
    MODEL_REFUSAL,
    MODEL_UNAVAILABLE,
    SemanticMode,
    SemanticModelResponse,
    SemanticVerifier,
)
from tests.semantic_factories import (
    ScriptedSemanticModel,
    make_semantic_evidence,
    model_output,
    valid_authorization_inputs,
)


VALID_RESULTS = model_output("PASS", "PASS")["constraint_results"]


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"constraint_results": VALID_RESULTS[:1]}, id="missing-result"),
        pytest.param(
            {"constraint_results": [VALID_RESULTS[0], VALID_RESULTS[0]]},
            id="duplicate-id",
        ),
        pytest.param(
            {
                "constraint_results": [
                    VALID_RESULTS[0],
                    {
                        "constraint_id": "unknown-1",
                        "status": "PASS",
                        "reason": "not requested",
                    },
                ]
            },
            id="unknown-id",
        ),
        pytest.param(
            {
                "constraint_results": [
                    VALID_RESULTS[0],
                    {**VALID_RESULTS[1], "status": "MAYBE"},
                ]
            },
            id="invalid-status",
        ),
        pytest.param({}, id="empty-object"),
        pytest.param([], id="wrong-provider-type"),
        pytest.param(
            {
                "constraint_results": [
                    VALID_RESULTS[0],
                    {**VALID_RESULTS[1], "confidence": 1},
                ]
            },
            id="extra-result-field",
        ),
        pytest.param(
            {"constraint_results": VALID_RESULTS, "action": "ALLOW"},
            id="global-action-field",
        ),
    ],
)
def test_malformed_model_outputs_normalize_to_review_and_are_replayable(
    response: object,
) -> None:
    model = ScriptedSemanticModel(response=response)
    verifier = SemanticVerifier(model=model, cache=InMemorySemanticCache())
    kwargs = {
        **valid_authorization_inputs(),
        "semantic_evidence": make_semantic_evidence(),
        "semantic_verifier": verifier,
    }

    live = authorize_transaction(**kwargs)
    replay = authorize_transaction(**kwargs, semantic_mode=SemanticMode.REPLAY)

    assert live.final_action is DecisionAction.REVIEW
    assert replay == live
    assert len(model.calls) == 1
    assert {
        result.status for result in live.semantic_decision.constraint_results
    } == {ConstraintStatus.ABSTAIN}
    assert {
        result.reason for result in live.semantic_decision.constraint_results
    } == {MODEL_OUTPUT_INVALID}


@pytest.mark.parametrize(
    ("response", "exception", "reason"),
    [
        pytest.param(None, RuntimeError("offline"), MODEL_UNAVAILABLE, id="provider-exception"),
        pytest.param(
            SemanticModelResponse(payload=None, refused=True),
            None,
            MODEL_REFUSAL,
            id="refusal",
        ),
        pytest.param(
            SemanticModelResponse(payload=None, incomplete=True),
            None,
            MODEL_INCOMPLETE,
            id="incomplete",
        ),
    ],
)
def test_provider_failures_are_bounded_abstentions_not_findings(
    response: object, exception: Exception | None, reason: str
) -> None:
    model = ScriptedSemanticModel(response=response, exception=exception)
    verifier = SemanticVerifier(model=model, cache=InMemorySemanticCache())

    result = authorize_transaction(
        **valid_authorization_inputs(),
        semantic_evidence=make_semantic_evidence(),
        semantic_verifier=verifier,
    )

    assert result.final_action is DecisionAction.REVIEW
    assert not result.deterministic_decision.findings
    assert {
        item.reason for item in result.semantic_decision.constraint_results
    } == {reason}
    assert len(model.calls) == 1
