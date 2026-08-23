from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import pytest

from mandateguard.evidence.provider import CatalogEvidenceProvider
from mandateguard.evidence.registry import CatalogProviderRegistry
from mandateguard.models.catalog import CatalogSnapshot
from tests.factories import make_catalog


@dataclass
class RecordingProvider:
    snapshot: CatalogSnapshot
    calls: list[str]

    def fetch_catalog(self, *, merchant_id: str) -> CatalogSnapshot:
        self.calls.append(merchant_id)
        return self.snapshot


def test_registry_resolves_the_psp_configured_provider_by_merchant() -> None:
    first = RecordingProvider(make_catalog(merchant_id="merchant-1"), [])
    second = RecordingProvider(make_catalog(merchant_id="merchant-2"), [])
    registry = CatalogProviderRegistry(
        {"merchant-1": first, "merchant-2": second}
    )

    assert registry.provider_for(merchant_id="merchant-1") is first
    assert registry.provider_for(merchant_id="merchant-2") is second
    assert registry.provider_for(merchant_id="merchant-unknown") is None
    assert isinstance(first, CatalogEvidenceProvider)


def test_registry_copies_psp_configuration_and_has_no_mutation_api() -> None:
    first = RecordingProvider(make_catalog(), [])
    configured = {"merchant-1": first}
    registry = CatalogProviderRegistry(configured)
    configured.clear()

    assert registry.provider_for(merchant_id="merchant-1") is first
    with pytest.raises(FrozenInstanceError):
        registry._providers = {}
    assert not hasattr(registry, "register")
    assert not hasattr(registry, "unregister")


@pytest.mark.parametrize("merchant_id", ["", "x" * 129, 1, None])
def test_registry_rejects_invalid_merchant_configuration(merchant_id) -> None:
    provider = RecordingProvider(make_catalog(), [])

    with pytest.raises(ValueError, match="merchant_id"):
        CatalogProviderRegistry({merchant_id: provider})


def test_registry_rejects_objects_without_the_narrow_provider_interface() -> None:
    with pytest.raises(TypeError, match="CatalogEvidenceProvider"):
        CatalogProviderRegistry({"merchant-1": object()})
