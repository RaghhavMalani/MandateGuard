"""Manifest-verified, server-side trusted-source resolution for REVIEW recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from mandateguard.core.hashing import sha256_canonical
from mandateguard.recovery.models import (
    MAX_NEW_EVIDENCE_ITEMS,
    AcquisitionItemStatus,
    EvidenceKind,
    EvidenceScope,
    TrustedEvidenceRecord,
    TrustedEvidenceSource,
)
from mandateguard.semantic.evidence import (
    SemanticEvidenceAcquisitionError,
    SemanticEvidenceEntry,
    SemanticEvidenceProviderRegistry,
    acquire_semantic_evidence,
)


@dataclass(frozen=True, slots=True)
class AcquiredEvidenceItem:
    """Verification result for one complete source manifest."""

    source_id: str
    status: AcquisitionItemStatus
    manifest_sha256: str | None
    source_scope: EvidenceScope | None
    expected_ids: tuple[str, ...] = ()
    expected_hashes: tuple[str, ...] = ()
    received_ids: tuple[str, ...] = ()
    received_hashes: tuple[str, ...] = ()
    entries: tuple[SemanticEvidenceEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("source_id must be non-empty")
        if not isinstance(self.status, AcquisitionItemStatus):
            raise TypeError("status must be AcquisitionItemStatus")
        if self.source_scope is not None and not isinstance(
            self.source_scope, EvidenceScope
        ):
            raise TypeError("source_scope must be EvidenceScope or None")
        if not isinstance(self.entries, tuple) or not all(
            isinstance(entry, SemanticEvidenceEntry) for entry in self.entries
        ):
            raise TypeError("entries must be a SemanticEvidenceEntry tuple")
        if self.status is not AcquisitionItemStatus.ACQUIRED and self.entries:
            raise ValueError("an unverified source cannot expose authorization entries")

    @property
    def entry(self) -> SemanticEvidenceEntry | None:
        """Compatibility accessor for single-record manifests."""

        return self.entries[0] if len(self.entries) == 1 else None


@dataclass(frozen=True, slots=True)
class AcquisitionBatch:
    items: tuple[AcquiredEvidenceItem, ...]
    provider_calls: int
    expected_applicable_ids: tuple[str, ...] = ()
    actual_applicable_ids: tuple[str, ...] = ()
    conflict_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, AcquiredEvidenceItem) for item in self.items
        ):
            raise TypeError("items must be an AcquiredEvidenceItem tuple")
        if (
            isinstance(self.provider_calls, bool)
            or not isinstance(self.provider_calls, int)
            or self.provider_calls < 0
        ):
            raise ValueError("provider_calls must be non-negative")

    @property
    def complete(self) -> bool:
        return (
            bool(self.items)
            and all(item.status is AcquisitionItemStatus.ACQUIRED for item in self.items)
            and not self.conflict_codes
            and self.actual_applicable_ids == self.expected_applicable_ids
        )

    @property
    def acquired_entries(self) -> tuple[SemanticEvidenceEntry, ...]:
        unique: dict[str, SemanticEvidenceEntry] = {}
        for item in self.items:
            if item.status is AcquisitionItemStatus.ACQUIRED:
                for entry in item.entries:
                    unique[entry.evidence_id] = entry
        return tuple(unique[key] for key in sorted(unique))


def _source_identity(source: TrustedEvidenceSource) -> tuple[object, ...]:
    manifest = source.manifest
    return (
        manifest.merchant_id,
        manifest.scope_type,
        manifest.sku,
        tuple(sorted(zip(manifest.record_ids, manifest.record_hashes, strict=True))),
    )


def _scope_entries(
    source: TrustedEvidenceSource,
    entries: tuple[SemanticEvidenceEntry, ...],
) -> tuple[SemanticEvidenceEntry, ...]:
    manifest = source.manifest
    if manifest.scope_type is EvidenceScope.MERCHANT_GLOBAL:
        return tuple(entry for entry in entries if entry.sku is None)
    return tuple(entry for entry in entries if entry.sku == manifest.sku)


def _active_sources(
    sources: tuple[TrustedEvidenceSource, ...], at_time: datetime
) -> tuple[TrustedEvidenceSource, ...]:
    active = tuple(source for source in sources if source.manifest.active_at(at_time))
    superseded = {
        source.manifest.supersedes_manifest_id
        for source in active
        if source.manifest.supersedes_manifest_id is not None
    }
    return tuple(
        source for source in active if source.manifest.manifest_id not in superseded
    )


def _applicable_records(
    sources: tuple[TrustedEvidenceSource, ...], at_time: datetime
) -> tuple[tuple[str, TrustedEvidenceRecord], ...]:
    records = tuple(
        (source.source_id, record)
        for source in sources
        for record in source.manifest.records
        if record.active_at(at_time)
    )
    superseded_ids = {
        record.supersedes_evidence_id
        for _, record in records
        if record.supersedes_evidence_id is not None
    }
    return tuple(
        (source_id, record)
        for source_id, record in records
        if record.evidence_id not in superseded_ids
    )


class TrustedEvidenceSourceRegistry:
    """Immutable manifests layered on the existing merchant provider registry."""

    __slots__ = ("_sources", "_providers", "registry_sha256")

    def __init__(
        self,
        *,
        sources: tuple[TrustedEvidenceSource, ...],
        providers: SemanticEvidenceProviderRegistry,
    ) -> None:
        if not isinstance(sources, tuple) or not all(
            isinstance(source, TrustedEvidenceSource) for source in sources
        ):
            raise TypeError("sources must be a TrustedEvidenceSource tuple")
        if not isinstance(providers, SemanticEvidenceProviderRegistry):
            raise TypeError("providers must be SemanticEvidenceProviderRegistry")
        mapped: dict[str, TrustedEvidenceSource] = {}
        manifest_ids: set[str] = set()
        for source in sources:
            if source.source_id in mapped:
                raise ValueError("trusted recovery source IDs must be unique")
            if source.manifest.manifest_id in manifest_ids:
                raise ValueError("trusted recovery manifest IDs must be unique")
            mapped[source.source_id] = source
            manifest_ids.add(source.manifest.manifest_id)
        self._sources: Mapping[str, TrustedEvidenceSource] = MappingProxyType(mapped)
        self._providers = providers
        self.registry_sha256 = sha256_canonical(
            tuple(source.manifest for source in sorted(sources, key=lambda item: item.source_id))
        )

    def source(self, source_id: str) -> TrustedEvidenceSource | None:
        if not isinstance(source_id, str):
            raise TypeError("source_id must be a string")
        return self._sources.get(source_id)

    def candidates(
        self,
        *,
        merchant_id: str,
        sku: str,
        evidence_kind: EvidenceKind,
        at_time: datetime,
        excluded_evidence_ids: frozenset[str] = frozenset(),
    ) -> tuple[TrustedEvidenceSource, ...]:
        """Return all active applicable sources, deduplicated before any budget."""

        if not isinstance(evidence_kind, EvidenceKind):
            raise TypeError("evidence_kind must be EvidenceKind")
        possible = tuple(
            source
            for source in self._sources.values()
            if source.merchant_id == merchant_id
            and evidence_kind in source.evidence_kinds
            and (
                source.manifest.scope_type is EvidenceScope.MERCHANT_GLOBAL
                or source.sku == sku
            )
            and not set(source.manifest.record_ids).issubset(excluded_evidence_ids)
        )
        active = _active_sources(possible, at_time)
        unique: dict[tuple[object, ...], TrustedEvidenceSource] = {}
        for source in sorted(active, key=lambda item: item.source_id):
            unique.setdefault(_source_identity(source), source)
        return tuple(unique.values())

    def acquire(
        self,
        *,
        source_ids: tuple[str, ...],
        merchant_id: str,
        skus: tuple[str, ...],
        existing_entries: tuple[SemanticEvidenceEntry, ...],
        item_limit: int,
        acquired_at: datetime,
    ) -> AcquisitionBatch:
        """Fetch complete source scopes or return a non-authorizing failure batch."""

        if not isinstance(source_ids, tuple) or not all(
            isinstance(source_id, str) for source_id in source_ids
        ):
            raise TypeError("source_ids must be a tuple of strings")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be unique")
        if not isinstance(skus, tuple) or not skus or not all(
            isinstance(sku, str) and sku for sku in skus
        ):
            raise ValueError("skus must be a non-empty string tuple")
        if (
            isinstance(item_limit, bool)
            or not isinstance(item_limit, int)
            or not 0 <= item_limit <= MAX_NEW_EVIDENCE_ITEMS
        ):
            raise ValueError("item_limit is outside the fixed evidence budget")
        if (
            not isinstance(acquired_at, datetime)
            or acquired_at.tzinfo is None
            or acquired_at.utcoffset() is None
        ):
            raise ValueError("acquired_at must be timezone-aware")

        requested = tuple(self._sources.get(source_id) for source_id in source_ids)
        invalid_items: list[AcquiredEvidenceItem] = []
        valid: list[TrustedEvidenceSource] = []
        seen_identities: set[tuple[object, ...]] = set()
        for source_id, source in zip(source_ids, requested, strict=True):
            if source is None:
                invalid_items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.NO_RECORD,
                        manifest_sha256=None,
                        source_scope=None,
                    )
                )
                continue
            manifest = source.manifest
            common = {
                "source_id": source_id,
                "manifest_sha256": manifest.manifest_sha256,
                "source_scope": manifest.scope_type,
                "expected_ids": manifest.record_ids,
                "expected_hashes": manifest.record_hashes,
            }
            if manifest.merchant_id != merchant_id or (
                manifest.scope_type is EvidenceScope.SKU_SPECIFIC
                and manifest.sku not in skus
            ):
                invalid_items.append(
                    AcquiredEvidenceItem(
                        status=AcquisitionItemStatus.WRONG_BINDING, **common
                    )
                )
                continue
            if acquired_at < manifest.effective_at:
                invalid_items.append(
                    AcquiredEvidenceItem(
                        status=AcquisitionItemStatus.SOURCE_NOT_EFFECTIVE, **common
                    )
                )
                continue
            if manifest.expires_at is not None and acquired_at >= manifest.expires_at:
                invalid_items.append(
                    AcquiredEvidenceItem(
                        status=AcquisitionItemStatus.SOURCE_EXPIRED, **common
                    )
                )
                continue
            identity = _source_identity(source)
            if identity in seen_identities:
                continue
            seen_identities.add(identity)
            valid.append(source)

        selected = _active_sources(tuple(valid), acquired_at)
        applicable = _applicable_records(selected, acquired_at)
        expected_hash_by_id: dict[str, str] = {}
        manifest_conflicts: set[str] = set()
        for _, record in applicable:
            previous = expected_hash_by_id.get(record.evidence_id)
            if previous is not None and previous != record.expected_entry_sha256:
                manifest_conflicts.add("DUPLICATE_ID_HASH_CONFLICT")
            expected_hash_by_id[record.evidence_id] = record.expected_entry_sha256
        expected_applicable_ids = tuple(sorted(expected_hash_by_id))

        if selected and not expected_applicable_ids:
            stale_items = tuple(invalid_items) + tuple(
                AcquiredEvidenceItem(
                    source_id=source.source_id,
                    status=(
                        AcquisitionItemStatus.SOURCE_NOT_EFFECTIVE
                        if all(
                            acquired_at < record.effective_at
                            for record in source.manifest.records
                        )
                        else AcquisitionItemStatus.SOURCE_EXPIRED
                    ),
                    manifest_sha256=source.manifest.manifest_sha256,
                    source_scope=source.manifest.scope_type,
                    expected_ids=source.manifest.record_ids,
                    expected_hashes=source.manifest.record_hashes,
                )
                for source in selected
            )
            return AcquisitionBatch(items=stale_items, provider_calls=0)

        if manifest_conflicts:
            conflicted = tuple(
                replace(item, status=AcquisitionItemStatus.CONFLICT)
                for item in invalid_items
            ) + tuple(
                AcquiredEvidenceItem(
                    source_id=source.source_id,
                    status=AcquisitionItemStatus.CONFLICT,
                    manifest_sha256=source.manifest.manifest_sha256,
                    source_scope=source.manifest.scope_type,
                    expected_ids=source.manifest.record_ids,
                    expected_hashes=source.manifest.record_hashes,
                )
                for source in selected
            )
            return AcquisitionBatch(
                items=conflicted,
                provider_calls=0,
                expected_applicable_ids=expected_applicable_ids,
                conflict_codes=tuple(sorted(manifest_conflicts)),
            )

        existing_ids = {entry.evidence_id for entry in existing_entries}
        new_expected_ids = tuple(
            evidence_id
            for evidence_id in expected_applicable_ids
            if evidence_id not in existing_ids
        )
        if len(new_expected_ids) > item_limit:
            budget_items = tuple(invalid_items) + tuple(
                AcquiredEvidenceItem(
                    source_id=source.source_id,
                    status=AcquisitionItemStatus.BUDGET_INSUFFICIENT,
                    manifest_sha256=source.manifest.manifest_sha256,
                    source_scope=source.manifest.scope_type,
                    expected_ids=source.manifest.record_ids,
                    expected_hashes=source.manifest.record_hashes,
                )
                for source in selected
            )
            return AcquisitionBatch(
                items=budget_items,
                provider_calls=0,
                expected_applicable_ids=expected_applicable_ids,
            )
        if not selected:
            return AcquisitionBatch(items=tuple(invalid_items), provider_calls=0)

        try:
            acquired = acquire_semantic_evidence(self._providers, merchant_id)
        except SemanticEvidenceAcquisitionError:
            unavailable = tuple(invalid_items) + tuple(
                AcquiredEvidenceItem(
                    source_id=source.source_id,
                    status=AcquisitionItemStatus.SOURCE_UNAVAILABLE,
                    manifest_sha256=source.manifest.manifest_sha256,
                    source_scope=source.manifest.scope_type,
                    expected_ids=source.manifest.record_ids,
                    expected_hashes=source.manifest.record_hashes,
                )
                for source in selected
            )
            return AcquisitionBatch(
                items=unavailable,
                provider_calls=1,
                expected_applicable_ids=expected_applicable_ids,
            )

        bundle_entries = acquired.bundle.entries
        raw_entry_by_id = {entry.evidence_id: entry for entry in bundle_entries}
        source_items: list[AcquiredEvidenceItem] = list(invalid_items)
        verified_entries: dict[str, SemanticEvidenceEntry] = {}
        record_by_source = {
            source.source_id: {record.evidence_id: record for record in source.manifest.records}
            for source in selected
        }
        applicable_ids_by_source: dict[str, set[str]] = {}
        for source_id, record in applicable:
            applicable_ids_by_source.setdefault(source_id, set()).add(record.evidence_id)

        for source in selected:
            manifest = source.manifest
            scoped = _scope_entries(source, bundle_entries)
            received_ids = tuple(entry.evidence_id for entry in scoped)
            received_hashes = tuple(sha256_canonical(entry) for entry in scoped)
            common = {
                "source_id": source.source_id,
                "manifest_sha256": manifest.manifest_sha256,
                "source_scope": manifest.scope_type,
                "expected_ids": manifest.record_ids,
                "expected_hashes": manifest.record_hashes,
                "received_ids": received_ids,
                "received_hashes": received_hashes,
            }
            wrongly_bound = tuple(
                raw_entry_by_id[evidence_id]
                for evidence_id in manifest.record_ids
                if evidence_id in raw_entry_by_id
                and raw_entry_by_id[evidence_id] not in scoped
            )
            if wrongly_bound:
                source_items.append(
                    AcquiredEvidenceItem(
                        status=AcquisitionItemStatus.WRONG_BINDING, **common
                    )
                )
                continue
            if set(received_ids) != set(manifest.record_ids):
                source_items.append(
                    AcquiredEvidenceItem(
                        status=AcquisitionItemStatus.SOURCE_INCOMPLETE, **common
                    )
                )
                continue
            actual_hash_by_id = {
                entry.evidence_id: sha256_canonical(entry) for entry in scoped
            }
            expected = record_by_source[source.source_id]
            if any(
                actual_hash_by_id[evidence_id]
                != expected[evidence_id].expected_entry_sha256
                for evidence_id in manifest.record_ids
            ):
                source_items.append(
                    AcquiredEvidenceItem(
                        status=AcquisitionItemStatus.TAMPERED, **common
                    )
                )
                continue
            active_ids = applicable_ids_by_source.get(source.source_id, set())
            active_entries = tuple(
                entry for entry in scoped if entry.evidence_id in active_ids
            )
            source_items.append(
                AcquiredEvidenceItem(
                    status=AcquisitionItemStatus.ACQUIRED,
                    entries=active_entries,
                    **common,
                )
            )
            for entry in active_entries:
                verified_entries[entry.evidence_id] = entry

        conflict_codes: set[str] = set()
        claim_values: dict[str, str] = {}
        for _, record in applicable:
            for claim in record.claims:
                previous = claim_values.get(claim.claim_id)
                if previous is not None and previous != claim.claim_value:
                    conflict_codes.add("SIMULTANEOUS_AUTHORITY_CONFLICT")
                claim_values[claim.claim_id] = claim.claim_value
        if conflict_codes:
            source_items = [
                replace(item, status=AcquisitionItemStatus.CONFLICT, entries=())
                if item.status is AcquisitionItemStatus.ACQUIRED
                else item
                for item in source_items
            ]
            verified_entries.clear()

        actual_applicable_ids = tuple(sorted(verified_entries))
        return AcquisitionBatch(
            items=tuple(source_items),
            provider_calls=1,
            expected_applicable_ids=expected_applicable_ids,
            actual_applicable_ids=actual_applicable_ids,
            conflict_codes=tuple(sorted(conflict_codes)),
        )
