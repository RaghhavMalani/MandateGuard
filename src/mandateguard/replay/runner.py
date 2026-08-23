"""Deterministic Tier A/B scenario evaluation and event construction."""

from __future__ import annotations

from mandateguard.audit.evidence import nonce_state_sha256
from mandateguard.audit.event import DecisionEvent
from mandateguard.audit.journal import DecisionJournal
from mandateguard.core.hashing import (
    catalog_snapshot_sha256,
    mandate_payload_sha256,
    transaction_body_sha256,
)
from mandateguard.models.decision import decide_deterministically
from mandateguard.policy.tier_a import evaluate_tier_a
from mandateguard.policy.tier_b import evaluate_tier_b
from mandateguard.replay.scenario import ReplayScenario


def run_scenario(
    scenario: ReplayScenario,
    *,
    sequence: int = 1,
    previous_event_sha256: str | None = None,
    journal: DecisionJournal | None = None,
) -> DecisionEvent:
    """Evaluate explicit inputs, create a canonical event, and optionally append it."""

    if not isinstance(scenario, ReplayScenario):
        raise TypeError("scenario must be a ReplayScenario")
    server_time = scenario.server_time
    nonce_state = scenario.nonce_state
    committed_hashes = scenario.psp_committed_hashes
    tier_a_results = evaluate_tier_a(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=server_time,
        nonce_state=nonce_state,
        committed_hashes=committed_hashes,
    )
    tier_b_findings = evaluate_tier_b(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
    )
    transaction_hash = transaction_body_sha256(scenario.transaction)
    catalog_hash = (
        catalog_snapshot_sha256(scenario.catalog_snapshot)
        if scenario.catalog_snapshot is not None
        else None
    )
    decision = decide_deterministically(
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        transaction_sha256=transaction_hash,
        catalog_snapshot_sha256=catalog_hash,
        tier_a_results=tier_a_results,
        tier_b_findings=tier_b_findings,
    )
    committed_transaction_hash = (
        committed_hashes.transaction_sha256 if committed_hashes is not None else None
    )
    committed_catalog_hash = (
        committed_hashes.catalog_snapshot_sha256 if committed_hashes is not None else None
    )
    nonce_hash = (
        nonce_state_sha256(nonce_state) if nonce_state is not None else None
    )
    event = DecisionEvent.create(
        sequence=sequence,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        server_time=server_time,
        mandate_payload_sha256=mandate_payload_sha256(scenario.mandate),
        transaction_body_sha256=transaction_hash,
        catalog_snapshot_sha256=catalog_hash,
        committed_transaction_sha256=committed_transaction_hash,
        committed_catalog_snapshot_sha256=committed_catalog_hash,
        nonce_state_sha256=nonce_hash,
        tier_a_results=tier_a_results,
        tier_b_findings=tier_b_findings,
        action=decision.action,
        previous_event_sha256=previous_event_sha256,
    )
    if journal is not None:
        journal.append(event)
    return event


def replay_scenario(
    recorded_scenario: ReplayScenario,
    *,
    sequence: int = 1,
    previous_event_sha256: str | None = None,
    journal: DecisionJournal | None = None,
) -> DecisionEvent:
    """Repeat a recorded scenario through the same deterministic evaluation path."""

    return run_scenario(
        recorded_scenario,
        sequence=sequence,
        previous_event_sha256=previous_event_sha256,
        journal=journal,
    )
