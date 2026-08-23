"""Single-fetch acquisition and commitment at the catalog trust boundary."""

from __future__ import annotations

from dataclasses import dataclass

from mandateguard.core.hashing import catalog_snapshot_sha256
from mandateguard.evidence.provider import (
    CatalogEvidenceAcquisitionError,
    CatalogProviderFailureError,
    CatalogProviderNotConfiguredError,
    CatalogSourceInvalidError,
)
from mandateguard.evidence.registry import CatalogProviderRegistry
from mandateguard.models.catalog import CatalogSnapshot


@dataclass(frozen=True, slots=True)
class CatalogEvidence:
    """One exact acquired snapshot and its immediate PSP-side commitment."""

    catalog_snapshot: CatalogSnapshot
    catalog_snapshot_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_snapshot, CatalogSnapshot):
            raise TypeError("catalog_snapshot must be CatalogSnapshot")
        if catalog_snapshot_sha256(self.catalog_snapshot) != self.catalog_snapshot_sha256:
            raise ValueError("catalog_snapshot_sha256 does not commit catalog_snapshot")


def acquire_catalog_evidence(
    registry: CatalogProviderRegistry, merchant_id: str
) -> CatalogEvidence:
    """Fetch exactly once from the PSP-selected provider and commit that snapshot."""

    if not isinstance(registry, CatalogProviderRegistry):
        raise TypeError("registry must be CatalogProviderRegistry")
    try:
        provider = registry.provider_for(merchant_id=merchant_id)
    except ValueError as exc:
        raise CatalogProviderNotConfiguredError("merchant identity is invalid") from exc
    if provider is None:
        raise CatalogProviderNotConfiguredError(
            "authoritative catalog provider is not configured for merchant"
        )

    try:
        snapshot = provider.fetch_catalog(merchant_id=merchant_id)
    except CatalogEvidenceAcquisitionError:
        raise
    except Exception as exc:
        raise CatalogProviderFailureError(
            "configured catalog provider failed to acquire evidence"
        ) from exc

    if not isinstance(snapshot, CatalogSnapshot):
        raise CatalogProviderFailureError(
            "configured catalog provider returned an invalid snapshot type"
        )
    if snapshot.merchant_id != merchant_id:
        raise CatalogSourceInvalidError(
            "provider catalog merchant does not match the registered merchant"
        )

    commitment = catalog_snapshot_sha256(snapshot)
    return CatalogEvidence(
        catalog_snapshot=snapshot,
        catalog_snapshot_sha256=commitment,
    )
