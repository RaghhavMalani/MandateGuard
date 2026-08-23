from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
import json
from pathlib import Path

import pytest

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.evidence.acquisition import acquire_catalog_evidence
from mandateguard.evidence.catalog_loader import FixtureCatalogEvidenceProvider
from mandateguard.evidence.provider import (
    CatalogEvidenceAcquisitionError,
    CatalogProviderFailureError,
    CatalogProviderNotConfiguredError,
    CatalogSourceInvalidError,
    CatalogSourceUnavailableError,
)
from mandateguard.evidence.registry import CatalogProviderRegistry
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.finding import TaxonomyFamily, TierACheckStatus
from mandateguard.policy.tier_a import evaluate_tier_a
from mandateguard.replay.runner import replay_scenario
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


VALID_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "catalogs"
    / "merchant-1.json"
)


def _fixture_provider(path: Path = VALID_FIXTURE) -> FixtureCatalogEvidenceProvider:
    return FixtureCatalogEvidenceProvider(fixture_path=path)


def _registry(path: Path = VALID_FIXTURE) -> CatalogProviderRegistry:
    return CatalogProviderRegistry({"merchant-1": _fixture_provider(path)})


def _fixture_mapping(
    *,
    merchant_id: str = "merchant-1",
    items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "snapshot_id": "fixture-test-v1",
        "merchant_id": merchant_id,
        "currency": "INR",
        "items": items
        if items is not None
        else [
            {
                "sku": "sku-1",
                "merchant_id": merchant_id,
                "effective_unit_price_minor": 10_000,
                "recurring": False,
            }
        ],
    }


def _write_fixture(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_transaction_price_cannot_affect_provider_catalog_price() -> None:
    transaction = make_transaction(
        payload=make_payload(
            lines=(make_line(effective_unit_price_minor=999_999),),
            declared_order_total_minor=999_999,
        )
    )

    evidence = acquire_catalog_evidence(
        registry=_registry(), merchant_id=transaction.payload.merchant_id
    )

    assert transaction.payload.lines[0].effective_unit_price_minor == 999_999
    assert (
        evidence.catalog_snapshot.item_by_sku("sku-1").effective_unit_price_minor
        == 10_000
    )


def test_transaction_line_total_cannot_affect_catalog_evidence() -> None:
    transaction = make_transaction(
        payload=make_payload(
            lines=(make_line(line_total_minor=1),),
            declared_order_total_minor=1,
        )
    )

    evidence = acquire_catalog_evidence(
        registry=_registry(), merchant_id=transaction.payload.merchant_id
    )

    assert transaction.payload.lines[0].line_total_minor == 1
    assert (
        evidence.catalog_snapshot.item_by_sku("sku-1").effective_unit_price_minor
        == 10_000
    )


def test_transaction_sku_cannot_redirect_provider_to_another_source() -> None:
    transaction = make_transaction(
        payload=make_payload(lines=(make_line(sku="../../attacker-catalog.json"),))
    )

    evidence = acquire_catalog_evidence(
        registry=_registry(), merchant_id=transaction.payload.merchant_id
    )

    assert evidence.catalog_snapshot.snapshot_id == "fixture-merchant-1-v1"
    assert evidence.catalog_snapshot.item_by_sku("sku-1") is not None


def test_buyer_cannot_supply_an_arbitrary_fixture_path() -> None:
    signature = inspect.signature(acquire_catalog_evidence)
    assert tuple(signature.parameters) == ("registry", "merchant_id")

    with pytest.raises(TypeError, match="unexpected keyword"):
        acquire_catalog_evidence(
            registry=_registry(),
            merchant_id="merchant-1",
            fixture_path=Path("attacker.json"),
        )


def test_buyer_cannot_select_another_provider() -> None:
    attacker_provider = _fixture_provider()

    with pytest.raises(TypeError, match="unexpected keyword"):
        acquire_catalog_evidence(
            registry=_registry(),
            merchant_id="merchant-1",
            provider=attacker_provider,
        )


def test_unknown_merchant_fails_closed_without_an_empty_catalog() -> None:
    with pytest.raises(CatalogProviderNotConfiguredError, match="not configured"):
        acquire_catalog_evidence(
            registry=_registry(), merchant_id="merchant-unknown"
        )


def test_missing_fixture_is_an_explicit_acquisition_failure(tmp_path: Path) -> None:
    with pytest.raises(CatalogSourceUnavailableError, match="unavailable"):
        acquire_catalog_evidence(
            registry=_registry(tmp_path / "missing.json"),
            merchant_id="merchant-1",
        )


def test_malformed_fixture_is_an_explicit_acquisition_failure(tmp_path: Path) -> None:
    fixture = tmp_path / "malformed.json"
    fixture.write_text("not-json", encoding="utf-8")

    with pytest.raises(CatalogSourceInvalidError, match="malformed"):
        acquire_catalog_evidence(
            registry=_registry(fixture), merchant_id="merchant-1"
        )


def test_duplicate_catalog_sku_is_rejected(tmp_path: Path) -> None:
    item = {
        "sku": "sku-1",
        "merchant_id": "merchant-1",
        "effective_unit_price_minor": 10_000,
        "recurring": False,
    }
    fixture = tmp_path / "duplicate-sku.json"
    _write_fixture(fixture, _fixture_mapping(items=[item, item.copy()]))

    with pytest.raises(CatalogSourceInvalidError, match="malformed"):
        acquire_catalog_evidence(
            registry=_registry(fixture), merchant_id="merchant-1"
        )


def test_fixture_merchant_must_match_registered_merchant(tmp_path: Path) -> None:
    fixture = tmp_path / "wrong-merchant.json"
    _write_fixture(fixture, _fixture_mapping(merchant_id="merchant-2"))

    with pytest.raises(CatalogSourceInvalidError, match="registered merchant"):
        acquire_catalog_evidence(
            registry=_registry(fixture), merchant_id="merchant-1"
        )


def test_successful_acquisition_commitment_hashes_the_returned_snapshot() -> None:
    evidence = acquire_catalog_evidence(
        registry=_registry(), merchant_id="merchant-1"
    )

    assert evidence.catalog_snapshot_sha256 == catalog_snapshot_sha256(
        evidence.catalog_snapshot
    )


def test_snapshot_replacement_after_commitment_is_detected_by_a6() -> None:
    evidence = acquire_catalog_evidence(
        registry=_registry(), merchant_id="merchant-1"
    )
    transaction = make_transaction()
    original_item = evidence.catalog_snapshot.item_by_sku("sku-1")
    mutated_item = replace(original_item, effective_unit_price_minor=1)
    mutated_snapshot = replace(
        evidence.catalog_snapshot,
        items=tuple(
            mutated_item if item.sku == "sku-1" else item
            for item in evidence.catalog_snapshot.items
        ),
    )
    commitments = CommittedHashes(
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=evidence.catalog_snapshot_sha256,
    )

    results = evaluate_tier_a(
        mandate=make_mandate(),
        transaction=transaction,
        catalog_snapshot=mutated_snapshot,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        committed_hashes=commitments,
    )
    a6 = next(result for result in results if result.family is TaxonomyFamily.A6)

    assert a6.status is TierACheckStatus.FAIL
    assert dict(a6.finding.details)["mutated_snapshots"] == "catalog"


def test_same_valid_fixture_produces_deterministic_snapshot_and_hash() -> None:
    first = acquire_catalog_evidence(
        registry=_registry(), merchant_id="merchant-1"
    )
    second = acquire_catalog_evidence(
        registry=_registry(), merchant_id="merchant-1"
    )

    assert first.catalog_snapshot == second.catalog_snapshot
    assert first.catalog_snapshot_sha256 == second.catalog_snapshot_sha256


def test_catalog_item_order_has_canonical_acquisition_hash(tmp_path: Path) -> None:
    first_item = {
        "sku": "sku-1",
        "merchant_id": "merchant-1",
        "effective_unit_price_minor": 10_000,
        "recurring": False,
    }
    second_item = {
        "sku": "sku-2",
        "merchant_id": "merchant-1",
        "effective_unit_price_minor": 20_000,
        "recurring": False,
    }
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    _write_fixture(first_path, _fixture_mapping(items=[first_item, second_item]))
    _write_fixture(second_path, _fixture_mapping(items=[second_item, first_item]))

    first = acquire_catalog_evidence(
        registry=_registry(first_path), merchant_id="merchant-1"
    )
    second = acquire_catalog_evidence(
        registry=_registry(second_path), merchant_id="merchant-1"
    )

    assert first.catalog_snapshot == second.catalog_snapshot
    assert first.catalog_snapshot_sha256 == second.catalog_snapshot_sha256


@dataclass
class ChangingProvider:
    calls: int = 0
    last_returned: CatalogSnapshot | None = None

    def fetch_catalog(self, *, merchant_id: str) -> CatalogSnapshot:
        self.calls += 1
        self.last_returned = make_catalog(
            price_minor=self.calls * 10_000,
            merchant_id=merchant_id,
        )
        return self.last_returned


def test_live_acquisition_fetches_once_and_evaluates_the_committed_object() -> None:
    provider = ChangingProvider()
    registry = CatalogProviderRegistry({"merchant-1": provider})
    transaction = make_transaction()

    evidence = acquire_catalog_evidence(
        registry=registry, merchant_id=transaction.payload.merchant_id
    )
    commitments = CommittedHashes(
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=evidence.catalog_snapshot_sha256,
    )
    results = evaluate_tier_a(
        mandate=make_mandate(),
        transaction=transaction,
        catalog_snapshot=evidence.catalog_snapshot,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        committed_hashes=commitments,
    )
    a6 = next(result for result in results if result.family is TaxonomyFamily.A6)

    assert provider.calls == 1
    assert evidence.catalog_snapshot is provider.last_returned
    assert evidence.catalog_snapshot.item_by_sku("sku-1").effective_unit_price_minor == 10_000
    assert a6.status is TierACheckStatus.PASS


@dataclass
class RaisingProvider:
    calls: int = 0

    def fetch_catalog(self, *, merchant_id: str) -> CatalogSnapshot:
        self.calls += 1
        raise AssertionError("provider must not be called during replay")


def test_replay_uses_historical_snapshot_without_provider_lookup() -> None:
    provider = RaisingProvider()
    registry = CatalogProviderRegistry({"merchant-1": provider})
    assert registry.provider_for(merchant_id="merchant-1") is provider
    transaction = make_transaction()
    historical_catalog = make_catalog()
    scenario = ReplayScenario(
        mandate=make_mandate(),
        transaction=transaction,
        catalog_snapshot=historical_catalog,
        server_time=SERVER_TIME,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=make_commitments(transaction, historical_catalog),
        replay_seed=91,
        evaluated_at=SERVER_TIME,
    )

    event = replay_scenario(scenario)

    assert provider.calls == 0
    assert event.catalog_snapshot_sha256 == catalog_snapshot_sha256(historical_catalog)


def test_provider_exception_is_wrapped_as_acquisition_failure() -> None:
    provider = RaisingProvider()
    registry = CatalogProviderRegistry({"merchant-1": provider})

    with pytest.raises(CatalogProviderFailureError) as caught:
        acquire_catalog_evidence(registry=registry, merchant_id="merchant-1")

    assert isinstance(caught.value, CatalogEvidenceAcquisitionError)
    assert isinstance(caught.value.__cause__, AssertionError)
    assert provider.calls == 1


class InvalidProvider:
    def fetch_catalog(self, *, merchant_id: str):
        return {"merchant_id": merchant_id}


def test_provider_must_return_a_complete_typed_snapshot() -> None:
    registry = CatalogProviderRegistry({"merchant-1": InvalidProvider()})

    with pytest.raises(CatalogProviderFailureError, match="invalid snapshot type"):
        acquire_catalog_evidence(registry=registry, merchant_id="merchant-1")


class WrongMerchantProvider:
    def fetch_catalog(self, *, merchant_id: str) -> CatalogSnapshot:
        return make_catalog(merchant_id="merchant-2")


def test_every_provider_is_bound_to_the_registered_merchant_identity() -> None:
    registry = CatalogProviderRegistry({"merchant-1": WrongMerchantProvider()})

    with pytest.raises(CatalogSourceInvalidError, match="registered merchant"):
        acquire_catalog_evidence(registry=registry, merchant_id="merchant-1")
