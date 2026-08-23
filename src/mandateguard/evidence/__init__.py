"""PSP-controlled independent merchant catalog evidence acquisition."""

from mandateguard.evidence.acquisition import CatalogEvidence, acquire_catalog_evidence
from mandateguard.evidence.catalog_loader import (
    FixtureCatalogEvidenceProvider,
    load_catalog_fixture,
)
from mandateguard.evidence.provider import (
    CatalogEvidenceAcquisitionError,
    CatalogEvidenceProvider,
    CatalogProviderFailureError,
    CatalogProviderNotConfiguredError,
    CatalogSourceInvalidError,
    CatalogSourceUnavailableError,
)
from mandateguard.evidence.registry import CatalogProviderRegistry

__all__ = [
    "CatalogEvidence",
    "CatalogEvidenceAcquisitionError",
    "CatalogEvidenceProvider",
    "CatalogProviderFailureError",
    "CatalogProviderNotConfiguredError",
    "CatalogProviderRegistry",
    "CatalogSourceInvalidError",
    "CatalogSourceUnavailableError",
    "FixtureCatalogEvidenceProvider",
    "acquire_catalog_evidence",
    "load_catalog_fixture",
]
