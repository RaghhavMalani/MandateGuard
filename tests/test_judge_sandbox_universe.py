"""The sandbox world: reproducible, exactly identified, and quarantined.

These tests are about the *world*, not about what the controller does in it.
They assert that the same generator produces the same world everywhere, that a
sandbox identity can never be mistaken for a registered or a crawled one, and
that trust cannot travel between the sandbox and the historical marketplace in
either direction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.intelligence.store import (
    TrustedCommerceStore,
    UnknownEvidenceError,
    UnknownProductError,
)
from mandateguard.product.discovery_service import REGISTERED_SOURCE, build_trusted_lookup
from mandateguard.sandbox.store import (
    CONFLICTED,
    DECLARED,
    NOT_DECLARED,
    SANDBOX_SNAPSHOT_ID,
    build_sandbox_store,
    readiness_for,
    scan_declarations,
)
from mandateguard.sandbox.templates import (
    CATEGORIES,
    MERCHANTS,
    PRODUCTS_PER_CATEGORY,
    SANDBOX_MERCHANT_PREFIX,
    WORLD_VERSION,
    EvidenceFamily,
)
from mandateguard.sandbox.universe import (
    build_universe,
    sandbox_catalog_id,
    universe_manifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = REPOSITORY_ROOT / "data" / "eval" / "judge-playground-v3" / "SANDBOX_FREEZE.json"


@pytest.fixture(scope="module")
def universe():
    return build_universe()


@pytest.fixture(scope="module")
def store(universe) -> TrustedCommerceStore:
    return build_sandbox_store(universe)


# ---------------------------------------------------------------- determinism


def test_two_generations_produce_the_same_world(universe) -> None:
    again = build_universe()
    assert again.products_sha256 == universe.products_sha256
    assert again.evidence_sha256 == universe.evidence_sha256
    assert len(again.products) == len(universe.products)
    for left, right in zip(again.products, universe.products):
        assert left == right


def test_generated_world_matches_the_frozen_manifest(universe) -> None:
    """A world that has drifted from its freeze is a different world.

    Regenerating the freeze is a deliberate act that accompanies a version
    change, never a way to make this assertion go quiet.
    """

    frozen = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    manifest = universe_manifest(universe)
    assert frozen["world_version"] == WORLD_VERSION
    assert frozen["products_sha256"] == manifest["products_sha256"]
    assert frozen["evidence_sha256"] == manifest["evidence_sha256"]
    assert frozen["product_count"] == manifest["product_count"]
    assert frozen["evidence_count"] == manifest["evidence_count"]


def test_world_is_the_size_the_brief_asked_for(universe) -> None:
    assert 2_000 <= len(universe.products) <= 5_000
    assert len(universe.products) == len(CATEGORIES) * PRODUCTS_PER_CATEGORY


def test_manifest_records_no_authorization_outcome(universe) -> None:
    """A manifest that mentioned a verdict would mean the world was built to one."""

    text = json.dumps(universe_manifest(universe)).upper()
    for word in ("ALLOW", "BLOCK", "REVIEW"):
        assert word not in text


# ------------------------------------------------------------------ identity


def test_every_sandbox_identity_is_unmistakably_a_sandbox_identity(universe) -> None:
    for product in universe.products:
        assert product.merchant_id.startswith(SANDBOX_MERCHANT_PREFIX)
        assert product.catalog_product_id.startswith("sandbox.")
        assert product.synthetic is True
        assert "Synthetic" in product.merchant_display_name


def test_merchant_and_sku_pairs_are_unique(universe) -> None:
    pairs = {(item.merchant_id, item.sku) for item in universe.products}
    assert len(pairs) == len(universe.products)
    ids = {item.catalog_product_id for item in universe.products}
    assert len(ids) == len(universe.products)


def test_evidence_ids_are_unique_and_bound_to_their_own_identity(universe) -> None:
    seen: set[str] = set()
    for entry in universe.evidence_entries:
        assert entry.evidence_id not in seen
        seen.add(entry.evidence_id)
        assert entry.merchant_id.startswith(SANDBOX_MERCHANT_PREFIX)


def test_sandbox_identities_cannot_collide_with_registered_fixtures(
    universe,
) -> None:
    registered = TrustedCommerceStore.from_files(
        catalog_path=REPOSITORY_ROOT / "fixtures" / "agentic_commerce" / "merchant_catalog.json",
        merchant_terms_path=REPOSITORY_ROOT
        / "fixtures"
        / "agentic_commerce"
        / "merchant_terms.json",
    )
    registered_pairs = {(item.merchant_id, item.sku) for item in registered.products}
    sandbox_pairs = {(item.merchant_id, item.sku) for item in universe.products}
    assert registered_pairs.isdisjoint(sandbox_pairs)
    registered_evidence = {item.evidence_id for item in registered.evidence_entries}
    sandbox_evidence = {item.evidence_id for item in universe.evidence_entries}
    assert registered_evidence.isdisjoint(sandbox_evidence)
    assert SANDBOX_SNAPSHOT_ID != registered.snapshot_id


def test_catalog_ids_are_derived_the_same_way_marketplace_ids_are() -> None:
    assert sandbox_catalog_id("sandbox-acme-audio", "audio-headphones-001").startswith(
        "sandbox."
    )
    # Namespaced by source, so a marketplace id and a sandbox id for the same
    # words are different identifiers rather than the same one twice.
    assert sandbox_catalog_id("sandbox-a", "x") != sandbox_catalog_id("sandbox-b", "x")


# ------------------------------------------------ evidence completeness


def test_the_generated_world_builds_a_valid_trusted_store(store) -> None:
    assert store.snapshot_id == SANDBOX_SNAPSHOT_ID
    assert len(store.products) == len(build_universe().products)


def test_every_product_resolves_its_own_evidence(universe, store) -> None:
    for product in universe.products:
        entries = store.evidence_for_product(
            merchant_id=product.merchant_id, sku=product.sku
        )
        # Merchant terms plus two product records, for every listing.
        assert len(entries) == 3
        resolved = store.resolve_evidence_ids(
            product.evidence_ids, merchant_id=product.merchant_id, sku=product.sku
        )
        assert len(resolved) == 3


def test_every_product_evidence_names_its_own_merchant_and_sku(universe, store) -> None:
    for product in universe.products[:200]:
        for entry in store.evidence_for_product(
            merchant_id=product.merchant_id, sku=product.sku
        ):
            assert entry.merchant_id == product.merchant_id
            assert entry.sku in {None, product.sku}
            assert "SYNTHETIC SANDBOX RECORD" in entry.text


def test_authoritative_price_in_evidence_matches_the_catalog_record(
    universe, store
) -> None:
    """A price the merchant published and a price the store holds must agree.

    They are two representations of one fact, and the controller checks the
    transaction against the store. Evidence that disagreed would be a lie the
    Playground was telling on the merchant's behalf.
    """

    for product in universe.products:
        entries = store.evidence_for_product(
            merchant_id=product.merchant_id, sku=product.sku
        )
        terms = next(item for item in entries if item.source_kind == "product_terms")
        rupees = f"{product.price_minor // 100:,}.{product.price_minor % 100:02d}"
        assert f"Authoritative price: {rupees} INR" in terms.text


def test_evidence_families_are_all_represented(universe) -> None:
    present = {item.evidence_family for item in universe.products}
    assert present == set(EvidenceFamily)


def test_subscription_shelf_never_claims_a_one_time_complete_plan(universe) -> None:
    subscriptions = [
        item for item in universe.products if item.category_id == "subscriptions-media"
    ]
    assert subscriptions
    assert all(
        item.evidence_family
        in {
            EvidenceFamily.RECURRING_DECLARED,
            EvidenceFamily.BILLING_UNDECLARED,
            EvidenceFamily.AUTHORITY_CONFLICT,
        }
        for item in subscriptions
    )


def test_a_product_cannot_resolve_another_merchants_evidence(universe, store) -> None:
    first, second = universe.products[0], None
    for candidate in universe.products:
        if candidate.merchant_id != first.merchant_id:
            second = candidate
            break
    assert second is not None
    with pytest.raises(UnknownEvidenceError):
        store.resolve_evidence_ids(
            second.evidence_ids, merchant_id=first.merchant_id, sku=first.sku
        )


def test_unknown_identities_are_refused(store) -> None:
    with pytest.raises(UnknownProductError):
        store.get_product(merchant_id="sandbox-acme-audio", sku="not-a-real-sku")
    with pytest.raises(UnknownProductError):
        store.get_product(merchant_id="merchant-scholarly", sku="studyglow-desk-lamp")


# ------------------------------------------------------- declaration scanning


def test_readiness_is_read_from_evidence_not_from_the_generator_label(
    universe, store
) -> None:
    """The readiness signals must be a measurement, not a copy of the fixture.

    Reading the family label would make the badges agree with the generator by
    construction and tell nobody anything about the evidence.
    """

    by_family: dict[EvidenceFamily, object] = {}
    for product in universe.products:
        by_family.setdefault(product.evidence_family, product)
        if len(by_family) == len(EvidenceFamily):
            break

    complete = by_family[EvidenceFamily.COMPLETE]
    assert readiness_for(store, complete)["billing_model"] == DECLARED
    assert readiness_for(store, complete)["content_classification"] == DECLARED
    assert readiness_for(store, complete)["intended_use"] == DECLARED

    undeclared = by_family[EvidenceFamily.BILLING_UNDECLARED]
    signals = readiness_for(store, undeclared)
    assert signals["billing_model"] == NOT_DECLARED
    assert signals["intended_use"] == NOT_DECLARED
    # Identity and price are still published: only the terms are missing.
    assert signals["merchant_identity"] == DECLARED
    assert signals["authoritative_price"] == DECLARED

    conflict = by_family[EvidenceFamily.AUTHORITY_CONFLICT]
    assert readiness_for(store, conflict)["billing_model"] == CONFLICTED

    recurring = by_family[EvidenceFamily.RECURRING_DECLARED]
    assert readiness_for(store, recurring)["billing_model"] == DECLARED


def test_declaration_scan_of_empty_evidence_declares_nothing() -> None:
    signals = scan_declarations(())
    assert set(signals.values()) <= {NOT_DECLARED, "UNKNOWN"}


# --------------------------------------------------------- trust quarantine


def test_sandbox_evidence_cannot_reach_a_marketplace_listing(store) -> None:
    """A crawled listing resolves to nothing through the sandbox store.

    The lookup keys on the registered source and on `merchant/sku`. A listing
    from the historical dataset satisfies neither, whatever it is called.
    """

    lookup = build_trusted_lookup(store)

    class CrawledListing:
        source = "flipkart"
        source_product_id = "sandbox-acme-audio/audio-headphones-000"

    facts = lookup(CrawledListing())
    assert facts.evidence_count == 0
    assert facts.merchant_of_record is None
    assert facts.category_declared_by_merchant is False


def test_a_marketplace_listing_cannot_inherit_trust_by_sharing_a_title(
    universe, store
) -> None:
    """Same words, different identifier, no trust. Identity is the identifier."""

    product = universe.products[0]
    lookup = build_trusted_lookup(store)

    class LookalikeListing:
        source = "flipkart"
        source_product_id = "impostor-1"
        title = product.name

    assert lookup(LookalikeListing()).evidence_count == 0

    class ClaimingRegisteredSource:
        # Even asserting the registered source does not help: the identifier
        # still has to resolve in the store, and this one does not.
        source = REGISTERED_SOURCE
        source_product_id = "merchant-nobody/nothing"

    assert lookup(ClaimingRegisteredSource()).evidence_count == 0


def test_a_sandbox_listing_resolves_only_through_its_exact_identifier(
    universe, store
) -> None:
    product = universe.products[0]
    lookup = build_trusted_lookup(store)

    class RegisteredSandboxListing:
        source = REGISTERED_SOURCE
        source_product_id = f"{product.merchant_id}/{product.sku}"

    facts = lookup(RegisteredSandboxListing())
    assert facts.evidence_count == 3
    assert facts.merchant_of_record == product.merchant_id


def test_every_merchant_in_the_world_is_a_declared_merchant(universe) -> None:
    declared = {item.merchant_id for item in MERCHANTS}
    used = {item.merchant_id for item in universe.products}
    assert used <= declared
