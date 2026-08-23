"""Narrow provider boundary for PSP-controlled catalog evidence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mandateguard.models.catalog import CatalogSnapshot


class CatalogEvidenceAcquisitionError(RuntimeError):
    """Authoritative catalog evidence could not be acquired."""


class CatalogProviderNotConfiguredError(CatalogEvidenceAcquisitionError):
    """No PSP-controlled provider is registered for the merchant."""


class CatalogSourceUnavailableError(CatalogEvidenceAcquisitionError):
    """The configured provider source could not be read."""


class CatalogSourceInvalidError(CatalogEvidenceAcquisitionError):
    """The configured provider source was malformed or inconsistent."""


class CatalogProviderFailureError(CatalogEvidenceAcquisitionError):
    """The configured provider failed without returning a complete snapshot."""


@runtime_checkable
class CatalogEvidenceProvider(Protocol):
    """Fetch one merchant catalog using only PSP-controlled source configuration."""

    def fetch_catalog(self, *, merchant_id: str) -> CatalogSnapshot:
        """Return a complete catalog snapshot for the registered merchant."""
