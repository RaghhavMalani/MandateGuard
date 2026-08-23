from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.audit.event import canonical_event_bytes
from mandateguard.audit.journal import (
    DecisionJournal,
    JournalError,
    JournalValidationError,
)
from mandateguard.core.canonical import canonical_json_bytes
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


def _scenario(*, max_total_minor: int = 500_000) -> ReplayScenario:
    mandate = make_mandate(max_total_minor=max_total_minor)
    transaction = make_transaction()
    catalog = make_catalog()
    return ReplayScenario(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=make_commitments(transaction, catalog),
        replay_seed=101,
        evaluated_at=SERVER_TIME,
    )


def _write_three_event_journal(path: Path) -> DecisionJournal:
    journal = DecisionJournal(path)
    first = run_scenario(_scenario(), sequence=1, journal=journal)
    second = run_scenario(
        _scenario(max_total_minor=5_000),
        sequence=2,
        previous_event_sha256=first.event_sha256,
        journal=journal,
    )
    run_scenario(
        _scenario(),
        sequence=3,
        previous_event_sha256=second.event_sha256,
        journal=journal,
    )
    return journal


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _rewrite_canonical_records(path: Path, records: list[dict]) -> None:
    path.write_bytes(b"\n".join(canonical_json_bytes(record) for record in records) + b"\n")


def test_journal_appends_one_canonical_utf8_event_per_line(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = DecisionJournal(path)
    event = run_scenario(_scenario(), journal=journal)

    assert path.read_bytes() == canonical_event_bytes(event) + b"\n"
    assert journal.read_all() == (event,)


def test_journal_append_rejects_wrong_sequence_or_previous_hash(tmp_path: Path) -> None:
    journal = DecisionJournal(tmp_path / "decisions.jsonl")
    first = run_scenario(_scenario(), journal=journal)

    wrong_sequence = run_scenario(
        _scenario(), sequence=3, previous_event_sha256=first.event_sha256
    )
    with pytest.raises(JournalValidationError):
        journal.append(wrong_sequence)

    wrong_previous = run_scenario(
        _scenario(), sequence=2, previous_event_sha256="0" * 64
    )
    with pytest.raises(JournalValidationError):
        journal.append(wrong_previous)


def test_tamper_modify_action_after_write_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    records = _records(path)
    records[0]["action"] = "BLOCK"
    _rewrite_canonical_records(path, records)

    with pytest.raises(JournalError):
        journal.read_all()


def test_tamper_modify_one_finding_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    records = _records(path)
    records[1]["tier_b_findings"][0]["message"] += " tampered"
    _rewrite_canonical_records(path, records)

    with pytest.raises(JournalError):
        journal.read_all()


def test_tamper_modify_transaction_hash_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    records = _records(path)
    records[0]["transaction_body_sha256"] = "0" * 64
    _rewrite_canonical_records(path, records)

    with pytest.raises(JournalError):
        journal.read_all()


def test_tamper_remove_intermediate_event_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join((lines[0], lines[2])) + b"\n")

    with pytest.raises(JournalError):
        journal.read_all()


def test_tamper_swap_two_events_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join((lines[1], lines[0], lines[2])) + b"\n")

    with pytest.raises(JournalError):
        journal.read_all()


def test_tamper_corrupt_one_jsonl_byte_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    raw = bytearray(path.read_bytes())
    raw[10] = 0xFF
    path.write_bytes(raw)

    with pytest.raises(JournalError):
        journal.read_all()


def test_tamper_truncate_final_jsonl_record_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    journal = _write_three_event_journal(path)
    path.write_bytes(path.read_bytes()[:-7])

    with pytest.raises(JournalError):
        journal.read_all()
