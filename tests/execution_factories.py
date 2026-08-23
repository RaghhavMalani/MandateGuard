"""Synthetic D6 test values; no real credentials, wall clocks, or randomness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from mandateguard.core.hashing import CommittedHashes, transaction_body_sha256
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.execution import (
    HMACSHA256Signer,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
    issue_execution_authorization,
)
from mandateguard.execution.models import RazorpayOrderRequest, RazorpayOrderResult
from mandateguard.models.decision import DecisionAction
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.models import AuthorizationResult
from mandateguard.semantic.orchestration import authorize_transaction
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_mandate,
    make_transaction,
)


SYNTHETIC_SIGNING_KEY = bytes(range(32))
SIGNING_KEY_ID = "execution-test-key-v1"
DECISION_NONCE = "decision_nonce_123456789"
CAPABILITY_EXPIRES_AT = SERVER_TIME + timedelta(minutes=2)
CONFIG = TrustedExecutionConfig(
    merchant_id="merchant-1",
    account_scope="synthetic-test-account-scope",
)


def make_authorization(
    action: DecisionAction = DecisionAction.ALLOW,
    *,
    transaction=None,
    mandate=None,
) -> tuple[AuthorizationResult, ReplayScenario]:
    actual_transaction = transaction or make_transaction()
    if action is DecisionAction.BLOCK:
        actual_mandate = mandate or make_mandate(max_total_minor=5_000)
        catalog = make_catalog()
        commitments = make_commitments(actual_transaction, catalog)
    elif action is DecisionAction.REVIEW:
        actual_mandate = mandate or make_mandate()
        catalog = None
        commitments = CommittedHashes(
            transaction_sha256=transaction_body_sha256(actual_transaction),
            catalog_snapshot_sha256=None,
        )
    else:
        actual_mandate = mandate or make_mandate()
        catalog = make_catalog()
        commitments = make_commitments(actual_transaction, catalog)
    scenario = ReplayScenario(
        mandate=actual_mandate,
        transaction=actual_transaction,
        catalog_snapshot=catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=commitments,
        replay_seed=601,
        evaluated_at=SERVER_TIME,
    )
    result = authorize_transaction(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.psp_committed_hashes,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
    )
    assert result.final_action is action
    return result, scenario


def make_signed_allow(
    *,
    authorization_result: AuthorizationResult | None = None,
    authorization_scenario: ReplayScenario | None = None,
    signer: HMACSHA256Signer | None = None,
    decision_nonce: str = DECISION_NONCE,
) -> tuple[SignedExecutionAuthorization, AuthorizationResult, object, object]:
    if authorization_result is None:
        result, scenario = make_authorization()
    else:
        result = authorization_result
        scenario = authorization_scenario
    assert isinstance(scenario, ReplayScenario)
    capability = issue_execution_authorization(
        authorization_result=result,
        authorization_scenario=scenario,
        semantic_evidence=None,
        semantic_verifier=None,
        issued_at=SERVER_TIME,
        expires_at=CAPABILITY_EXPIRES_AT,
        decision_nonce=decision_nonce,
        config=CONFIG,
        signer=signer
        or HMACSHA256Signer(key_id=SIGNING_KEY_ID, key=SYNTHETIC_SIGNING_KEY),
    )
    assert isinstance(capability, SignedExecutionAuthorization)
    return capability, result, scenario.mandate, scenario.transaction


@dataclass
class RecordingClient:
    exception: Exception | None = None
    calls: list[RazorpayOrderRequest] = field(default_factory=list)

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        self.calls.append(request)
        if self.exception is not None:
            raise self.exception
        return RazorpayOrderResult(
            razorpay_order_id="order_synthetic_result",
            amount=request.amount,
            currency=request.currency,
            receipt=request.receipt,
            status="created",
        )
