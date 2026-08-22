from __future__ import annotations

from datetime import timedelta

import pytest

from mandateguard.core.hashing import CommittedHashes
from mandateguard.core.nonce_ledger import NonceAlreadyConsumed, NonceLedgerState
from mandateguard.models.catalog import CatalogItem
from mandateguard.models.finding import TaxonomyFamily
from mandateguard.policy.tier_a import evaluate_tier_a
from tests.factories import (
    SERVER_TIME,
    make_catalog,
    make_commitments,
    make_line,
    make_mandate,
    make_payload,
    make_transaction,
)


def _families(findings: tuple) -> set[TaxonomyFamily]:
    return {finding.family for finding in findings}


def _evaluate(*, mandate=None, transaction=None, catalog=None, nonce_state=None, server_time=None, commitments=None):
    actual_mandate = mandate or make_mandate()
    actual_transaction = transaction or make_transaction()
    actual_catalog = catalog or make_catalog()
    return evaluate_tier_a(
        mandate=actual_mandate,
        transaction=actual_transaction,
        catalog_snapshot=actual_catalog,
        server_time=server_time or SERVER_TIME,
        nonce_state=nonce_state or NonceLedgerState(),
        committed_hashes=commitments or make_commitments(actual_transaction, actual_catalog),
    )


def test_tier_a_happy_path_passes_all_checks() -> None:
    assert _evaluate() == ()


def test_a1_detects_declared_price_divergence() -> None:
    transaction = make_transaction(payload=make_payload(lines=(make_line(unit_price_minor=9_000),)))

    assert TaxonomyFamily.A1 in _families(_evaluate(transaction=transaction))


def test_a2_detects_missing_and_wrongly_owned_skus() -> None:
    lines = (make_line(sku="missing"), make_line(sku="wrong-owner"))
    transaction = make_transaction(payload=make_payload(lines=lines))
    catalog = make_catalog(
        items=(
            CatalogItem(
                sku="wrong-owner",
                merchant_id="merchant-2",
                price_minor=100_00,
                recurring=False,
            ),
        )
    )

    assert TaxonomyFamily.A2 in _families(_evaluate(transaction=transaction, catalog=catalog))


def test_a3_detects_merchant_substitution() -> None:
    transaction = make_transaction(payload=make_payload(merchant_id="merchant-2"))

    assert TaxonomyFamily.A3 in _families(_evaluate(transaction=transaction))


def test_a4_detects_replay_and_v1_ledger_is_single_use() -> None:
    mandate = make_mandate()
    initial = NonceLedgerState()
    consumed = initial.consume(mandate.payload.nonce)

    assert not initial.is_consumed(mandate.payload.nonce)
    assert TaxonomyFamily.A4 in _families(_evaluate(mandate=mandate, nonce_state=consumed))
    with pytest.raises(NonceAlreadyConsumed):
        consumed.consume(mandate.payload.nonce)


def test_a5_uses_only_explicit_server_time() -> None:
    mandate = make_mandate()

    before_expiry = _evaluate(
        mandate=mandate,
        server_time=mandate.payload.expires_at - timedelta(microseconds=1),
    )
    at_expiry = _evaluate(mandate=mandate, server_time=mandate.payload.expires_at)

    assert TaxonomyFamily.A5 not in _families(before_expiry)
    assert TaxonomyFamily.A5 in _families(at_expiry)


def test_a6_detects_transaction_or_catalog_snapshot_mutation() -> None:
    transaction = make_transaction()
    catalog = make_catalog()
    commitments = CommittedHashes(
        transaction_sha256="0" * 64,
        catalog_snapshot_sha256="f" * 64,
    )

    assert TaxonomyFamily.A6 in _families(
        _evaluate(transaction=transaction, catalog=catalog, commitments=commitments)
    )


def test_a7_uses_catalog_price_times_execution_quantity() -> None:
    mandate = make_mandate(max_total_minor=500_000)
    transaction = make_transaction(
        payload=make_payload(lines=(make_line(unit_price_minor=200_000, quantity=3),))
    )
    catalog = make_catalog(price_minor=200_000)

    findings = _evaluate(mandate=mandate, transaction=transaction, catalog=catalog)

    assert TaxonomyFamily.A7 in _families(findings)
    a7 = next(finding for finding in findings if finding.family is TaxonomyFamily.A7)
    assert dict(a7.details)["catalog_total_minor"] == 600_000


def test_a8_uses_catalog_recurrence_not_agent_recurrence() -> None:
    mandate = make_mandate(recurring_allowed=False)
    transaction = make_transaction(payload=make_payload(lines=(make_line(recurring=False),)))
    catalog = make_catalog(recurring=True)

    findings = _evaluate(mandate=mandate, transaction=transaction, catalog=catalog)

    assert TaxonomyFamily.A8 in _families(findings)


def test_tier_a_is_replay_safe_for_identical_explicit_inputs() -> None:
    mandate = make_mandate()
    transaction = make_transaction()
    catalog = make_catalog()
    commitments = make_commitments(transaction, catalog)
    inputs = {
        "mandate": mandate,
        "transaction": transaction,
        "catalog_snapshot": catalog,
        "server_time": SERVER_TIME,
        "nonce_state": NonceLedgerState(),
        "committed_hashes": commitments,
    }

    assert evaluate_tier_a(**inputs) == evaluate_tier_a(**inputs)


def test_tier_a_rejects_naive_server_time() -> None:
    with pytest.raises(ValueError):
        _evaluate(server_time=SERVER_TIME.replace(tzinfo=None))
