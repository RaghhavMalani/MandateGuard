"""Server-owned trusted evidence registry for the Commerce Lab recovery demo."""

from __future__ import annotations

from pathlib import Path

from mandateguard.recovery import (
    EvidenceKind,
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
    sources = (
        TrustedEvidenceSource(
            source_id="studyglow-sku-terms-v2",
            evidence_id="studyglow-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-scholarly",
            sku="studyglow-desk-lamp",
            evidence_kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            expected_entry_sha256=(
                "c2dba8edfbe610c3d5acf05a967430d08ad38642db7c84dab9b171381040e0a8"
            ),
        ),
        TrustedEvidenceSource(
            source_id="market-edge-syllabus-v2",
            evidence_id="market-edge-syllabus-v2",
            display_name="Merchant Course Syllabus",
            merchant_id="merchant-academy",
            sku="market-edge-course",
            evidence_kinds=(EvidenceKind.PURPOSE, EvidenceKind.EXCLUSION),
            expected_entry_sha256=(
                "ae4c98bd51f463a13bd88459188f99adda74dd0d11856fa26af4df72a0053b6b"
            ),
        ),
        TrustedEvidenceSource(
            source_id="flexi-sku-terms-v2",
            evidence_id="flexi-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-nova",
            sku="flexi-desk-companion",
            evidence_kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            expected_entry_sha256=(
                "e5b257cd1746a77ba569ba9e828142eae8592b7ffc7cf04a29cc575849d6d5e8"
            ),
        ),
    )
    return TrustedEvidenceSourceRegistry(sources=sources, providers=providers)
