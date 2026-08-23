from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import datetime

import pytest

from mandateguard.audit.event import (
    DecisionEvent,
    canonical_event_bytes,
)
from mandateguard.core.hashing import (
    CommittedHashes,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogItem
from mandateguard.models.decision import DecisionAction
from mandateguard.models.finding import Finding
from mandateguard.models.mandate import Mandate
from mandateguard.models import decision as decision_module
from mandateguard.policy import tier_a as tier_a_module
from mandateguard.policy import tier_b as tier_b_module
from mandateguard.replay import runner as replay_runner_module
from mandateguard.replay import scenario as replay_scenario_module
from mandateguard.replay.runner import replay_scenario, run_scenario
from mandateguard.replay.scenario import ReplayScenario
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_line,
    make_mandate,
    make_payload,
    make_transaction,
)


def _scenario(
    *,
    mandate: Mandate | None = None,
    transaction=None,
    catalog: object = Ellipsis,
    server_time: datetime | None = SERVER_TIME,
    nonce_state: NonceLedgerState | None = NonceLedgerState(),
    commitments: CommittedHashes | None | object = Ellipsis,
    replay_seed: int = 7,
) -> ReplayScenario:
    actual_mandate = mandate if mandate is not None else make_mandate()
    actual_transaction = transaction if transaction is not None else make_transaction()
    actual_catalog = make_catalog() if catalog is Ellipsis else catalog
    actual_commitments = (
        make_commitments(actual_transaction, actual_catalog)
        if commitments is Ellipsis
        else commitments
    )
    return ReplayScenario(
        mandate=actual_mandate,
        transaction=actual_transaction,
        catalog_snapshot=actual_catalog,
        server_time=server_time,
        nonce_state=nonce_state,
        psp_committed_hashes=actual_commitments,
        replay_seed=replay_seed,
        evaluated_at=SERVER_TIME,
    )


def _allow() -> ReplayScenario:
    return _scenario(replay_seed=1)


def _catalog_amount_violation() -> ReplayScenario:
    transaction = make_transaction(
        payload=make_payload(
            lines=(make_line(effective_unit_price_minor=449_900),),
            declared_order_total_minor=449_900,
        )
    )
    catalog = make_catalog(price_minor=549_900)
    return _scenario(
        mandate=make_mandate(max_total_minor=500_000),
        transaction=transaction,
        catalog=catalog,
        commitments=make_commitments(transaction, catalog),
        replay_seed=2,
    )


def _missing_independent_evidence() -> ReplayScenario:
    transaction = make_transaction()
    return _scenario(
        transaction=transaction,
        catalog=None,
        commitments=CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=None,
        ),
        replay_seed=3,
    )


def _a_fail_and_a_not_evaluable() -> ReplayScenario:
    mandate = make_mandate()
    return _scenario(
        mandate=mandate,
        catalog=None,
        server_time=mandate.payload.expires_at,
        nonce_state=None,
        commitments=None,
        replay_seed=4,
    )


def _b_fail_and_a_not_evaluable() -> ReplayScenario:
    return _scenario(
        mandate=make_mandate(max_total_minor=5_000),
        catalog=None,
        commitments=None,
        replay_seed=5,
    )


def _replayed_nonce() -> ReplayScenario:
    mandate = make_mandate()
    return _scenario(
        mandate=mandate,
        nonce_state=NonceLedgerState(frozenset({mandate.payload.nonce})),
        replay_seed=6,
    )


def _expired_exactly_at_boundary() -> ReplayScenario:
    mandate = make_mandate()
    return _scenario(
        mandate=mandate,
        server_time=mandate.payload.expires_at,
        replay_seed=7,
    )


def _catalog_commitment_mismatch() -> ReplayScenario:
    transaction = make_transaction()
    catalog = make_catalog()
    return _scenario(
        transaction=transaction,
        catalog=catalog,
        commitments=CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256="0" * 64,
        ),
        replay_seed=8,
    )


@pytest.mark.parametrize(
    ("scenario_factory", "expected_action"),
    [
        pytest.param(_allow, DecisionAction.ALLOW, id="A-valid-allow"),
        pytest.param(
            _catalog_amount_violation,
            DecisionAction.BLOCK,
            id="B-catalog-amount-violation",
        ),
        pytest.param(
            _missing_independent_evidence,
            DecisionAction.REVIEW,
            id="C-independent-evidence-absent",
        ),
        pytest.param(
            _a_fail_and_a_not_evaluable,
            DecisionAction.BLOCK,
            id="D-a-fail-plus-a-not-evaluable",
        ),
        pytest.param(
            _b_fail_and_a_not_evaluable,
            DecisionAction.BLOCK,
            id="E-b-fail-plus-a-not-evaluable",
        ),
        pytest.param(_replayed_nonce, DecisionAction.BLOCK, id="F-replayed-nonce"),
        pytest.param(
            _expired_exactly_at_boundary,
            DecisionAction.BLOCK,
            id="G-expires-at-boundary",
        ),
        pytest.param(
            _catalog_commitment_mismatch,
            DecisionAction.BLOCK,
            id="H-catalog-commitment-mismatch",
        ),
    ],
)
def test_golden_scenarios_replay_to_byte_identical_events(
    scenario_factory, expected_action: DecisionAction
) -> None:
    first = run_scenario(scenario_factory())
    second = replay_scenario(scenario_factory())

    assert first.action is expected_action
    assert second.action is expected_action
    assert canonical_event_bytes(first) == canonical_event_bytes(second)
    assert first.event_sha256 == second.event_sha256


def test_event_mapping_insertion_order_does_not_change_canonical_bytes() -> None:
    event = run_scenario(_allow())
    decoded = json.loads(canonical_event_bytes(event))
    reversed_mapping = dict(reversed(tuple(decoded.items())))

    reconstructed = DecisionEvent.from_mapping(reversed_mapping)

    assert canonical_event_bytes(reconstructed) == canonical_event_bytes(event)


def test_finding_detail_construction_order_is_canonical() -> None:
    event = run_scenario(_catalog_amount_violation())
    original = event.tier_a_results[0].finding
    assert original is not None
    reversed_details = dict(reversed(original.details))
    reconstructed = Finding.create(original.family, original.message, reversed_details)
    reconstructed_result = replace(event.tier_a_results[0], finding=reconstructed)
    reconstructed_event = DecisionEvent.create(
        sequence=event.sequence,
        replay_seed=event.replay_seed,
        evaluated_at=event.evaluated_at,
        mandate_payload_sha256=event.mandate_payload_sha256,
        transaction_body_sha256=event.transaction_body_sha256,
        catalog_snapshot_sha256=event.catalog_snapshot_sha256,
        tier_a_results=(reconstructed_result, *event.tier_a_results[1:]),
        tier_b_findings=event.tier_b_findings,
        action=event.action,
        previous_event_sha256=event.previous_event_sha256,
    )

    assert reconstructed == original
    assert canonical_event_bytes(reconstructed_event) == canonical_event_bytes(event)
    assert reconstructed_event.event_sha256 == event.event_sha256


def test_catalog_item_input_order_does_not_change_replay_event() -> None:
    sku_1 = CatalogItem(
        sku="sku-1",
        merchant_id="merchant-1",
        effective_unit_price_minor=10_000,
        recurring=False,
    )
    sku_2 = replace(sku_1, sku="sku-2")
    first_catalog = make_catalog(items=(sku_1, sku_2))
    second_catalog = make_catalog(items=(sku_2, sku_1))
    transaction = make_transaction()
    first = run_scenario(
        _scenario(
            transaction=transaction,
            catalog=first_catalog,
            commitments=make_commitments(transaction, first_catalog),
        )
    )
    second = run_scenario(
        _scenario(
            transaction=transaction,
            catalog=second_catalog,
            commitments=make_commitments(transaction, second_catalog),
        )
    )

    assert canonical_event_bytes(first) == canonical_event_bytes(second)


def test_semantically_ordered_transaction_lines_are_not_reordered() -> None:
    first_line = make_line(sku="sku-1")
    second_line = make_line(sku="sku-2")
    first = make_transaction(payload=make_payload(lines=(first_line, second_line)))
    second = make_transaction(payload=make_payload(lines=(second_line, first_line)))

    assert transaction_body_sha256(first) != transaction_body_sha256(second)


@pytest.mark.parametrize(
    "forbidden",
    [
        "datetime.now",
        "time.time",
        "random.",
        "uuid.",
        "os.urandom",
        "secrets.",
    ],
)
def test_deterministic_path_contains_no_runtime_entropy_or_clock_reads(
    forbidden: str,
) -> None:
    deterministic_modules = (
        tier_a_module,
        tier_b_module,
        decision_module,
        replay_scenario_module,
        replay_runner_module,
    )
    source = "\n".join(inspect.getsource(module) for module in deterministic_modules)

    assert forbidden not in source
