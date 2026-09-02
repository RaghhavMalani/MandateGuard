"""Load the 20 frozen Resolve evaluation worlds into typed policy inputs.

Each world file is one independently authored merchant/SKU/transaction world.
Loading is deterministic and offline: it decodes JSON, constructs the frozen
mandate, transaction, catalogue, initial evidence, and trusted source
manifests, and re-derives every manifested record hash from the evidence
fixture. It never authorizes, acquires, or calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    sha256_canonical,
    transaction_body_sha256,
)
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
    SemanticConstraint,
    SemanticConstraintFamily,
)
from mandateguard.models.transaction import (
    Transaction,
    TransactionLine,
    TransactionPayload,
)
from mandateguard.recovery import (
    EvidenceKind,
    EvidenceScope,
    TrustedEvidenceClaim,
    TrustedEvidenceManifest,
    TrustedEvidenceRecord,
    TrustedEvidenceSource,
    TrustedEvidenceSourceRegistry,
)
from mandateguard.semantic.evidence import (
    FixtureSemanticEvidenceProvider,
    SemanticEvidence,
    SemanticEvidenceBundle,
    SemanticEvidenceProviderRegistry,
    load_semantic_evidence_fixture,
    semantic_evidence_sha256,
)


WORLD_SCHEMA = "RESOLVE_EVALUATION_WORLD_V1"
FIXTURE_ROOT = Path("fixtures") / "engineering" / "resolve_recovery"
EXPECTED_CASE_COUNT = 20


class WorldFixtureError(RuntimeError):
    """A frozen evaluation world fixture is missing, malformed, or unbound."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise WorldFixtureError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_float(value: str) -> float:
    raise WorldFixtureError(f"floating-point values are not allowed: {value}")


def read_strict_json(path: Path) -> Any:
    """Decode one fixture without duplicate keys, floats, or JSON constants."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorldFixtureError(f"fixture is unavailable: {path}") from error
    try:
        return json.loads(
            raw,
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except ValueError as error:
        raise WorldFixtureError(f"fixture is malformed: {path}") from error


def _field(mapping: Mapping[str, Any], name: str, path: Path) -> Any:
    if not isinstance(mapping, Mapping) or name not in mapping:
        raise WorldFixtureError(f"{path.name} is missing the field {name!r}")
    return mapping[name]


def _aware(value: Any, name: str, path: Path) -> datetime:
    if not isinstance(value, str):
        raise WorldFixtureError(f"{path.name}.{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise WorldFixtureError(f"{path.name}.{name} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorldFixtureError(f"{path.name}.{name} must be timezone-aware")
    return parsed


def _optional_aware(value: Any, name: str, path: Path) -> datetime | None:
    return None if value is None else _aware(value, name, path)


@dataclass(frozen=True, slots=True)
class BindingProbe:
    """A source deliberately offered against the wrong transaction identity."""

    source_id: str
    probe: str
    expected_status: str


@dataclass(frozen=True, slots=True)
class ProviderFault:
    """A preregistered deterministic trusted-provider outage."""

    mode: str
    merchant_id: str
    raises: str


@dataclass(frozen=True, slots=True)
class ResolveCaseWorld:
    """One independently authored merchant/SKU/transaction world."""

    case_id: str
    case_family: str
    merchant_id: str
    sku: str
    currency: str
    amount_minor: int
    mandate: Mandate
    transaction: Transaction
    catalog_snapshot: CatalogSnapshot
    initial_evidence: SemanticEvidence
    initial_evidence_ids: tuple[str, ...]
    sources: tuple[TrustedEvidenceSource, ...]
    evidence_fixtures: Mapping[str, Path]
    binding_probes: tuple[BindingProbe, ...]
    provider_fault: ProviderFault | None
    world_path: Path

    @property
    def committed_hashes(self) -> CommittedHashes:
        return CommittedHashes(
            transaction_sha256=transaction_body_sha256(self.transaction),
            catalog_snapshot_sha256=catalog_snapshot_sha256(self.catalog_snapshot),
        )


def _build_mandate(raw: Mapping[str, Any], path: Path) -> Mandate:
    hard = _field(raw, "hard", path)
    attestation = _field(raw, "issuer_attestation", path)
    semantic = tuple(
        SemanticConstraint(
            constraint_id=item["constraint_id"],
            kind=item["kind"],
            text=item["text"],
            constraint_family=SemanticConstraintFamily(item["constraint_family"]),
        )
        for item in _field(raw, "semantic", path)
    )
    return Mandate(
        payload=MandatePayload(
            mandate_id=_field(raw, "mandate_id", path),
            nonce=_field(raw, "nonce", path),
            issued_at=_aware(_field(raw, "issued_at", path), "issued_at", path),
            expires_at=_aware(_field(raw, "expires_at", path), "expires_at", path),
            subject_ref=_field(raw, "subject_ref", path),
            currency=_field(raw, "currency", path),
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=hard["max_total_minor"],
                    max_quantity=hard["max_quantity"],
                    recurring_allowed=hard["recurring_allowed"],
                    merchant_allowlist=tuple(hard["merchant_allowlist"]),
                    sku_allowlist=tuple(hard["sku_allowlist"]),
                ),
                semantic=semantic,
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance=attestation["assurance"], issuer_id=attestation["issuer_id"]
        ),
    )


def _build_catalog(raw: Mapping[str, Any], path: Path) -> CatalogSnapshot:
    return CatalogSnapshot(
        snapshot_id=_field(raw, "snapshot_id", path),
        merchant_id=_field(raw, "merchant_id", path),
        currency=_field(raw, "currency", path),
        items=tuple(
            CatalogItem(
                sku=item["sku"],
                merchant_id=item["merchant_id"],
                effective_unit_price_minor=item["effective_unit_price_minor"],
                recurring=item["recurring"],
            )
            for item in _field(raw, "items", path)
        ),
    )


def _build_transaction(raw: Mapping[str, Any], path: Path) -> Transaction:
    lines = _field(raw, "lines", path)
    payload = TransactionPayload(
        transaction_id=_field(raw, "transaction_id", path),
        merchant_id=_field(raw, "merchant_id", path),
        cart_currency=_field(raw, "cart_currency", path),
        order_currency=_field(raw, "order_currency", path),
        declared_order_total_minor=sum(line["line_total_minor"] for line in lines),
        declared_aggregate_quantity=sum(line["quantity"] for line in lines),
        cart_recurring=_field(raw, "cart_recurring", path),
        order_recurring=_field(raw, "order_recurring", path),
        lines=tuple(
            TransactionLine(
                sku=line["sku"],
                effective_unit_price_minor=line["effective_unit_price_minor"],
                quantity=line["quantity"],
                line_total_minor=line["line_total_minor"],
                recurring=line["recurring"],
            )
            for line in lines
        ),
    )
    return Transaction(
        payload=payload, declared_transaction_hash=transaction_body_sha256(payload)
    )


def _build_sources(
    raw_sources: Any, *, entry_hashes: Mapping[str, str], path: Path
) -> tuple[TrustedEvidenceSource, ...]:
    sources: list[TrustedEvidenceSource] = []
    for raw in raw_sources:
        records: list[TrustedEvidenceRecord] = []
        for item in _field(raw, "records", path):
            evidence_id = item["evidence_id"]
            declared = item["expected_entry_sha256"]
            actual = entry_hashes.get(evidence_id)
            if actual is None:
                raise WorldFixtureError(
                    f"{path.name}: manifest record {evidence_id!r} has no evidence entry"
                )
            if declared != actual:
                raise WorldFixtureError(
                    f"{path.name}: manifest record {evidence_id!r} hash does not "
                    "match the frozen evidence fixture"
                )
            records.append(
                TrustedEvidenceRecord(
                    evidence_id=evidence_id,
                    expected_entry_sha256=declared,
                    effective_at=_aware(item["effective_at"], "effective_at", path),
                    expires_at=_optional_aware(item["expires_at"], "expires_at", path),
                    supersedes_evidence_id=item["supersedes_evidence_id"],
                    claims=tuple(
                        TrustedEvidenceClaim(claim["claim_id"], claim["claim_value"])
                        for claim in item["claims"]
                    ),
                )
            )
        manifest = TrustedEvidenceManifest(
            manifest_id=_field(raw, "manifest_id", path),
            source_id=_field(raw, "source_id", path),
            merchant_id=_field(raw, "merchant_id", path),
            scope_type=EvidenceScope(_field(raw, "scope_type", path)),
            sku=_field(raw, "sku", path),
            evidence_kinds=tuple(
                EvidenceKind(kind) for kind in _field(raw, "evidence_kinds", path)
            ),
            manifest_version=_field(raw, "manifest_version", path),
            effective_at=_aware(_field(raw, "effective_at", path), "effective_at", path),
            expires_at=_optional_aware(
                _field(raw, "expires_at", path), "expires_at", path
            ),
            records=tuple(records),
            supersedes_manifest_id=_field(raw, "supersedes_manifest_id", path),
        )
        sources.append(
            TrustedEvidenceSource(
                source_id=manifest.source_id,
                display_name=_field(raw, "display_name", path),
                manifest=manifest,
            )
        )
    return tuple(sources)


def load_world(path: Path, *, repository_root: Path) -> ResolveCaseWorld:
    """Load and structurally validate one frozen evaluation world."""

    raw = read_strict_json(path)
    if _field(raw, "schema", path) != WORLD_SCHEMA:
        raise WorldFixtureError(f"{path.name} is not a {WORLD_SCHEMA} world")

    evidence_fixtures: dict[str, Path] = {}
    entry_hashes: dict[str, str] = {}
    bundles: dict[str, SemanticEvidenceBundle] = {}
    for item in _field(raw, "evidence_fixtures", path):
        fixture_path = repository_root / item["path"]
        if not fixture_path.is_file():
            raise WorldFixtureError(f"evidence fixture is missing: {item['path']}")
        bundle = load_semantic_evidence_fixture(fixture_path)
        if bundle.merchant_id != item["merchant_id"]:
            raise WorldFixtureError(
                f"{path.name}: evidence fixture merchant does not match its binding"
            )
        evidence_fixtures[bundle.merchant_id] = fixture_path
        bundles[bundle.merchant_id] = bundle
        for entry in bundle.entries:
            if entry.evidence_id in entry_hashes:
                raise WorldFixtureError(
                    f"{path.name}: evidence id {entry.evidence_id!r} is not unique"
                )
            entry_hashes[entry.evidence_id] = sha256_canonical(entry)

    merchant_id = _field(raw, "merchant_id", path)
    if merchant_id not in bundles:
        raise WorldFixtureError(f"{path.name}: no evidence fixture for its merchant")
    by_id = {entry.evidence_id: entry for entry in bundles[merchant_id].entries}
    initial_ids = tuple(_field(raw, "initial_evidence_ids", path))
    missing = [evidence_id for evidence_id in initial_ids if evidence_id not in by_id]
    if not initial_ids or missing:
        raise WorldFixtureError(
            f"{path.name}: initial evidence is empty or unbound: {missing}"
        )
    initial_bundle = SemanticEvidenceBundle(
        merchant_id=merchant_id,
        entries=tuple(by_id[evidence_id] for evidence_id in initial_ids),
    )
    initial_evidence = SemanticEvidence(
        bundle=initial_bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(initial_bundle),
    )

    fault_raw = _field(raw, "provider_fault", path)
    fault = (
        None
        if fault_raw is None
        else ProviderFault(
            mode=fault_raw["mode"],
            merchant_id=fault_raw["merchant_id"],
            raises=fault_raw["raises"],
        )
    )
    if fault is not None and fault.merchant_id not in evidence_fixtures:
        raise WorldFixtureError(f"{path.name}: provider fault names an unknown merchant")

    return ResolveCaseWorld(
        case_id=_field(raw, "case_id", path),
        case_family=_field(raw, "case_family", path),
        merchant_id=merchant_id,
        sku=_field(raw, "sku", path),
        currency=_field(raw, "currency", path),
        amount_minor=_field(raw, "amount_minor", path),
        mandate=_build_mandate(_field(raw, "mandate", path), path),
        transaction=_build_transaction(_field(raw, "transaction", path), path),
        catalog_snapshot=_build_catalog(_field(raw, "catalog", path), path),
        initial_evidence=initial_evidence,
        initial_evidence_ids=initial_ids,
        sources=_build_sources(
            _field(raw, "recovery_sources", path),
            entry_hashes=entry_hashes,
            path=path,
        ),
        evidence_fixtures=MappingProxyType(evidence_fixtures),
        binding_probes=tuple(
            BindingProbe(
                source_id=item["source_id"],
                probe=item["probe"],
                expected_status=item["expected_status"],
            )
            for item in _field(raw, "binding_probes", path)
        ),
        provider_fault=fault,
        world_path=path,
    )


def load_worlds(repository_root: Path) -> tuple[ResolveCaseWorld, ...]:
    """Load every frozen world in case-id order."""

    directory = repository_root / FIXTURE_ROOT / "worlds"
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise WorldFixtureError(f"no evaluation worlds under {directory}")
    worlds = tuple(load_world(path, repository_root=repository_root) for path in paths)
    case_ids = [world.case_id for world in worlds]
    if len(set(case_ids)) != len(case_ids):
        raise WorldFixtureError("evaluation case IDs are not unique")
    return worlds


def build_registry(
    worlds: tuple[ResolveCaseWorld, ...],
) -> TrustedEvidenceSourceRegistry:
    """Build the one shared registry every case is evaluated against.

    Every world's sources live in the same registry, so merchant and SKU
    binding is exercised against real foreign sources rather than against an
    empty configuration.
    """

    sources: list[TrustedEvidenceSource] = []
    providers: dict[str, Any] = {}
    seen: set[str] = set()
    for world in worlds:
        for source in world.sources:
            if source.source_id in seen:
                raise WorldFixtureError(
                    f"trusted source {source.source_id!r} is declared by two worlds"
                )
            seen.add(source.source_id)
            sources.append(source)
        for merchant_id, fixture_path in world.evidence_fixtures.items():
            providers.setdefault(
                merchant_id, FixtureSemanticEvidenceProvider(fixture_path)
            )
    return TrustedEvidenceSourceRegistry(
        sources=tuple(sources),
        providers=SemanticEvidenceProviderRegistry(providers),
    )


__all__ = [
    "EXPECTED_CASE_COUNT",
    "FIXTURE_ROOT",
    "WORLD_SCHEMA",
    "BindingProbe",
    "ProviderFault",
    "ResolveCaseWorld",
    "WorldFixtureError",
    "build_registry",
    "load_world",
    "load_worlds",
    "read_strict_json",
]
