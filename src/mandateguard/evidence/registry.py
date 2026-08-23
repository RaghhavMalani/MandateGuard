"""Immutable PSP-side merchant-to-provider configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from mandateguard.evidence.provider import CatalogEvidenceProvider


def _validate_merchant_id(merchant_id: object) -> None:
    if not isinstance(merchant_id, str) or not merchant_id or len(merchant_id) > 128:
        raise ValueError(
            "merchant_id must be a non-empty string of at most 128 characters"
        )


@dataclass(frozen=True, slots=True, init=False)
class CatalogProviderRegistry:
    """Frozen provider selection configured by the PSP, keyed by merchant identity."""

    _providers: Mapping[str, CatalogEvidenceProvider]

    def __init__(self, providers: Mapping[str, CatalogEvidenceProvider]) -> None:
        if not isinstance(providers, Mapping):
            raise TypeError("providers must be a mapping")
        configured: dict[str, CatalogEvidenceProvider] = {}
        for merchant_id, provider in providers.items():
            _validate_merchant_id(merchant_id)
            if not isinstance(provider, CatalogEvidenceProvider):
                raise TypeError("each provider must implement CatalogEvidenceProvider")
            configured[merchant_id] = provider
        object.__setattr__(self, "_providers", MappingProxyType(configured))

    def provider_for(self, *, merchant_id: str) -> CatalogEvidenceProvider | None:
        """Resolve only by merchant identity; all source details stay in PSP config."""

        _validate_merchant_id(merchant_id)
        return self._providers.get(merchant_id)
