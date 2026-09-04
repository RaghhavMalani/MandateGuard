"""The free-text journey, end to end, through the real product service.

These tests run against the committed catalog and frozen indexes. If those are
absent the discovery surface reports itself unavailable and the authorization
journeys still work, which is itself asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from mandateguard.discovery.trust import DISCOVERY_ONLY_STAGES
from mandateguard.intelligence.models import SelectedProductIdentity
from mandateguard.product.discovery_service import (
    DiscoverySurface,
    build_trusted_lookup,
    select,
)
from mandateguard.product.http import create_server
from mandateguard.product.service import CommerceLabService


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "data" / "processed" / "discovery_catalog.jsonl.gz"

requires_catalog = pytest.mark.skipif(
    not CATALOG_PATH.exists(),
    reason="the discovery catalog is not built in this checkout",
)


@pytest.fixture(scope="module")
def service():
    instance = CommerceLabService()
    try:
        yield instance
    finally:
        instance.close()


# --------------------------------------------------------------------------
# Availability and configuration
# --------------------------------------------------------------------------


def test_the_product_starts_whether_or_not_the_catalog_is_built(service) -> None:
    health = service.health()
    assert health["status"] == "ok"
    assert isinstance(health["discovery_catalog_available"], bool)


def test_a_missing_catalog_is_reported_rather_than_crashing(tmp_path: Path, service) -> None:
    surface = DiscoverySurface(
        processed_dir=tmp_path,
        models_dir=tmp_path,
        store=service.store,
    )
    assert surface.available is False
    config = surface.public_config()
    assert config["available"] is False
    assert config["reason"]
    assert config["boundary"]["discovery_catalog_is_trusted_evidence"] is False
    with pytest.raises(RuntimeError):
        surface.search("a desk lamp")


@requires_catalog
def test_the_public_config_publishes_provenance_and_licensing(service) -> None:
    discovery = service.public_config()["discovery"]
    assert discovery["available"] is True
    assert discovery["provenance"]["licence"] == "CC BY-SA 4.0"
    assert discovery["provenance"]["publisher"]
    assert discovery["provenance"]["catalog_sha256"]
    assert discovery["provenance"]["trust_tier"] == "DISCOVERY_LISTING"
    assert discovery["catalog"]["listings"] > 10_000


@requires_catalog
def test_system_scale_and_model_quality_are_separate_blocks(service) -> None:
    config = service.public_config()
    scale = config["system_scale"]
    quality = config["model_quality"]
    assert scale["kind"] == "SYSTEM_SCALE"
    assert quality["kind"] == "MODEL_QUALITY"
    # Neither block borrows the other's numbers.
    assert "macro_f1" not in scale
    assert "catalog_listings" not in quality
    assert quality["classifier"]["advisory_only"] is True
    assert "never authorization" in quality["boundary"].lower() or "not authorization" in (
        quality["boundary"].lower()
    )


@requires_catalog
def test_the_measured_negative_results_are_published_not_hidden(service) -> None:
    findings = service.public_config()["model_quality"]["negative_results"]
    assert findings
    joined = " ".join(item["finding"] for item in findings)
    assert "did not improve retrieval over BM25" in joined
    assert "rejected" in joined.lower()


# --------------------------------------------------------------------------
# Custom intent
# --------------------------------------------------------------------------


@requires_catalog
def test_an_arbitrary_intent_returns_explained_candidates(service) -> None:
    payload = service.discovery_search(
        intent="Buy a study lamp under Rs 2000. No subscriptions.", top_k=6
    )
    assert payload["candidates"]
    assert payload["mandate"]["max_total_minor"] == 200_000
    assert payload["mandate"]["recurring_allowed"] is False
    assert payload["mandate_plain_english"]
    for candidate in payload["candidates"]:
        assert candidate["match"]["headline"]
        assert candidate["transactability"]["checks"]
        assert candidate["anomaly"]["authorization_authority"] == "NONE"
        assert candidate["trust_tier"] == "DISCOVERY_LISTING"


@requires_catalog
def test_a_stated_price_ceiling_is_enforced_on_every_candidate(service) -> None:
    payload = service.discovery_search(intent="desk lamp under Rs 800", top_k=8)
    for candidate in payload["candidates"]:
        assert candidate["price_minor"] is not None
        assert candidate["price_minor"] <= 80_000


@requires_catalog
def test_an_exclusion_removes_matching_listings_from_the_results(service) -> None:
    payload = service.discovery_search(
        intent="a study set under Rs 3000 and no notebook", top_k=8
    )
    for candidate in payload["candidates"]:
        haystack = f"{candidate['title']} {candidate['top_category']}".casefold()
        assert "notebook" not in haystack


@requires_catalog
def test_a_crawled_listing_ends_at_review_required_with_no_provider_call(service) -> None:
    intent = "Buy wired headphones under Rs 5000. One-time payment only."
    payload = service.discovery_search(intent=intent, top_k=8)
    unvouched = next(
        item for item in payload["candidates"] if not item["transactable"]
    )
    result = service.discovery_select(
        intent=intent, catalog_product_id=unvouched["catalog_product_id"]
    )
    selection = result["selection"]
    assert selection["transactable"] is False
    assert selection["status"] == "REVIEW REQUIRED"
    assert selection["stage"] == DISCOVERY_ONLY_STAGES[3]
    assert selection["product_identity"] is None
    assert selection["payment_provider_calls"] == 0
    assert "will not manufacture an ALLOW" in selection["next_step"]


@requires_catalog
def test_a_registered_listing_hands_a_typed_identity_to_the_controller(service) -> None:
    """The handoff is a structured identity, never a sentence.

    The previous design appended "SKU: <sku>" to the user's own text and let the
    buyer's parser find it again. That made product identity a property of prose,
    which the user also writes.
    """

    intent = "Buy a study lamp under Rs 2000. No subscriptions."
    payload = service.discovery_search(intent=intent, top_k=8)
    vouched = next(item for item in payload["candidates"] if item["transactable"])
    selection = service.discovery_select(
        intent=intent, catalog_product_id=vouched["catalog_product_id"]
    )["selection"]
    assert selection["transactable"] is True
    assert selection["status"] == "READY FOR AUTHORIZATION"
    assert selection["stage"] == DISCOVERY_ONLY_STAGES[1]
    assert selection["payment_provider_calls"] == 0

    identity = selection["product_identity"]
    assert identity is not None
    assert identity["source"] == "mandateguard"
    assert identity["merchant_id"] and identity["sku"]
    assert identity["source_product_id"] == f"{identity['merchant_id']}/{identity['sku']}"
    assert identity["catalog_product_id"] == vouched["catalog_product_id"]
    # There is no prose channel left to carry identity.
    assert "authorization_intent" not in selection


@requires_catalog
def test_the_whole_journey_reaches_a_real_controller_decision(service) -> None:
    intent = "Buy a study lamp under Rs 2000 for individual study. No subscriptions."
    payload = service.discovery_search(intent=intent, top_k=8)
    vouched = next(item for item in payload["candidates"] if item["transactable"])
    selection = service.discovery_select(
        intent=intent, catalog_product_id=vouched["catalog_product_id"]
    )["selection"]
    identity = SelectedProductIdentity.from_mapping(selection["product_identity"])
    snapshot = service.run_sync(
        user_intent=intent, selected_product=identity, timeout_seconds=60.0
    )
    result = snapshot["result"]
    assert result["decision"] in {"ALLOW", "BLOCK", "REVIEW"}
    assert result["authorization"]["controller_source"] == (
        "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER"
    )
    assert result["observed_counters"]["openai_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0
    # Whatever the controller decided, it decided about the clicked product.
    assert result["buyer"]["merchant"] == identity.merchant_id
    assert result["buyer"]["sku"] == identity.sku
    # And the buyer saw the user's own sentence, with nothing appended to it.
    assert result["buyer"]["buyer_provided_text"] == intent
    assert "SKU:" not in result["buyer"]["buyer_provided_text"]


@requires_catalog
def test_selecting_a_listing_that_is_not_in_the_results_is_refused(service) -> None:
    with pytest.raises(KeyError):
        service.discovery_select(
            intent="a desk lamp under Rs 2000",
            catalog_product_id="flipkart.deadbeefdeadbeefdeadbeef",
        )


@requires_catalog
@pytest.mark.parametrize("top_k", [0, 99, -1])
def test_an_out_of_range_result_count_is_refused(service, top_k: int) -> None:
    with pytest.raises(ValueError):
        service.discovery_search(intent="a desk lamp", top_k=top_k)


def test_an_empty_intent_is_refused_before_any_search(service) -> None:
    with pytest.raises((ValueError, RuntimeError)):
        service.discovery_search(intent="   ")


# --------------------------------------------------------------------------
# The trusted lookup is the only channel, and it carries no evidence text
# --------------------------------------------------------------------------


@requires_catalog
def test_only_registered_listings_resolve_trusted_facts(service) -> None:
    lookup = build_trusted_lookup(service.store)
    catalog = service.discovery.engine.catalog
    crawled = next(item for item in catalog if item.source == "flipkart")
    registered = next(item for item in catalog if item.source == "mandateguard")
    assert lookup(crawled).evidence_count == 0
    assert lookup(crawled).merchant_of_record is None
    assert lookup(registered).evidence_count > 0
    assert lookup(registered).merchant_of_record


@requires_catalog
def test_the_trusted_lookup_never_returns_evidence_text(service) -> None:
    """Counts and identities cross the boundary. Merchant statements do not."""

    lookup = build_trusted_lookup(service.store)
    registered = next(
        item for item in service.discovery.engine.catalog if item.source == "mandateguard"
    )
    facts = lookup(registered)
    assert set(vars(type(facts))["__slots__"]) == {
        "evidence_count",
        "merchant_of_record",
        "recurrence_evidenced",
        "category_declared_by_merchant",
    }
    assert isinstance(facts.evidence_count, int)


def test_a_listing_whose_identifier_is_malformed_resolves_to_nothing(service) -> None:
    from tests.discovery_factories import build_product

    lookup = build_trusted_lookup(service.store)
    forged = build_product(source="mandateguard", source_product_id="no-slash-here")
    assert lookup(forged).evidence_count == 0


def test_selection_of_an_untransactable_candidate_never_produces_an_identity() -> None:
    selection = select(
        {"catalog_product_id": "flipkart.abc", "transactable": False, "source": "flipkart"},
        "buy something",
    )
    assert selection.product_identity is None
    assert selection.transactable is False


def test_a_crawled_listing_cannot_forge_a_registered_identity() -> None:
    """A crawled listing that claims registered identity resolves to nothing.

    `transactable` is set by the trusted-evidence lookup, so a candidate cannot
    reach here claiming it. This asserts the second gate anyway: even a mapping
    that claims both, from the wrong source, yields no identity.
    """

    for forged in (
        {
            "catalog_product_id": "flipkart.abc",
            "transactable": True,
            "source": "flipkart",
            "source_product_id": "merchant-scholarly/studyglow-desk-lamp",
        },
        {
            "catalog_product_id": "flipkart.abc",
            "transactable": True,
            "source": "MandateGuard",
            "source_product_id": "merchant-scholarly/studyglow-desk-lamp",
        },
        {
            "catalog_product_id": "flipkart.abc",
            "transactable": True,
            "source": "mandateguard",
            "source_product_id": "no-slash-here",
        },
        {
            "catalog_product_id": "flipkart.abc",
            "transactable": True,
            "source": "mandateguard",
            "source_product_id": "merchant/with/extra/slashes",
        },
    ):
        selection = select(forged, "buy something")
        assert selection.product_identity is None
        assert selection.transactable is False
        assert selection.status == "REVIEW REQUIRED"


# --------------------------------------------------------------------------
# HTTP boundary
# --------------------------------------------------------------------------


@pytest.fixture()
def http_server():
    server = create_server(host="127.0.0.1", port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        server.service.close()


def _post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


@requires_catalog
def test_the_search_endpoint_returns_candidates(http_server: str) -> None:
    payload = _post(
        http_server,
        "/api/discovery/search",
        {"intent": "a desk lamp under Rs 2000", "top_k": 3},
    )
    assert len(payload["candidates"]) <= 3
    assert payload["boundary"]["discovery_catalog_is_trusted_evidence"] is False


def test_the_search_endpoint_refuses_an_unexpected_field(http_server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(
            http_server,
            "/api/discovery/search",
            {"intent": "a lamp", "top_k": 3, "authorize": True},
        )
    assert error.value.code == 400


def test_the_search_endpoint_refuses_a_missing_intent(http_server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(http_server, "/api/discovery/search", {"top_k": 3})
    assert error.value.code == 400


@requires_catalog
def test_the_select_endpoint_reports_a_missing_listing_as_not_found(
    http_server: str,
) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(
            http_server,
            "/api/discovery/select",
            {"intent": "a desk lamp", "catalog_product_id": "flipkart.notarealid"},
        )
    assert error.value.code == 404
