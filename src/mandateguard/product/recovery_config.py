"""Server-owned trusted evidence registry for the Commerce Lab recovery demo.

Every registered merchant declares exactly two authoritative scopes: one
`MERCHANT_GLOBAL` manifest and one `SKU_SPECIFIC` manifest per SKU. That is the
only partition `_scope_entries` can enforce, so two sources never share a scope
and no source can make another permanently `SOURCE_INCOMPLETE`.

Each manifest lists its complete record set, including the records that already
informed the initial `REVIEW`. Recovery therefore adds authoritative evidence
without withdrawing anything the first decision saw, which is what keeps the
initial-evidence monotonicity check satisfiable at the product default policy.

Records of `RECURRENCE` or `EXCLUSION` manifests carry normalized claim
metadata in the `billing.` and `content.` namespaces. `UNESTABLISHED` is an
explicit declaration that the record asserts nothing, not an absence of
metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mandateguard.recovery import (
    CLAIM_VALUE_UNESTABLISHED,
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


EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

_UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY = TrustedEvidenceClaim(
    "purpose.individual-study", CLAIM_VALUE_UNESTABLISHED
)
_UNESTABLISHED_PURPOSE_PROFESSIONAL = TrustedEvidenceClaim(
    "purpose.professional-development", CLAIM_VALUE_UNESTABLISHED
)
_UNESTABLISHED_BILLING = TrustedEvidenceClaim(
    "billing.model", CLAIM_VALUE_UNESTABLISHED
)
_UNESTABLISHED_GAMBLING = TrustedEvidenceClaim(
    "content.gambling", CLAIM_VALUE_UNESTABLISHED
)
_UNESTABLISHED_SUBSCRIPTION = TrustedEvidenceClaim(
    "content.subscription", CLAIM_VALUE_UNESTABLISHED
)


def _record(
    evidence_id: str,
    entry_sha256: str,
    claims: tuple[TrustedEvidenceClaim, ...],
) -> TrustedEvidenceRecord:
    return TrustedEvidenceRecord(
        evidence_id=evidence_id,
        expected_entry_sha256=entry_sha256,
        effective_at=EFFECTIVE_AT,
        claims=claims,
    )


def _source(
    *,
    source_id: str,
    display_name: str,
    merchant_id: str,
    scope_type: EvidenceScope,
    sku: str | None,
    kinds: tuple[EvidenceKind, ...],
    records: tuple[TrustedEvidenceRecord, ...],
    manifest_version: str,
) -> TrustedEvidenceSource:
    return TrustedEvidenceSource(
        source_id=source_id,
        display_name=display_name,
        manifest=TrustedEvidenceManifest(
            manifest_id=f"{source_id}:manifest:{manifest_version}",
            source_id=source_id,
            merchant_id=merchant_id,
            scope_type=scope_type,
            sku=sku,
            evidence_kinds=kinds,
            manifest_version=manifest_version,
            effective_at=EFFECTIVE_AT,
            expires_at=None,
            records=records,
        ),
    )


def _merchant_terms_source(
    *,
    source_id: str,
    merchant_id: str,
    evidence_id: str,
    entry_sha256: str,
    claims: tuple[TrustedEvidenceClaim, ...],
) -> TrustedEvidenceSource:
    """Merchant-global terms declare purpose scope only.

    These records defer the billing model and the content classification to the
    product record, so they never enter a `RECURRENCE` or `EXCLUSION` conflict
    scope and cannot compete with the SKU manifest for those facts.
    """

    return _source(
        source_id=source_id,
        display_name="Merchant Terms",
        merchant_id=merchant_id,
        scope_type=EvidenceScope.MERCHANT_GLOBAL,
        sku=None,
        kinds=(EvidenceKind.PURPOSE,),
        records=(_record(evidence_id, entry_sha256, claims),),
        manifest_version="1",
    )


def build_recovery_registry(repository_root: Path) -> TrustedEvidenceSourceRegistry:
    """Bind fixed source IDs and hashes to PSP-controlled fixture providers."""

    fixture_root = repository_root / "fixtures" / "recovery"
    providers = SemanticEvidenceProviderRegistry(
        {
            merchant_id: FixtureSemanticEvidenceProvider(
                fixture_root / f"{merchant_id}.json"
            )
            for merchant_id in (
                "merchant-scholarly",
                "merchant-academy",
                "merchant-nova",
                "merchant-lumen",
                "merchant-veritas",
            )
        }
    )

    sources = (
        _merchant_terms_source(
            source_id="scholarly-merchant-terms-v1",
            merchant_id="merchant-scholarly",
            evidence_id="scholarly-terms-v1",
            entry_sha256=(
                "5f8de2113a9950d63b3d5de5f55e105808df5b324acc8964982f7aafa2c26acf"
            ),
            claims=(_UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY,),
        ),
        _source(
            source_id="studyglow-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-scholarly",
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku="studyglow-desk-lamp",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            records=(
                _record(
                    "studyglow-evidence-v1",
                    "cb7ff30f7f0eae01eb7f9511610980a6c4d5e06e398fe37023a32ae1c2e8fdb4",
                    (
                        TrustedEvidenceClaim("purpose.individual-study", "SUPPORTED"),
                        TrustedEvidenceClaim("billing.model", "ONE_TIME"),
                    ),
                ),
                _record(
                    "studyglow-sku-terms-v2",
                    "c2dba8edfbe610c3d5acf05a967430d08ad38642db7c84dab9b171381040e0a8",
                    (
                        TrustedEvidenceClaim("purpose.individual-study", "SUPPORTED"),
                        TrustedEvidenceClaim("billing.model", "ONE_TIME"),
                    ),
                ),
            ),
            manifest_version="2",
        ),
        _merchant_terms_source(
            source_id="academy-merchant-terms-v1",
            merchant_id="merchant-academy",
            evidence_id="academy-terms-v1",
            entry_sha256=(
                "017af6e380e912f6754ec1512a7dd00d147627b22f5903846b8d91ec4db4ce3c"
            ),
            claims=(_UNESTABLISHED_PURPOSE_PROFESSIONAL,),
        ),
        _source(
            source_id="market-edge-syllabus-v2",
            display_name="Merchant Course Syllabus",
            merchant_id="merchant-academy",
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku="market-edge-course",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.EXCLUSION),
            records=(
                _record(
                    "market-edge-evidence-v1",
                    "5ca555a613819941a3d676c5a7779955077a6fbee3b380798644182de1b9dcd6",
                    (
                        TrustedEvidenceClaim(
                            "purpose.professional-development", "SUPPORTED"
                        ),
                        TrustedEvidenceClaim("content.gambling", "PRESENT"),
                    ),
                ),
                _record(
                    "market-edge-syllabus-v2",
                    "30ba6054d0f29c4cf3c95949533ec31c741d8071006ba52cc472141de131e8bb",
                    (
                        TrustedEvidenceClaim(
                            "purpose.professional-development", "SUPPORTED"
                        ),
                        TrustedEvidenceClaim("content.gambling", "PRESENT"),
                    ),
                ),
            ),
            manifest_version="2",
        ),
        _merchant_terms_source(
            source_id="nova-merchant-terms-v1",
            merchant_id="merchant-nova",
            evidence_id="nova-terms-v1",
            entry_sha256=(
                "4865d1aaf9422664b78f7dbb94cdeaeb7c3150326e9cd7475a017874bc752956"
            ),
            claims=(_UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY,),
        ),
        _source(
            source_id="flexi-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-nova",
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku="flexi-desk-companion",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            records=(
                _record(
                    "flexi-evidence-v1",
                    "cb6da6616c7bdadbc39497d98c879587e1740f35cc60807bc6ef90b5cfedc033",
                    (
                        _UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY,
                        _UNESTABLISHED_BILLING,
                    ),
                ),
                _record(
                    "flexi-sku-terms-v2",
                    "e5b257cd1746a77ba569ba9e828142eae8592b7ffc7cf04a29cc575849d6d5e8",
                    (
                        _UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY,
                        _UNESTABLISHED_BILLING,
                    ),
                ),
            ),
            manifest_version="2",
        ),
        _merchant_terms_source(
            source_id="lumen-merchant-terms-v1",
            merchant_id="merchant-lumen",
            evidence_id="lumen-terms-v1",
            entry_sha256=(
                "3816896d65125db89c9a5e4302f63d23ff4bee1349a6b488262c6b4b6a8fa6b5"
            ),
            claims=(_UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY,),
        ),
        _source(
            source_id="aurora-sku-terms-v2",
            display_name="Merchant SKU Terms",
            merchant_id="merchant-lumen",
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku="aurora-focus-lamp",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
            records=(
                _record(
                    "aurora-listing-v1",
                    "7a7fc7facaa0586ad305af8197d0a0d1098641042a978bdd35d97f7fb71250cd",
                    (
                        _UNESTABLISHED_PURPOSE_INDIVIDUAL_STUDY,
                        _UNESTABLISHED_BILLING,
                        _UNESTABLISHED_SUBSCRIPTION,
                    ),
                ),
                _record(
                    "aurora-sku-terms-v2",
                    "164310171744db76c375fd6ca1139c3fa3cadb8a12851fb27ba67da772bac86c",
                    (
                        TrustedEvidenceClaim("purpose.individual-study", "SUPPORTED"),
                        TrustedEvidenceClaim("billing.model", "ONE_TIME"),
                        TrustedEvidenceClaim("content.subscription", "ABSENT"),
                    ),
                ),
            ),
            manifest_version="2",
        ),
        _merchant_terms_source(
            source_id="veritas-merchant-terms-v1",
            merchant_id="merchant-veritas",
            evidence_id="veritas-terms-v1",
            entry_sha256=(
                "32c5a32f3ff3716d7ac00d847f1c8c74de3a3d6410251f123fe093d1d68d44fd"
            ),
            claims=(_UNESTABLISHED_PURPOSE_PROFESSIONAL,),
        ),
        _source(
            source_id="signal-edge-syllabus-v2",
            display_name="Merchant Course Syllabus",
            merchant_id="merchant-veritas",
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku="signal-edge-workshop",
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.EXCLUSION),
            records=(
                _record(
                    "signal-edge-listing-v1",
                    "920be42522dd8d743d82253115337f6ab404c9098a3b8820fde01ba90ea6272e",
                    (
                        _UNESTABLISHED_PURPOSE_PROFESSIONAL,
                        _UNESTABLISHED_GAMBLING,
                    ),
                ),
                _record(
                    "signal-edge-syllabus-v2",
                    "bb6d0d6b7a26601b381463b06e749142c3ddd6dece9b4274c6517e67efc6bdcc",
                    (
                        TrustedEvidenceClaim(
                            "purpose.professional-development", "SUPPORTED"
                        ),
                        TrustedEvidenceClaim("content.gambling", "PRESENT"),
                    ),
                ),
            ),
            manifest_version="2",
        ),
    )
    return TrustedEvidenceSourceRegistry(sources=sources, providers=providers)
