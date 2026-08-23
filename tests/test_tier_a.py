from __future__ import annotations

from datetime import timedelta

import pytest

from mandateguard.core.hashing import (
    CommitmentState,
    CommittedHashes,
    compare_sha256_commitment,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceAlreadyConsumed, NonceLedgerState
from mandateguard.models.catalog import CatalogItem
from mandateguard.models.finding import TaxonomyFamily, TierACheckResult, TierACheckStatus
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


_UNSET = object()


def _result(
    results: tuple[TierACheckResult, ...], family: TaxonomyFamily
) -> TierACheckResult:
    return next(result for result in results if result.family is family)


def _evaluate(
    *,
    mandate=None,
    transaction=None,
    catalog=_UNSET,
    nonce_state=_UNSET,
    server_time=_UNSET,
    commitments=_UNSET,
):
    actual_mandate = mandate or make_mandate()
    actual_transaction = transaction or make_transaction()
    actual_catalog = make_catalog() if catalog is _UNSET else catalog
    actual_nonce_state = NonceLedgerState() if nonce_state is _UNSET else nonce_state
    actual_server_time = SERVER_TIME if server_time is _UNSET else server_time
    if commitments is _UNSET:
        actual_commitments = (
            make_commitments(actual_transaction, actual_catalog)
            if actual_catalog is not None
            else CommittedHashes(
                transaction_sha256=transaction_body_sha256(actual_transaction),
                catalog_snapshot_sha256=None,
            )
        )
    else:
        actual_commitments = commitments
    return evaluate_tier_a(
        mandate=actual_mandate,
        transaction=actual_transaction,
        catalog_snapshot=actual_catalog,
        server_time=actual_server_time,
        nonce_state=actual_nonce_state,
        committed_hashes=actual_commitments,
    )


def test_tier_a_happy_path_passes_all_checks() -> None:
    results = _evaluate()

    assert len(results) == 8
    assert all(result.status is TierACheckStatus.PASS for result in results)


def test_commitment_states_distinguish_absent_match_and_mismatch() -> None:
    digest = "a" * 64

    assert (
        compare_sha256_commitment(actual_sha256=None, committed_sha256=digest)
        is CommitmentState.ABSENT
    )
    assert (
        compare_sha256_commitment(actual_sha256=digest, committed_sha256=digest)
        is CommitmentState.MATCH
    )
    assert (
        compare_sha256_commitment(
            actual_sha256=digest,
            committed_sha256="b" * 64,
        )
        is CommitmentState.MISMATCH
    )


def test_a1_requires_exact_declared_catalog_price_equality() -> None:
    transaction = make_transaction(
        payload=make_payload(lines=(make_line(effective_unit_price_minor=9_999),))
    )
    catalog = make_catalog(price_minor=10_000)

    result = _result(
        _evaluate(transaction=transaction, catalog=catalog), TaxonomyFamily.A1
    )

    assert result.status is TierACheckStatus.FAIL


def test_a2_detects_missing_and_wrongly_owned_skus() -> None:
    lines = (make_line(sku="missing"), make_line(sku="wrong-owner"))
    transaction = make_transaction(payload=make_payload(lines=lines))
    catalog = make_catalog(
        items=(
            CatalogItem(
                sku="wrong-owner",
                merchant_id="merchant-2",
                effective_unit_price_minor=100_00,
                recurring=False,
            ),
        )
    )

    assert (
        _result(_evaluate(transaction=transaction, catalog=catalog), TaxonomyFamily.A2).status
        is TierACheckStatus.FAIL
    )


def test_a3_detects_merchant_substitution() -> None:
    transaction = make_transaction(payload=make_payload(merchant_id="merchant-2"))

    assert (
        _result(_evaluate(transaction=transaction), TaxonomyFamily.A3).status
        is TierACheckStatus.FAIL
    )


def test_a4_detects_replay_and_v1_ledger_is_single_use() -> None:
    mandate = make_mandate()
    initial = NonceLedgerState()
    consumed = initial.consume(mandate.payload.nonce)

    assert not initial.is_consumed(mandate.payload.nonce)
    assert (
        _result(
            _evaluate(mandate=mandate, nonce_state=consumed), TaxonomyFamily.A4
        ).status
        is TierACheckStatus.FAIL
    )
    with pytest.raises(NonceAlreadyConsumed):
        consumed.consume(mandate.payload.nonce)


def test_a5_uses_only_explicit_server_time() -> None:
    mandate = make_mandate()

    before_expiry = _evaluate(
        mandate=mandate,
        server_time=mandate.payload.expires_at - timedelta(microseconds=1),
    )
    at_expiry = _evaluate(mandate=mandate, server_time=mandate.payload.expires_at)

    assert _result(before_expiry, TaxonomyFamily.A5).status is TierACheckStatus.PASS
    assert _result(at_expiry, TaxonomyFamily.A5).status is TierACheckStatus.FAIL


def test_a6_detects_transaction_or_catalog_snapshot_mutation() -> None:
    transaction = make_transaction()
    catalog = make_catalog()
    commitments = CommittedHashes(
        transaction_sha256="0" * 64,
        catalog_snapshot_sha256="f" * 64,
    )

    result = _result(
        _evaluate(transaction=transaction, catalog=catalog, commitments=commitments),
        TaxonomyFamily.A6,
    )

    assert result.status is TierACheckStatus.FAIL


def test_a7_uses_catalog_price_times_execution_quantity() -> None:
    mandate = make_mandate(max_total_minor=500_000)
    transaction = make_transaction(
        payload=make_payload(
            lines=(make_line(effective_unit_price_minor=200_000, quantity=3),)
        )
    )
    catalog = make_catalog(price_minor=200_000)

    result = _result(
        _evaluate(mandate=mandate, transaction=transaction, catalog=catalog),
        TaxonomyFamily.A7,
    )

    assert result.status is TierACheckStatus.FAIL
    assert result.finding is not None
    assert dict(result.finding.details)["catalog_total_minor"] == 600_000


def test_a8_uses_catalog_recurrence_not_agent_recurrence() -> None:
    mandate = make_mandate(recurring_allowed=False)
    transaction = make_transaction(payload=make_payload(lines=(make_line(recurring=False),)))
    catalog = make_catalog(recurring=True)

    result = _result(
        _evaluate(mandate=mandate, transaction=transaction, catalog=catalog),
        TaxonomyFamily.A8,
    )

    assert result.status is TierACheckStatus.FAIL


def test_missing_catalog_is_not_evaluable_only_for_catalog_dependent_checks() -> None:
    results = _evaluate(catalog=None)
    statuses = {result.family: result.status for result in results}

    assert {
        family for family, status in statuses.items() if status is TierACheckStatus.NOT_EVALUABLE
    } == {
        TaxonomyFamily.A1,
        TaxonomyFamily.A2,
        TaxonomyFamily.A3,
        TaxonomyFamily.A6,
        TaxonomyFamily.A7,
        TaxonomyFamily.A8,
    }
    assert statuses[TaxonomyFamily.A4] is TierACheckStatus.PASS
    assert statuses[TaxonomyFamily.A5] is TierACheckStatus.PASS


def test_unavailable_nonce_ledger_and_server_time_are_not_evaluable() -> None:
    results = _evaluate(nonce_state=None, server_time=None)

    assert _result(results, TaxonomyFamily.A4).status is TierACheckStatus.NOT_EVALUABLE
    assert _result(results, TaxonomyFamily.A5).status is TierACheckStatus.NOT_EVALUABLE


def test_incomparable_catalog_currency_is_not_evaluable_without_finding() -> None:
    results = _evaluate(catalog=make_catalog(currency="USD"))

    for family in (TaxonomyFamily.A1, TaxonomyFamily.A7):
        result = _result(results, family)
        assert result.status is TierACheckStatus.NOT_EVALUABLE
        assert result.finding is None


def test_missing_sku_recurrence_and_price_evidence_is_not_evaluable() -> None:
    transaction = make_transaction(payload=make_payload(lines=(make_line(sku="missing"),)))
    results = _evaluate(transaction=transaction, catalog=make_catalog(items=()))

    assert _result(results, TaxonomyFamily.A2).status is TierACheckStatus.FAIL
    for family in (TaxonomyFamily.A1, TaxonomyFamily.A7, TaxonomyFamily.A8):
        result = _result(results, family)
        assert result.status is TierACheckStatus.NOT_EVALUABLE
        assert result.finding is None


def test_catalog_commitment_mismatch_fails_a6_only() -> None:
    transaction = make_transaction()
    catalog = make_catalog()
    commitments = CommittedHashes(
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256="0" * 64,
    )

    results = _evaluate(
        transaction=transaction,
        catalog=catalog,
        commitments=commitments,
    )

    a6 = _result(results, TaxonomyFamily.A6)
    assert a6.status is TierACheckStatus.FAIL
    assert a6.finding is not None
    for family in (
        TaxonomyFamily.A1,
        TaxonomyFamily.A2,
        TaxonomyFamily.A3,
        TaxonomyFamily.A7,
        TaxonomyFamily.A8,
    ):
        result = _result(results, family)
        assert result.status is TierACheckStatus.NOT_EVALUABLE
        assert result.finding is None
        assert result.reason == "catalog failed commitment integrity verification"


def test_absent_catalog_commitment_is_not_evaluable_without_findings() -> None:
    transaction = make_transaction()
    catalog = make_catalog()
    commitments = CommittedHashes(
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=None,
    )

    results = _evaluate(
        transaction=transaction,
        catalog=catalog,
        commitments=commitments,
    )

    a6 = _result(results, TaxonomyFamily.A6)
    assert a6.status is TierACheckStatus.NOT_EVALUABLE
    assert a6.finding is None
    for family in (
        TaxonomyFamily.A1,
        TaxonomyFamily.A2,
        TaxonomyFamily.A3,
        TaxonomyFamily.A7,
        TaxonomyFamily.A8,
    ):
        result = _result(results, family)
        assert result.status is TierACheckStatus.NOT_EVALUABLE
        assert result.finding is None
        assert result.reason == "committed merchant catalog snapshot unavailable"


def test_only_actual_tier_a_violations_have_findings() -> None:
    passing = _evaluate()
    unavailable = _evaluate(catalog=None, nonce_state=None, server_time=None)
    expired = _evaluate(server_time=make_mandate().payload.expires_at)

    assert all(result.finding is None for result in passing)
    assert all(
        result.finding is None
        for result in unavailable
        if result.status is TierACheckStatus.NOT_EVALUABLE
    )
    assert _result(expired, TaxonomyFamily.A5).finding is not None


def test_non_applicable_recurrence_check_passes_instead_of_not_evaluable() -> None:
    result = _result(_evaluate(catalog=make_catalog(recurring=False)), TaxonomyFamily.A8)

    assert result.status is TierACheckStatus.PASS


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
