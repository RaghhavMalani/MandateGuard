"""Server-side trusted-source resolution for REVIEW recovery."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mandateguard.core.hashing import sha256_canonical
from mandateguard.recovery.models import (
    MAX_NEW_EVIDENCE_ITEMS,
    AcquisitionItemStatus,
    EvidenceKind,
    TrustedEvidenceSource,
)
from mandateguard.semantic.evidence import (
    SemanticEvidenceEntry,
    SemanticEvidenceProviderRegistry,
    acquire_semantic_evidence,
)


@dataclass(frozen=True, slots=True)
class AcquiredEvidenceItem:
    source_id: str
    status: AcquisitionItemStatus
    entry: SemanticEvidenceEntry | None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("source_id must be non-empty")
        if not isinstance(self.status, AcquisitionItemStatus):
            raise TypeError("status must be AcquisitionItemStatus")
        if self.status is AcquisitionItemStatus.ACQUIRED:
            if not isinstance(self.entry, SemanticEvidenceEntry):
                raise TypeError("ACQUIRED requires an evidence entry")
        elif self.entry is not None:
            raise ValueError("rejected acquisition items cannot expose evidence")


@dataclass(frozen=True, slots=True)
class AcquisitionBatch:
    items: tuple[AcquiredEvidenceItem, ...]
    provider_calls: int

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
    def acquired_entries(self) -> tuple[SemanticEvidenceEntry, ...]:
        return tuple(
            item.entry
            for item in self.items
            if item.status is AcquisitionItemStatus.ACQUIRED and item.entry is not None
        )


class TrustedEvidenceSourceRegistry:
    """Immutable source allowlist layered on existing evidence providers."""

    __slots__ = ("_sources", "_providers")

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
        for source in sources:
            if source.source_id in mapped:
                raise ValueError("trusted recovery source IDs must be unique")
            mapped[source.source_id] = source
        self._sources: Mapping[str, TrustedEvidenceSource] = MappingProxyType(mapped)
        self._providers = providers

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
        excluded_evidence_ids: frozenset[str] = frozenset(),
    ) -> tuple[TrustedEvidenceSource, ...]:
        """Return only server-registered, identity-bound candidate sources."""

        if not isinstance(evidence_kind, EvidenceKind):
            raise TypeError("evidence_kind must be EvidenceKind")
        return tuple(
            sorted(
                (
                    source
                    for source in self._sources.values()
                    if source.merchant_id == merchant_id
                    and source.sku == sku
                    and evidence_kind in source.evidence_kinds
                    and source.evidence_id not in excluded_evidence_ids
                ),
                key=lambda source: source.source_id,
            )
        )

    def acquire(
        self,
        *,
        source_ids: tuple[str, ...],
        merchant_id: str,
        sku: str,
        existing_entries: tuple[SemanticEvidenceEntry, ...],
        item_limit: int,
    ) -> AcquisitionBatch:
        """Fetch allowlisted IDs only; URLs or evidence prose are not accepted."""

        if not isinstance(source_ids, tuple) or not all(
            isinstance(source_id, str) for source_id in source_ids
        ):
            raise TypeError("source_ids must be a tuple of strings")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be unique")
        if (
            isinstance(item_limit, bool)
            or not isinstance(item_limit, int)
            or not 0 <= item_limit <= MAX_NEW_EVIDENCE_ITEMS
        ):
            raise ValueError("item_limit is outside the fixed evidence budget")

        selected_ids = source_ids[:item_limit]
        sources: list[TrustedEvidenceSource | None] = [
            self._sources.get(source_id) for source_id in selected_ids
        ]
        provider_calls = 0
        bundle_entries: dict[str, SemanticEvidenceEntry] = {}
        if any(
            source is not None
            and source.merchant_id == merchant_id
            and source.sku == sku
            for source in sources
        ):
            acquired = acquire_semantic_evidence(self._providers, merchant_id)
            provider_calls = 1
            bundle_entries = {
                entry.evidence_id: entry for entry in acquired.bundle.entries
            }

        existing_ids = {entry.evidence_id for entry in existing_entries}
        existing_content = {
            (entry.merchant_id, entry.sku, entry.source_kind, entry.text)
            for entry in existing_entries
        }
        items: list[AcquiredEvidenceItem] = []
        for source_id, source in zip(selected_ids, sources, strict=True):
            if source is None:
                items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.NO_RECORD,
                        entry=None,
                    )
                )
                continue
            if source.merchant_id != merchant_id or source.sku != sku:
                items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.WRONG_BINDING,
                        entry=None,
                    )
                )
                continue
            entry = bundle_entries.get(source.evidence_id)
            if entry is None:
                items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.NO_RECORD,
                        entry=None,
                    )
                )
                continue
            if entry.merchant_id != merchant_id or entry.sku not in {None, sku}:
                items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.WRONG_BINDING,
                        entry=None,
                    )
                )
                continue
            if sha256_canonical(entry) != source.expected_entry_sha256:
                items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.TAMPERED,
                        entry=None,
                    )
                )
                continue
            content_key = (entry.merchant_id, entry.sku, entry.source_kind, entry.text)
            if entry.evidence_id in existing_ids or content_key in existing_content:
                items.append(
                    AcquiredEvidenceItem(
                        source_id=source_id,
                        status=AcquisitionItemStatus.DUPLICATE,
                        entry=None,
                    )
                )
                continue
            items.append(
                AcquiredEvidenceItem(
                    source_id=source_id,
                    status=AcquisitionItemStatus.ACQUIRED,
                    entry=entry,
                )
            )
            existing_ids.add(entry.evidence_id)
            existing_content.add(content_key)
        return AcquisitionBatch(items=tuple(items), provider_calls=provider_calls)
