from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from pathlib import Path

import pytest

from mandateguard.semantic.evidence import (
    FixtureSemanticEvidenceProvider,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    SemanticEvidenceProviderFailureError,
    SemanticEvidenceProviderRegistry,
    SemanticEvidenceSourceInvalidError,
    acquire_semantic_evidence,
    semantic_evidence_sha256,
)
from tests.semantic_factories import make_semantic_bundle


VALID_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "semantic"
    / "merchant-1.json"
)


def _registry(path: Path = VALID_FIXTURE) -> SemanticEvidenceProviderRegistry:
    return SemanticEvidenceProviderRegistry(
        {"merchant-1": FixtureSemanticEvidenceProvider(path)}
    )


def test_fixture_provider_loads_psp_controlled_immutable_bundle() -> None:
    evidence = acquire_semantic_evidence(_registry(), "merchant-1")

    assert evidence.bundle.merchant_id == "merchant-1"
    assert tuple(entry.evidence_id for entry in evidence.bundle.entries) == (
        "merchant-terms-v1",
        "sku-1-description-v1",
        "sku-2-description-v1",
    )
    assert evidence.semantic_evidence_sha256 == semantic_evidence_sha256(evidence.bundle)


def test_bundle_order_is_canonical_for_hashing() -> None:
    bundle = make_semantic_bundle()
    reversed_bundle = SemanticEvidenceBundle(
        merchant_id=bundle.merchant_id,
        entries=tuple(reversed(bundle.entries)),
    )

    assert reversed_bundle == bundle
    assert semantic_evidence_sha256(reversed_bundle) == semantic_evidence_sha256(bundle)


def test_bundle_rejects_duplicate_ids_and_ambiguous_duplicate_content() -> None:
    entry = SemanticEvidenceEntry(
        evidence_id="one",
        merchant_id="merchant-1",
        sku="sku-1",
        source_kind="product_description",
        text="One-time study guide.",
    )
    with pytest.raises(ValueError, match="IDs"):
        SemanticEvidenceBundle(merchant_id="merchant-1", entries=(entry, entry))
    with pytest.raises(ValueError, match="ambiguous"):
        SemanticEvidenceBundle(
            merchant_id="merchant-1",
            entries=(
                entry,
                SemanticEvidenceEntry(
                    evidence_id="two",
                    merchant_id=entry.merchant_id,
                    sku=entry.sku,
                    source_kind=entry.source_kind,
                    text=entry.text,
                ),
            ),
        )


def test_bundle_rejects_merchant_identity_mismatch() -> None:
    entry = SemanticEvidenceEntry(
        evidence_id="one",
        merchant_id="merchant-2",
        sku=None,
        source_kind="merchant_terms",
        text="One-time orders.",
    )
    with pytest.raises(ValueError, match="bundle merchant"):
        SemanticEvidenceBundle(merchant_id="merchant-1", entries=(entry,))


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda value: value.update(extra="unexpected"), id="unknown-field"),
        pytest.param(lambda value: value["entries"][0].update(extra="unexpected"), id="unknown-entry-field"),
        pytest.param(lambda value: value["entries"][0].update(text=1.5), id="float"),
        pytest.param(lambda value: value["entries"][0].update(merchant_id="merchant-2"), id="merchant-mismatch"),
    ],
)
def test_fixture_rejects_malformed_fields(tmp_path, mutation) -> None:
    value = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    mutation(value)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SemanticEvidenceSourceInvalidError, match="malformed"):
        acquire_semantic_evidence(_registry(path), "merchant-1")


def test_fixture_rejects_duplicate_json_keys(tmp_path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"merchant_id":"merchant-1","merchant_id":"merchant-2","entries":[]}',
        encoding="utf-8",
    )
    with pytest.raises(SemanticEvidenceSourceInvalidError, match="malformed"):
        acquire_semantic_evidence(_registry(path), "merchant-1")


def test_buyer_cannot_select_path_provider_description_or_source_kind() -> None:
    assert tuple(inspect.signature(acquire_semantic_evidence).parameters) == (
        "registry",
        "merchant_id",
    )
    for field, value in (
        ("fixture_path", Path("buyer.json")),
        ("provider", object()),
        ("text", "buyer description"),
        ("source_kind", "buyer_kind"),
    ):
        with pytest.raises(TypeError, match="unexpected keyword"):
            acquire_semantic_evidence(_registry(), "merchant-1", **{field: value})


@dataclass
class CountingProvider:
    calls: int = 0

    def fetch_semantic_evidence(self, *, merchant_id: str) -> SemanticEvidenceBundle:
        self.calls += 1
        return make_semantic_bundle()


def test_acquisition_fetches_once_and_sku_only_selects_within_bundle() -> None:
    provider = CountingProvider()
    evidence = acquire_semantic_evidence(
        SemanticEvidenceProviderRegistry({"merchant-1": provider}), "merchant-1"
    )
    selected = evidence.bundle.relevant_to_skus(("../../another-source.json",))

    assert provider.calls == 1
    assert tuple(entry.evidence_id for entry in selected) == ("terms-v1",)


class InvalidProvider:
    def fetch_semantic_evidence(self, *, merchant_id: str):
        return {"merchant_id": merchant_id}


def test_provider_must_return_complete_typed_bundle() -> None:
    registry = SemanticEvidenceProviderRegistry({"merchant-1": InvalidProvider()})
    with pytest.raises(SemanticEvidenceProviderFailureError, match="invalid bundle type"):
        acquire_semantic_evidence(registry, "merchant-1")
