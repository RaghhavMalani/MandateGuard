from __future__ import annotations

import json
from hashlib import sha256

import pytest

from mandateguard.audit.event import (
    EVENT_SCHEMA_VERSION,
    DecisionEvent,
    DecisionEventValidationError,
    canonical_event_body_bytes,
    canonical_event_bytes,
)
from mandateguard.audit.hash_chain import event_body_sha256
from mandateguard.core.hashing import mandate_payload_sha256
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.decision import DecisionAction
from mandateguard.replay.runner import run_scenario
from mandateguard.replay.scenario import ReplayScenario
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_mandate,
    make_transaction,
)


def _event() -> DecisionEvent:
    mandate = make_mandate()
    transaction = make_transaction()
    catalog = make_catalog()
    return run_scenario(
        ReplayScenario(
            mandate=mandate,
            transaction=transaction,
            catalog_snapshot=catalog,
            server_time=SERVER_TIME,
            nonce_state=NonceLedgerState(),
            psp_committed_hashes=make_commitments(transaction, catalog),
            replay_seed=271828,
            evaluated_at=SERVER_TIME,
        )
    )


def test_decision_event_contains_only_the_canonical_schema_fields() -> None:
    event = _event()

    assert set(event.record_data()) == {
        "schema_version",
        "sequence",
        "replay_seed",
        "evaluated_at",
        "server_time",
        "mandate_payload_sha256",
        "transaction_body_sha256",
        "catalog_snapshot_sha256",
        "committed_transaction_sha256",
        "committed_catalog_snapshot_sha256",
        "nonce_state_sha256",
        "tier_a_results",
        "tier_b_findings",
        "action",
        "previous_event_sha256",
        "event_sha256",
    }
    assert event.schema_version == EVENT_SCHEMA_VERSION == "1.1"
    assert event.mandate_payload_sha256 == mandate_payload_sha256(make_mandate())
    assert event.action is DecisionAction.ALLOW


def test_event_hash_is_sha256_of_canonical_body_excluding_itself() -> None:
    event = _event()
    body = canonical_event_body_bytes(event)

    assert b'"event_sha256":' not in body
    assert event.event_sha256 == sha256(body).hexdigest()
    assert event.event_sha256 == event_body_sha256(event)


def test_canonical_event_bytes_are_compact_utf8_without_newline() -> None:
    encoded = canonical_event_bytes(_event())

    assert encoded.decode("utf-8").startswith("{")
    assert b"\n" not in encoded
    assert b": " not in encoded
    assert b", " not in encoded


def test_event_round_trips_through_its_strict_mapping_decoder() -> None:
    event = _event()
    decoded = json.loads(canonical_event_bytes(event))

    restored = DecisionEvent.from_mapping(decoded)

    assert restored == event
    assert canonical_event_bytes(restored) == canonical_event_bytes(event)


def test_event_decoder_rejects_unknown_and_missing_fields() -> None:
    decoded = json.loads(canonical_event_bytes(_event()))
    decoded["runtime_hostname"] = "must-not-be-recorded"

    with pytest.raises(DecisionEventValidationError):
        DecisionEvent.from_mapping(decoded)

    del decoded["runtime_hostname"]
    del decoded["replay_seed"]
    with pytest.raises(DecisionEventValidationError):
        DecisionEvent.from_mapping(decoded)


def test_event_decoder_rejects_action_inconsistent_with_results() -> None:
    decoded = json.loads(canonical_event_bytes(_event()))
    decoded["action"] = "BLOCK"

    with pytest.raises(DecisionEventValidationError):
        DecisionEvent.from_mapping(decoded)
