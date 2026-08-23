from __future__ import annotations

from dataclasses import replace

import pytest

from mandateguard.audit.hash_chain import (
    HashChainError,
    verify_event_hash,
    verify_hash_chain,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.replay.runner import run_scenario
from mandateguard.replay.scenario import ReplayScenario
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_mandate,
    make_transaction,
)


def _chain() -> tuple:
    mandate = make_mandate()
    transaction = make_transaction()
    catalog = make_catalog()
    scenario = ReplayScenario(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=make_commitments(transaction, catalog),
        replay_seed=42,
        evaluated_at=SERVER_TIME,
    )
    first = run_scenario(scenario, sequence=1)
    second = run_scenario(
        scenario,
        sequence=2,
        previous_event_sha256=first.event_sha256,
    )
    third = run_scenario(
        scenario,
        sequence=3,
        previous_event_sha256=second.event_sha256,
    )
    return first, second, third


def test_valid_hash_chain_is_accepted() -> None:
    verify_hash_chain(_chain())


def test_altered_historical_event_is_rejected() -> None:
    first, second, third = _chain()
    altered_first = replace(first, replay_seed=first.replay_seed + 1)

    with pytest.raises(HashChainError):
        verify_hash_chain((altered_first, second, third))


def test_removed_intermediate_event_is_rejected() -> None:
    first, _, third = _chain()

    with pytest.raises(HashChainError):
        verify_hash_chain((first, third))


def test_reordered_events_are_rejected() -> None:
    first, second, third = _chain()

    with pytest.raises(HashChainError):
        verify_hash_chain((second, first, third))


def test_incorrect_previous_hash_is_rejected() -> None:
    first, second, third = _chain()
    incorrect_link = replace(second, previous_event_sha256="0" * 64)

    with pytest.raises(HashChainError):
        verify_hash_chain((first, incorrect_link, third))


def test_incorrect_event_hash_is_rejected() -> None:
    first, second, third = _chain()
    incorrect_hash = replace(first, event_sha256="0" * 64)

    with pytest.raises(HashChainError):
        verify_hash_chain((incorrect_hash, second, third))


def test_tampered_committed_transaction_hash_fails_event_hash_verification() -> None:
    event, _, _ = _chain()
    tampered = replace(event, committed_transaction_sha256="0" * 64)

    with pytest.raises(HashChainError):
        verify_event_hash(tampered)


def test_tampered_committed_catalog_hash_fails_event_hash_verification() -> None:
    event, _, _ = _chain()
    tampered = replace(event, committed_catalog_snapshot_sha256="0" * 64)

    with pytest.raises(HashChainError):
        verify_event_hash(tampered)


def test_tampered_server_time_fails_event_hash_verification() -> None:
    event, _, _ = _chain()
    tampered = replace(event, server_time=None)

    with pytest.raises(HashChainError):
        verify_event_hash(tampered)


def test_tampered_nonce_state_hash_fails_event_hash_verification() -> None:
    event, _, _ = _chain()
    tampered = replace(event, nonce_state_sha256="0" * 64)

    with pytest.raises(HashChainError):
        verify_event_hash(tampered)
