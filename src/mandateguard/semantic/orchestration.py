"""Full Tier A/B gate followed by optional constrained Tier C verification."""

from __future__ import annotations

from datetime import datetime

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.decision import (
    DecisionAction,
    DeterministicDecision,
    decide_deterministically,
)
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction
from mandateguard.policy.tier_a import evaluate_tier_a
from mandateguard.policy.tier_b import evaluate_tier_b
from mandateguard.semantic.evidence import SemanticEvidence
from mandateguard.semantic.models import (
    AuthorizationResult,
    action_for_semantic_verdict,
)
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier


def finalize_authorization(
    *,
    deterministic_decision: DeterministicDecision,
    mandate: Mandate,
    transaction: Transaction,
    catalog_snapshot: CatalogSnapshot | None,
    semantic_evidence: SemanticEvidence | None,
    semantic_verifier: SemanticVerifier | None,
    semantic_mode: SemanticMode = SemanticMode.LIVE,
) -> AuthorizationResult:
    """Apply the non-overridable deterministic gate and local semantic reducer."""

    if not isinstance(deterministic_decision, DeterministicDecision):
        raise TypeError("deterministic_decision must be DeterministicDecision")
    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")
    if catalog_snapshot is not None and not isinstance(catalog_snapshot, CatalogSnapshot):
        raise TypeError("catalog_snapshot must be CatalogSnapshot or None")
    if not isinstance(semantic_mode, SemanticMode):
        raise TypeError("semantic_mode must be SemanticMode")

    semantic_constraints_present = bool(mandate.payload.constraints.semantic)
    if deterministic_decision.action is DecisionAction.BLOCK:
        return AuthorizationResult(
            deterministic_decision=deterministic_decision,
            semantic_decision=None,
            final_action=DecisionAction.BLOCK,
            semantic_constraints_present=semantic_constraints_present,
        )
    if deterministic_decision.action is DecisionAction.REVIEW:
        return AuthorizationResult(
            deterministic_decision=deterministic_decision,
            semantic_decision=None,
            final_action=DecisionAction.REVIEW,
            semantic_constraints_present=semantic_constraints_present,
        )
    if not semantic_constraints_present:
        return AuthorizationResult(
            deterministic_decision=deterministic_decision,
            semantic_decision=None,
            final_action=DecisionAction.ALLOW,
            semantic_constraints_present=False,
        )

    if catalog_snapshot is None:
        raise ValueError("Tier C requires the exact deterministically evaluated catalog")
    if not isinstance(semantic_evidence, SemanticEvidence):
        raise TypeError("Tier C requires acquired SemanticEvidence")
    if not isinstance(semantic_verifier, SemanticVerifier):
        raise TypeError("Tier C requires a configured SemanticVerifier")
    if deterministic_decision.transaction_sha256 != transaction_body_sha256(transaction):
        raise ValueError("transaction does not match the deterministic decision")
    if deterministic_decision.catalog_snapshot_sha256 != catalog_snapshot_sha256(
        catalog_snapshot
    ):
        raise ValueError("catalog snapshot does not match the deterministic decision")

    request = semantic_verifier.make_request(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog_snapshot,
        semantic_evidence=semantic_evidence,
    )
    semantic_decision = semantic_verifier.evaluate(request, mode=semantic_mode)
    return AuthorizationResult(
        deterministic_decision=deterministic_decision,
        semantic_decision=semantic_decision,
        final_action=action_for_semantic_verdict(semantic_decision.verdict),
        semantic_constraints_present=True,
    )


def authorize_transaction(
    *,
    mandate: Mandate,
    transaction: Transaction,
    catalog_snapshot: CatalogSnapshot | None,
    server_time: datetime | None,
    nonce_state: NonceLedgerState | None,
    committed_hashes: CommittedHashes | None,
    replay_seed: int,
    evaluated_at: datetime,
    semantic_evidence: SemanticEvidence | None = None,
    semantic_verifier: SemanticVerifier | None = None,
    semantic_mode: SemanticMode = SemanticMode.LIVE,
) -> AuthorizationResult:
    """Evaluate frozen Tier A/B policy, then invoke Tier C only after ALLOW."""

    tier_a_results = evaluate_tier_a(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog_snapshot,
        server_time=server_time,
        nonce_state=nonce_state,
        committed_hashes=committed_hashes,
    )
    tier_b_findings = evaluate_tier_b(mandate=mandate, transaction=transaction)
    deterministic = decide_deterministically(
        replay_seed=replay_seed,
        evaluated_at=evaluated_at,
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=(
            catalog_snapshot_sha256(catalog_snapshot)
            if catalog_snapshot is not None
            else None
        ),
        tier_a_results=tier_a_results,
        tier_b_findings=tier_b_findings,
    )
    return finalize_authorization(
        deterministic_decision=deterministic,
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog_snapshot,
        semantic_evidence=semantic_evidence,
        semantic_verifier=semantic_verifier,
        semantic_mode=semantic_mode,
    )
