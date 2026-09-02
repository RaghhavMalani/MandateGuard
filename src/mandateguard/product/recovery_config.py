"""Server-owned trusted evidence registry for the Commerce Lab recovery demo."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
    SemanticEvidenceProviderRegistry,
)


def build_recovery_registry(repository_root: Path) -> TrustedEvidenceSourceRegistry:
    """Bind fixed source IDs and hashes to PSP-controlled fixture providers."""

    fixture_root = repository_root / "fixtures" / "recovery"
    providers = SemanticEvidenceProviderRegistry(
        {
            "merchant-scholarly": FixtureSemanticEvidenceProvider(
                fixture_root / "merchant-scholarly.json"
            ),
            "merchant-academy": FixtureSemanticEvidenceProvider(
                fixture_root / "merchant-academy.json"
            ),
            "merchant-nova": FixtureSemanticEvidenceProvider(
                fixture_root / "merchant-nova.json"
            ),
        }
    )
    effective_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def source(
        *,
        source_id: str,
        display_name: str,
        merchant_id: str,
        sku: str,
        kinds: tuple[EvidenceKind, ...],
        entry_sha256: str,
        claims: tuple[TrustedEvidenceClaim, ...],
    ) -> TrustedEvidenceSource:
        return TrustedEvidenceSource(
            source_id=source_id,
            display_name=display_name,
            manifest=TrustedEvidenceManifest(
                manifest_id=f"{source_id}:manifest:2",
                source_id=source_id,
                merchant_id=merchant_id,
                scope_type=EvidenceScope.SKU_SPECIFIC,
                sku=sku,
                evidence_kinds=kinds,
                manifest_version="2",
                effective_at=effective_at,
                expires_at=None,
                records=(
                    TrustedEvidenceRecord(
                        evidence_id=source_id,
                        expected_entry_sha256=entry_sha256,
                        effective_at=effective_at,
                        claims=claims,
                    ),
                ),
            ),
        )

    sources = (
        source(
            source_id="studyglow-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-scholarly",
            sku="studyglow-desk-lamp",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            entry_sha256=(
                "c2dba8edfbe610c3d5acf05a967430d08ad38642db7c84dab9b171381040e0a8"
            ),
            claims=(
                TrustedEvidenceClaim("purpose.individual-study", "SUPPORTED"),
                TrustedEvidenceClaim("billing.model", "ONE_TIME"),
            ),
        ),
        source(
            source_id="market-edge-syllabus-v2",
            display_name="Merchant Course Syllabus",
            merchant_id="merchant-academy",
            sku="market-edge-course",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.EXCLUSION),
            entry_sha256=(
                "ae4c98bd51f463a13bd88459188f99adda74dd0d11856fa26af4df72a0053b6b"
            ),
            claims=(
                TrustedEvidenceClaim("purpose.professional-development", "SUPPORTED"),
                TrustedEvidenceClaim("content.gambling", "PRESENT"),
            ),
        ),
        source(
            source_id="flexi-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-nova",
            sku="flexi-desk-companion",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            entry_sha256=(
                "e5b257cd1746a77ba569ba9e828142eae8592b7ffc7cf04a29cc575849d6d5e8"
            ),
            claims=(
                TrustedEvidenceClaim("purpose.individual-study", "UNESTABLISHED"),
                TrustedEvidenceClaim("billing.model", "UNESTABLISHED"),
            ),
        ),
    )
    return TrustedEvidenceSourceRegistry(sources=sources, providers=providers)
