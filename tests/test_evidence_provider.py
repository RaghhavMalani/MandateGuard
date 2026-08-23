from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from mandateguard.evidence.catalog_loader import FixtureCatalogEvidenceProvider
from mandateguard.evidence.provider import (
    CatalogEvidenceProvider,
    CatalogSourceInvalidError,
    CatalogSourceUnavailableError,
)


VALID_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "catalogs"
    / "merchant-1.json"
)


def _valid_fixture_mapping() -> dict[str, object]:
    return {
        "snapshot_id": "fixture-test-v1",
        "merchant_id": "merchant-1",
        "currency": "INR",
        "items": [
            {
                "sku": "sku-1",
                "merchant_id": "merchant-1",
                "effective_unit_price_minor": 10_000,
                "recurring": False,
            }
        ],
    }


def _write_mapping(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_provider_protocol_exposes_only_merchant_identity() -> None:
    signature = inspect.signature(CatalogEvidenceProvider.fetch_catalog)

    assert tuple(signature.parameters) == ("self", "merchant_id")
    assert signature.parameters["merchant_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert all(
        forbidden not in signature.parameters
        for forbidden in (
            "transaction",
            "url",
            "fixture_path",
            "provider_name",
            "catalog_price",
            "snapshot_hash",
        )
    )


def test_fixture_provider_loads_configured_complete_catalog() -> None:
    provider = FixtureCatalogEvidenceProvider(fixture_path=VALID_FIXTURE)

    snapshot = provider.fetch_catalog(merchant_id="merchant-1")

    assert isinstance(provider, CatalogEvidenceProvider)
    assert snapshot.snapshot_id == "fixture-merchant-1-v1"
    assert snapshot.merchant_id == "merchant-1"
    assert snapshot.currency == "INR"
    assert tuple(item.sku for item in snapshot.items) == ("sku-1", "sku-2")
    assert snapshot.item_by_sku("sku-1").effective_unit_price_minor == 10_000


def test_missing_fixture_is_an_explicit_acquisition_failure(tmp_path: Path) -> None:
    provider = FixtureCatalogEvidenceProvider(fixture_path=tmp_path / "missing.json")

    with pytest.raises(CatalogSourceUnavailableError, match="unavailable"):
        provider.fetch_catalog(merchant_id="merchant-1")


@pytest.mark.parametrize(
    ("mutate", "description"),
    [
        pytest.param(
            lambda value: value.update(extra="buyer-controlled"),
            "unknown top-level field",
            id="unknown-field",
        ),
        pytest.param(
            lambda value: value.update(currency="inr"),
            "invalid currency",
            id="invalid-currency",
        ),
        pytest.param(
            lambda value: value.update(items=[]),
            "empty item collection",
            id="empty-items",
        ),
        pytest.param(
            lambda value: value["items"][0].update(
                effective_unit_price_minor=100.0
            ),
            "floating-point price",
            id="floating-price",
        ),
        pytest.param(
            lambda value: value["items"][0].update(merchant_id="merchant-2"),
            "item ownership mismatch",
            id="item-merchant-mismatch",
        ),
        pytest.param(
            lambda value: value["items"][0].update(extra="unknown"),
            "unknown item field",
            id="unknown-item-field",
        ),
    ],
)
def test_fixture_provider_rejects_invalid_catalogs_without_partial_success(
    tmp_path: Path, mutate, description: str
) -> None:
    fixture = _valid_fixture_mapping()
    mutate(fixture)
    fixture_path = tmp_path / "invalid.json"
    _write_mapping(fixture_path, fixture)
    provider = FixtureCatalogEvidenceProvider(fixture_path=fixture_path)

    with pytest.raises(CatalogSourceInvalidError, match="malformed"):
        provider.fetch_catalog(merchant_id="merchant-1")

def test_fixture_provider_rejects_malformed_json(tmp_path: Path) -> None:
    fixture_path = tmp_path / "malformed.json"
    fixture_path.write_text('{"snapshot_id":', encoding="utf-8")
    provider = FixtureCatalogEvidenceProvider(fixture_path=fixture_path)

    with pytest.raises(CatalogSourceInvalidError, match="malformed"):
        provider.fetch_catalog(merchant_id="merchant-1")


def test_fixture_provider_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    fixture_path = tmp_path / "duplicate-field.json"
    fixture_path.write_text(
        '{"snapshot_id":"first","snapshot_id":"second",'
        '"merchant_id":"merchant-1","currency":"INR","items":[]}',
        encoding="utf-8",
    )
    provider = FixtureCatalogEvidenceProvider(fixture_path=fixture_path)

    with pytest.raises(CatalogSourceInvalidError, match="malformed"):
        provider.fetch_catalog(merchant_id="merchant-1")
