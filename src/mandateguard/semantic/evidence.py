"""Trusted, immutable semantic evidence acquisition for Tier C."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from mandateguard.core.hashing import sha256_canonical


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BUNDLE_FIELDS = frozenset({"merchant_id", "entries"})
_ENTRY_FIELDS = frozenset(
    {"evidence_id", "merchant_id", "sku", "source_kind", "text"}
)


class SemanticEvidenceAcquisitionError(RuntimeError):
    """Trusted semantic evidence could not be acquired."""


class SemanticEvidenceProviderNotConfiguredError(SemanticEvidenceAcquisitionError):
    """No PSP-controlled semantic evidence provider is registered."""


class SemanticEvidenceSourceUnavailableError(SemanticEvidenceAcquisitionError):
    """The configured semantic evidence source could not be read."""


class SemanticEvidenceSourceInvalidError(SemanticEvidenceAcquisitionError):
    """The configured semantic evidence source is malformed or inconsistent."""


class SemanticEvidenceProviderFailureError(SemanticEvidenceAcquisitionError):
    """The configured provider failed without returning a complete bundle."""


def _require_nonempty_string(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(
            f"{name} must be a non-empty string of at most {maximum} characters"
        )


def _validate_merchant_id(value: object) -> None:
    _require_nonempty_string(value, "merchant_id", 128)


@dataclass(frozen=True, slots=True)
class SemanticEvidenceEntry:
    """One PSP-sourced merchant or product text treated only as evidence data."""

    evidence_id: str
    merchant_id: str
    sku: str | None
    source_kind: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not _IDENTIFIER_RE.fullmatch(
            self.evidence_id
        ):
            raise ValueError("evidence_id must be a bounded identifier")
        _validate_merchant_id(self.merchant_id)
        if self.sku is not None:
            _require_nonempty_string(self.sku, "sku", 128)
        if not isinstance(self.source_kind, str) or not _SOURCE_KIND_RE.fullmatch(
            self.source_kind
        ):
            raise ValueError("source_kind must be a generic lowercase identifier")
        _require_nonempty_string(self.text, "text", 20_000)


def _entry_sort_key(entry: SemanticEvidenceEntry) -> tuple[bool, str, str, str]:
    return (
        entry.sku is not None,
        entry.sku or "",
        entry.source_kind,
        entry.evidence_id,
    )


@dataclass(frozen=True, slots=True)
class SemanticEvidenceBundle:
    """Complete semantic evidence registered for one merchant."""

    merchant_id: str
    entries: tuple[SemanticEvidenceEntry, ...]

    def __post_init__(self) -> None:
        _validate_merchant_id(self.merchant_id)
        if not isinstance(self.entries, tuple) or not self.entries:
            raise ValueError("entries must be a non-empty tuple")
        if not all(isinstance(entry, SemanticEvidenceEntry) for entry in self.entries):
            raise ValueError("entries contains an invalid semantic evidence entry")
        if any(entry.merchant_id != self.merchant_id for entry in self.entries):
            raise ValueError("every evidence entry must belong to the bundle merchant")

        evidence_ids = [entry.evidence_id for entry in self.entries]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique within a bundle")
        content_keys = [
            (entry.sku, entry.source_kind, entry.text) for entry in self.entries
        ]
        if len(content_keys) != len(set(content_keys)):
            raise ValueError("duplicate ambiguous semantic evidence entry")
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=_entry_sort_key)))

    def relevant_to_skus(self, skus: tuple[str, ...]) -> tuple[SemanticEvidenceEntry, ...]:
        """Select global and matching-SKU entries without interpreting source_kind."""

        if not isinstance(skus, tuple) or not all(isinstance(sku, str) for sku in skus):
            raise TypeError("skus must be a tuple of strings")
        selected_skus = frozenset(skus)
        return tuple(
            entry
            for entry in self.entries
            if entry.sku is None or entry.sku in selected_skus
        )


def semantic_evidence_sha256(bundle: SemanticEvidenceBundle) -> str:
    """Commit a canonically ordered semantic evidence bundle."""

    if not isinstance(bundle, SemanticEvidenceBundle):
        raise TypeError("bundle must be SemanticEvidenceBundle")
    return sha256_canonical(bundle)


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    """One exact acquired bundle and its immediate PSP-side commitment."""

    bundle: SemanticEvidenceBundle
    semantic_evidence_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, SemanticEvidenceBundle):
            raise TypeError("bundle must be SemanticEvidenceBundle")
        if semantic_evidence_sha256(self.bundle) != self.semantic_evidence_sha256:
            raise ValueError("semantic_evidence_sha256 does not commit bundle")


@runtime_checkable
class SemanticEvidenceProvider(Protocol):
    """Fetch evidence using only PSP-controlled source configuration."""

    def fetch_semantic_evidence(
        self, *, merchant_id: str
    ) -> SemanticEvidenceBundle:
        """Return the complete registered bundle for one merchant."""


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_float(value: str) -> None:
    raise ValueError(f"floating-point values are not allowed: {value}")


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _require_exact_fields(
    value: object, *, expected: frozenset[str], location: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a JSON object")
    actual = frozenset(value)
    if actual != expected:
        missing = ",".join(sorted(expected - actual)) or "none"
        unknown = ",".join(sorted(actual - expected)) or "none"
        raise ValueError(
            f"{location} has invalid fields (missing={missing}; unknown={unknown})"
        )
    return value


def _decode_semantic_evidence_fixture(raw: str) -> SemanticEvidenceBundle:
    decoded = json.loads(
        raw,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_float=_reject_float,
        parse_constant=_reject_non_json_number,
    )
    bundle = _require_exact_fields(
        decoded, expected=_BUNDLE_FIELDS, location="semantic_evidence"
    )
    raw_entries = bundle["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("semantic_evidence.entries must be a non-empty JSON array")
    entries: list[SemanticEvidenceEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        entry = _require_exact_fields(
            raw_entry,
            expected=_ENTRY_FIELDS,
            location=f"semantic_evidence.entries[{index}]",
        )
        entries.append(
            SemanticEvidenceEntry(
                evidence_id=entry["evidence_id"],
                merchant_id=entry["merchant_id"],
                sku=entry["sku"],
                source_kind=entry["source_kind"],
                text=entry["text"],
            )
        )
    return SemanticEvidenceBundle(
        merchant_id=bundle["merchant_id"], entries=tuple(entries)
    )


def load_semantic_evidence_fixture(fixture_path: Path) -> SemanticEvidenceBundle:
    """Read one complete bundle from a trusted PSP-configured fixture."""

    if not isinstance(fixture_path, Path):
        raise TypeError("fixture_path must be pathlib.Path PSP configuration")
    try:
        raw = fixture_path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise SemanticEvidenceSourceInvalidError(
            "configured semantic evidence fixture is malformed"
        ) from exc
    except OSError as exc:
        raise SemanticEvidenceSourceUnavailableError(
            "configured semantic evidence fixture is unavailable"
        ) from exc
    try:
        return _decode_semantic_evidence_fixture(raw)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SemanticEvidenceSourceInvalidError(
            "configured semantic evidence fixture is malformed"
        ) from exc


@dataclass(frozen=True, slots=True)
class FixtureSemanticEvidenceProvider:
    """Prototype provider backed by a trusted PSP-side fixture path."""

    fixture_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_path, Path):
            raise TypeError("fixture_path must be pathlib.Path PSP configuration")

    def fetch_semantic_evidence(
        self, *, merchant_id: str
    ) -> SemanticEvidenceBundle:
        try:
            _validate_merchant_id(merchant_id)
        except ValueError as exc:
            raise SemanticEvidenceSourceInvalidError(
                "requested merchant identity is invalid"
            ) from exc
        bundle = load_semantic_evidence_fixture(self.fixture_path)
        if bundle.merchant_id != merchant_id:
            raise SemanticEvidenceSourceInvalidError(
                "configured semantic evidence merchant does not match the registered merchant"
            )
        return bundle


@dataclass(frozen=True, slots=True, init=False)
class SemanticEvidenceProviderRegistry:
    """Immutable PSP-side merchant-to-provider configuration."""

    _providers: Mapping[str, SemanticEvidenceProvider]

    def __init__(self, providers: Mapping[str, SemanticEvidenceProvider]) -> None:
        if not isinstance(providers, Mapping):
            raise TypeError("providers must be a mapping")
        configured: dict[str, SemanticEvidenceProvider] = {}
        for merchant_id, provider in providers.items():
            _validate_merchant_id(merchant_id)
            if not isinstance(provider, SemanticEvidenceProvider):
                raise TypeError("each provider must implement SemanticEvidenceProvider")
            configured[merchant_id] = provider
        object.__setattr__(self, "_providers", MappingProxyType(configured))

    def provider_for(
        self, *, merchant_id: str
    ) -> SemanticEvidenceProvider | None:
        _validate_merchant_id(merchant_id)
        return self._providers.get(merchant_id)


def acquire_semantic_evidence(
    registry: SemanticEvidenceProviderRegistry, merchant_id: str
) -> SemanticEvidence:
    """Fetch exactly once from the PSP-selected provider and commit the bundle."""

    if not isinstance(registry, SemanticEvidenceProviderRegistry):
        raise TypeError("registry must be SemanticEvidenceProviderRegistry")
    try:
        provider = registry.provider_for(merchant_id=merchant_id)
    except ValueError as exc:
        raise SemanticEvidenceProviderNotConfiguredError(
            "merchant identity is invalid"
        ) from exc
    if provider is None:
        raise SemanticEvidenceProviderNotConfiguredError(
            "semantic evidence provider is not configured for merchant"
        )
    try:
        bundle = provider.fetch_semantic_evidence(merchant_id=merchant_id)
    except SemanticEvidenceAcquisitionError:
        raise
    except Exception as exc:
        raise SemanticEvidenceProviderFailureError(
            "configured semantic evidence provider failed"
        ) from exc
    if not isinstance(bundle, SemanticEvidenceBundle):
        raise SemanticEvidenceProviderFailureError(
            "configured semantic evidence provider returned an invalid bundle type"
        )
    if bundle.merchant_id != merchant_id:
        raise SemanticEvidenceSourceInvalidError(
            "provider evidence merchant does not match the registered merchant"
        )
    return SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )
