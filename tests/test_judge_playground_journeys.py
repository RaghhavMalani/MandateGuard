"""Playground journeys, judged by the real controller.

Every assertion below reads a verdict the shipped authorization controller
produced. Nothing here constructs a decision, stubs a verdict, or asserts an
outcome that a fixture was labelled with; where a test wants a BLOCK it arranges
a world and a mandate that ought to earn one and then checks what came back.

The tests are grouped by the question they answer:

* the sandbox is reachable and reaches all three outcomes;
* nothing the browser sends can move an authorization;
* consent, replay and identity binding behave as they do everywhere else;
* one visitor cannot touch another visitor's run;
* simulated onboarding creates new evidence and leaves the crawled row alone.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import socket
import tempfile
from typing import Any, Iterator
import urllib.request as urllib_request
from uuid import uuid4

import pytest

from mandateguard.models.decision import DecisionAction
from mandateguard.product.playground import PlaygroundError, explain_decision
from mandateguard.product.service import CommerceLabService
from mandateguard.sandbox.session import (
    MAX_ONBOARDED_PER_SESSION,
    MAX_RUNS_PER_SESSION,
)
from mandateguard.sandbox.templates import EvidenceFamily


@pytest.fixture(scope="module")
def service() -> Iterator[CommerceLabService]:
    # A module-scoped service so the 3,960-product world is generated once for
    # the whole file rather than once per test. Its own directory, so nothing
    # here shares a ledger or a consent registry with another test module.
    state_dir = Path(tempfile.mkdtemp(prefix="mandateguard-playground-tests-"))
    instance = CommerceLabService(state_dir=state_dir)
    try:
        yield instance
    finally:
        instance.close()
        shutil.rmtree(state_dir, ignore_errors=True)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib_request, "urlopen", forbidden)


def session_id(service: CommerceLabService) -> str:
    return service.open_judge_session()["session_id"]


def authorize(
    service: CommerceLabService,
    *,
    intent: str,
    catalog_product_id: str,
    session: str,
    max_total_minor: int | None = None,
    defer_execution: bool = False,
) -> dict[str, Any]:
    run, _deduplicated, _session = service.playground_authorize(
        intent=intent,
        catalog_product_id=catalog_product_id,
        request_id="test_" + uuid4().hex,
        session_id=session,
        max_total_minor=max_total_minor,
        defer_execution=defer_execution,
    )
    assert run.completion.wait(60), "playground run did not finish"
    return service.playground_run_snapshot(run)


def run_scenario(service: CommerceLabService, scenario_id: str, session: str):
    run, _deduplicated, _session, scenario = service.playground_scenario(
        scenario_id=scenario_id,
        request_id="test_" + uuid4().hex,
        session_id=session,
    )
    assert run.completion.wait(60), "scenario run did not finish"
    return run, scenario


def first_with_family(service: CommerceLabService, family: EvidenceFamily, **filters):
    universe = service.playground._universe  # noqa: SLF001 - test reaches into the world
    for product in universe.products:
        if product.evidence_family is not family:
            continue
        if filters.get("max_price") and product.price_minor > filters["max_price"]:
            continue
        if filters.get("non_recurring") and product.recurring:
            continue
        return product
    raise AssertionError(f"no sandbox listing in family {family}")


# ------------------------------------------------------------------- search


ARBITRARY_INTENTS = [
    "headphones under 5000",
    "wireless headphones for gym under 4k",
    "desk lamp under 1500",
    "something to help me study at night",
    "running shoes below 6000",
    "camera for beginners",
    "backpack for college under 3000",
    "smartwatch under 10000",
    "mechanical keyboard below 7000",
    "beginner finance course without gambling",
    "office chair under 15000",
    "laptop stand",
    "power bank under 2000",
    "yoga mat under 2500",
    "air fryer under 6000",
    "kitchen knife under 1500",
    "notebook set",
    "bluetooth speaker under 4000",
    "i need headphones for my commute under 6000",
    "study lamp upto 1200",
]


@pytest.mark.parametrize("intent", ARBITRARY_INTENTS)
def test_arbitrary_judge_intents_find_candidates(
    service: CommerceLabService, intent: str
) -> None:
    payload = service.playground_search(intent=intent, top_k=8)
    assert payload["candidates"], f"no candidate for {intent!r}"
    assert payload["no_match_message"] is None
    for candidate in payload["candidates"]:
        assert candidate["synthetic"] is True
        assert candidate["world"] in {"SANDBOX", "SANDBOX_ONBOARDED"}
        assert candidate["readiness"]["merchant_identity"] == "DECLARED"


def test_candidate_found_rate_across_arbitrary_intents(
    service: CommerceLabService,
) -> None:
    found = sum(
        1
        for intent in ARBITRARY_INTENTS
        if service.playground_search(intent=intent, top_k=8)["candidates"]
    )
    assert found / len(ARBITRARY_INTENTS) >= 0.95


def test_a_request_nothing_matches_explains_itself_instead_of_dead_ending(
    service: CommerceLabService,
) -> None:
    """An impossible budget must produce an explanation, not an empty page."""

    payload = service.playground_search(intent="Buy a laptop under INR 200.", top_k=8)
    assert payload["candidates"] == []
    assert payload["no_match_message"] == (
        "No suitable sandbox product matched all of your constraints."
    )
    assert payload["near_misses"], "closest candidates must still be offered"
    for miss in payload["near_misses"]:
        assert miss["excluded_by"] == "MAX_TOTAL"
        assert "above your" in miss["explanation"]
    assert payload["constraints_applied"]


def test_search_never_reaches_a_decision_or_a_provider(
    service: CommerceLabService,
) -> None:
    payload = service.playground_search(intent="desk lamp under 2000", top_k=6)
    assert payload["authority"] == "RETRIEVAL_IS_ADVISORY_AND_DECIDES_NOTHING"
    text = str(payload)
    assert "razorpay" not in text.lower()
    for key in ("decision", "capability", "authorization_result"):
        assert key not in payload


def test_an_unbounded_instruction_is_not_given_an_invented_budget(
    service: CommerceLabService,
) -> None:
    payload = service.playground_search(intent="laptop stand", top_k=6)
    assert payload["spending_limit_required"] is True
    assert payload["mandate"]["max_total_minor"] is None
    assert payload["mandate"]["ceiling_source"] == "NOT_STATED"
    with pytest.raises(PlaygroundError) as error:
        service.playground_authorize(
            intent="laptop stand",
            catalog_product_id=payload["candidates"][0]["catalog_product_id"],
            request_id="test_" + uuid4().hex,
        )
    assert error.value.code == "SPENDING_LIMIT_REQUIRED"


# ------------------------------------------------- the three outcomes


def test_allow_comes_from_the_real_controller(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    intent = "Buy wireless headphones under INR 5,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=8, session_id=session)
    complete = next(
        item
        for item in payload["candidates"]
        if item["readiness"]["billing_model"] == "DECLARED"
    )
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=complete["catalog_product_id"],
        session=session,
    )
    result = snapshot["result"]
    assert snapshot["state"] == "COMPLETE"
    assert snapshot["world"] == "SANDBOX"
    assert result["decision"] == "ALLOW"
    # The verdict came out of the frozen controller, and says so.
    assert result["authorization"]["controller_source"] == (
        "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER"
    )
    assert result["authorization"]["final_controller"] == "ALLOW"
    assert result["authorization"]["deterministic"]["action"] == "ALLOW"
    assert result["authorization"]["semantic"]["verdict"] == "PASS"
    # Tier A and Tier B were genuinely evaluated, not skipped for a sandbox run.
    assert len(result["authorization"]["deterministic"]["tier_a"]) >= 8
    assert len(result["authorization"]["deterministic"]["tier_b"]) >= 10
    assert result["evidence"]["trusted_evidence_count"] >= 1
    assert result["execution"]["external_network_calls"] == 0
    assert snapshot["explanation"]["headline"] == (
        "This purchase matches your mandate. Payment execution may proceed."
    )


def test_choosing_an_over_budget_listing_blocks_before_payment(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    run, scenario = run_scenario(service, "budget-violation", session)
    result = run.snapshot()["result"]
    assert result["decision"] == "BLOCK"
    assert result["execution"]["razorpay_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0
    assert result["execution"]["status"] == "NOT_CALLED"
    failed = {
        item["family"]
        for item in result["authorization"]["deterministic"]["tier_b"]
        if item["status"] == "FAIL"
    }
    # B6 is the price ceiling; the mandate's own limit is what stopped it.
    assert "B6" in failed
    assert scenario.expectation.startswith("BLOCK")


def test_declared_prohibited_content_blocks_on_the_stated_exclusion(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    run, _scenario = run_scenario(service, "prohibited-content", session)
    result = run.snapshot()["result"]
    assert result["decision"] == "BLOCK"
    assert result["authorization"]["semantic"]["verdict"] == "VIOLATION"
    violations = [
        item
        for item in result["authorization"]["semantic"]["checks"]
        if item["status"] == "VIOLATION"
    ]
    assert violations, "the exclusion must be the thing that failed"
    assert violations[0]["family"] == "exclusion"
    assert result["execution"]["razorpay_calls"] == 0


def test_a_recurring_listing_blocks_when_recurrence_was_not_permitted(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    product = first_with_family(
        service, EvidenceFamily.RECURRING_DECLARED, max_price=200_000
    )
    intent = "Buy a subscription under INR 3,000. No subscriptions."
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=product.catalog_product_id,
        session=session,
    )
    assert snapshot["result"]["decision"] == "BLOCK"
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0


def test_undeclared_billing_reaches_review_not_a_guess(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    run, _scenario = run_scenario(service, "billing-undeclared", session)
    snapshot = run.snapshot()
    result = snapshot["result"]
    assert result["decision"] == "REVIEW"
    assert result["execution"]["razorpay_calls"] == 0
    assert result["execution"]["external_network_calls"] == 0
    abstained = [
        item
        for item in result["authorization"]["semantic"]["checks"]
        if item["status"] == "ABSTAIN"
    ]
    assert abstained, "REVIEW must be an abstention, not an unexplained stop"


def test_conflicting_merchant_records_reach_review(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    run, _scenario = run_scenario(service, "evidence-conflict", session)
    result = run.snapshot()["result"]
    assert result["decision"] == "REVIEW"
    assert result["execution"]["razorpay_calls"] == 0
    evidence_text = " ".join(card["text"] for card in result["evidence"]["cards"])
    assert "registered records disagree" in evidence_text


def test_every_scenario_reaches_the_outcome_it_documents(
    service: CommerceLabService, no_network: None
) -> None:
    """The documented expectation is checked here, and nowhere on a decision path.

    A scenario whose verdict has moved is a change in the system that somebody
    should look at, which is exactly what a failing test is for.
    """

    session = session_id(service)
    expected = {
        "safe-purchase": "ALLOW",
        "budget-violation": "BLOCK",
        "prohibited-content": "BLOCK",
        "evidence-conflict": "REVIEW",
        "billing-undeclared": "REVIEW",
        "recoverable-review": "REVIEW",
        "revoked-after-allow": "ALLOW",
        "replay": "ALLOW",
    }
    for scenario_id, decision in expected.items():
        run, _scenario = run_scenario(service, scenario_id, session)
        snapshot = run.snapshot()
        assert snapshot["state"] == "COMPLETE", f"{scenario_id}: {snapshot.get('error')}"
        assert snapshot["result"]["decision"] == decision, scenario_id


# ------------------------------------------- consent, replay, mutation


def test_revocation_refuses_execution_before_any_provider_call(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    run, _scenario = run_scenario(service, "revoked-after-allow", session)
    assert run.snapshot()["result"]["decision"] == "ALLOW"
    assert run.snapshot()["result"]["execution"]["status"] == "AUTHORIZED"

    service.revoke_mandate(run.run_id)
    after = service.attempt_execution(run.run_id)
    execution = after["result"]["execution"]
    assert execution["status"] == "REJECTED_BEFORE_NETWORK"
    assert execution["reason"] == "MANDATE_REVOKED"
    assert execution["razorpay_calls"] == 0
    assert execution["external_network_calls"] == 0


def test_the_same_capability_cannot_be_spent_twice(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    run, _scenario = run_scenario(service, "replay", session)
    assert run.snapshot()["result"]["execution"]["status"] == "ORDER_CREATED"
    replayed = service.replay(run.run_id)
    replay = replayed["result"]["execution"]["replay"]
    assert replay["status"] == "REJECTED_BEFORE_NETWORK"
    assert replay["reason"] == "NONCE_ALREADY_USED"
    assert replay["razorpay_additional_calls"] == 0
    assert replay["external_additional_calls"] == 0


def test_a_mutated_price_from_the_browser_changes_nothing(
    service: CommerceLabService, no_network: None
) -> None:
    """The client sends an identifier. Price comes from the trusted store."""

    session = session_id(service)
    intent = "Buy a desk lamp under INR 5,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=6, session_id=session)
    candidate = payload["candidates"][0]
    truth = candidate["price_minor"]
    # A browser is free to render whatever it likes; the server re-reads.
    candidate["price_minor"] = 1
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=candidate["catalog_product_id"],
        session=session,
    )
    assert snapshot["result"]["buyer"]["price_minor"] == truth


def test_a_mutated_sku_or_merchant_cannot_address_another_listing(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    intent = "Buy a desk lamp under INR 5,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=6, session_id=session)
    real = payload["candidates"][0]["catalog_product_id"]
    for forged in (
        real[:-1] + ("0" if real[-1] != "0" else "1"),
        "sandbox.deadbeefdeadbeefdeadbeef",
        "flipkart.0123456789abcdef01234567",
        "sandbox-acme-audio/audio-headphones-000",
    ):
        with pytest.raises(PlaygroundError) as error:
            service.playground_authorize(
                intent=intent,
                catalog_product_id=forged,
                request_id="test_" + uuid4().hex,
                session_id=session,
            )
        assert error.value.code == "PRODUCT_NOT_FOUND"


def test_a_stated_ceiling_always_beats_a_client_supplied_one(
    service: CommerceLabService, no_network: None
) -> None:
    """Setting your own limit is consent. Raising a limit you typed is not.

    A client that could quietly widen a ceiling its user wrote down would be
    able to turn a BLOCK into an ALLOW from the browser, so the parsed value
    wins whenever the instruction states one.
    """

    session = session_id(service)
    intent = "Buy headphones under INR 2,000. No subscriptions."
    product = None
    universe = service.playground._universe  # noqa: SLF001
    for candidate in universe.products:
        if candidate.category_id == "audio-headphones" and candidate.price_minor > 200_000:
            product = candidate
            break
    assert product is not None
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=product.catalog_product_id,
        session=session,
        max_total_minor=9_999_900,
    )
    result = snapshot["result"]
    assert result["decision"] == "BLOCK"
    assert result["execution"]["razorpay_calls"] == 0


def test_the_intent_the_run_records_is_the_intent_that_was_sent(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    intent = "Buy a desk lamp under INR 3,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=6, session_id=session)
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=payload["candidates"][0]["catalog_product_id"],
        session=session,
    )
    assert snapshot["result"]["buyer"]["mandate"] == intent


# ------------------------------------------------------ session isolation


def test_two_visitors_get_two_sessions(service: CommerceLabService) -> None:
    first = session_id(service)
    second = session_id(service)
    assert first != second
    assert first.startswith("js_") and len(first) == 35


def test_one_session_cannot_act_on_another_session_run(
    service: CommerceLabService, no_network: None
) -> None:
    owner = session_id(service)
    stranger = session_id(service)
    run, _scenario = run_scenario(service, "revoked-after-allow", owner)

    # The owner may.
    service.authorize_run_access(run, owner)
    # Nobody else may, with or without a session.
    for identity in (stranger, None, "js_" + "0" * 32):
        with pytest.raises(PermissionError):
            service.authorize_run_access(run, identity)


def test_revoking_in_one_session_does_not_reach_another_session_capability(
    service: CommerceLabService, no_network: None
) -> None:
    """The demo of revocation would be a lie if it cancelled a stranger's run."""

    first_session = session_id(service)
    second_session = session_id(service)
    first, _ = run_scenario(service, "revoked-after-allow", first_session)
    second, _ = run_scenario(service, "revoked-after-allow", second_session)
    assert first.run_id != second.run_id

    service.revoke_mandate(first.run_id)
    refused = service.attempt_execution(first.run_id)
    assert refused["result"]["execution"]["status"] == "REJECTED_BEFORE_NETWORK"

    # The other visitor's capability is untouched and still spendable.
    allowed = service.attempt_execution(second.run_id)
    assert allowed["result"]["execution"]["status"] == "ORDER_CREATED"


def test_an_idempotent_request_id_is_not_shared_across_sessions(
    service: CommerceLabService, no_network: None
) -> None:
    owner = session_id(service)
    stranger = session_id(service)
    intent = "Buy a desk lamp under INR 3,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=6, session_id=owner)
    product_id = payload["candidates"][0]["catalog_product_id"]
    request_id = "shared_" + uuid4().hex
    run, _dedup, _session = service.playground_authorize(
        intent=intent,
        catalog_product_id=product_id,
        request_id=request_id,
        session_id=owner,
    )
    assert run.completion.wait(60)
    with pytest.raises(ValueError, match="already bound"):
        service.playground_authorize(
            intent=intent,
            catalog_product_id=product_id,
            request_id=request_id,
            session_id=stranger,
        )


# ------------------------------------------- simulated merchant onboarding


@pytest.fixture
def marketplace_listing(service: CommerceLabService) -> dict[str, Any]:
    if not service.discovery.available:
        pytest.skip("the historical discovery catalog is not built in this checkout")
    intent = "Buy a study lamp under INR 2,000. No subscriptions."
    payload = service.discovery_search(intent=intent, top_k=4)
    if not payload["candidates"]:
        pytest.skip("no marketplace candidate for the onboarding fixture")
    return {"intent": intent, "candidate": payload["candidates"][0]}


def test_onboarding_creates_new_synthetic_evidence(
    service: CommerceLabService, marketplace_listing: dict[str, Any], no_network: None
) -> None:
    session = session_id(service)
    listing = marketplace_listing["candidate"]
    payload = service.playground_onboard(
        intent=marketplace_listing["intent"],
        catalog_product_id=listing["catalog_product_id"],
        declaration={
            "merchant_display_name": "Onboarding Test Merchant",
            "price_minor": 129_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["individual study"],
        },
        session_id=session,
    )
    assert payload["simulation"] is True
    assert payload["merchant"]["merchant_id"].startswith("sandbox-onboarded-")
    assert payload["product"]["synthetic"] is True
    # The declared price is the authoritative one, not the crawled listing's.
    assert payload["product"]["price_minor"] == 129_900
    assert payload["readiness"]["billing_model"] == "DECLARED"
    assert len(payload["trusted_evidence"]) == 3
    for entry in payload["trusted_evidence"]:
        assert entry["evidence_id"].startswith("sbev-")
        assert "SYNTHETIC SANDBOX RECORD" in entry["text"]


def test_onboarding_leaves_the_marketplace_row_untrusted(
    service: CommerceLabService, marketplace_listing: dict[str, Any], no_network: None
) -> None:
    """The crawled row must be exactly as untransactable afterwards as before."""

    session = session_id(service)
    intent = marketplace_listing["intent"]
    listing_id = marketplace_listing["candidate"]["catalog_product_id"]
    before = service.discovery_select(intent=intent, catalog_product_id=listing_id)
    assert before["selection"]["transactable"] is False

    payload = service.playground_onboard(
        intent=intent,
        catalog_product_id=listing_id,
        declaration={
            "merchant_display_name": "Onboarding Trust Test",
            "price_minor": 99_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["home use"],
        },
        session_id=session,
    )
    assert payload["source_listing"]["still_untrusted"] is True
    assert payload["marketplace_listing_after_onboarding"]["transactable"] is False

    after = service.discovery_select(intent=intent, catalog_product_id=listing_id)
    assert after["selection"] == before["selection"]
    assert after["candidate"]["trusted_evidence_count"] == (
        before["candidate"]["trusted_evidence_count"]
    )


def test_a_freshly_onboarded_listing_gets_a_fresh_authorization(
    service: CommerceLabService, marketplace_listing: dict[str, Any], no_network: None
) -> None:
    """A fresh run, and an honest one: the family nobody vouched for.

    Onboarding publishes a price, a billing model, a content classification and
    an intended-use list, and every one of those clears. What it cannot publish
    is a *server-owned product family*: the listing's only category words came
    off a crawled marketplace page, and this instruction does name a product
    family. So A2 reports the identity as unavailable and the run reaches
    REVIEW rather than guessing that the two agree.
    """

    session = session_id(service)
    intent = marketplace_listing["intent"]
    payload = service.playground_onboard(
        intent=intent,
        catalog_product_id=marketplace_listing["candidate"]["catalog_product_id"],
        declaration={
            "merchant_display_name": "Onboarding Allow Test",
            "price_minor": 99_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["individual study", "home use"],
        },
        session_id=session,
    )
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=payload["product"]["catalog_product_id"],
        session=session,
    )
    assert snapshot["world"] == "SANDBOX_ONBOARDED"
    assert snapshot["result"]["decision"] == "REVIEW"
    assert snapshot["result"]["buyer"]["merchant"].startswith("sandbox-onboarded-")
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0
    tier_a = snapshot["result"]["authorization"]["deterministic"]["tier_a"]
    unresolved = [row for row in tier_a if row["status"] != "PASS"]
    assert [row["family"] for row in unresolved] == ["A2"]
    assert unresolved[0]["status"] == "NOT_EVALUABLE"
    assert unresolved[0]["reason"] == (
        "server-owned product-family identity unavailable for selected SKU"
    )


def test_an_onboarded_merchant_that_declares_nothing_reaches_review(
    service: CommerceLabService, marketplace_listing: dict[str, Any], no_network: None
) -> None:
    """Publishing a record is not the same as publishing terms in it."""

    session = session_id(service)
    intent = marketplace_listing["intent"]
    payload = service.playground_onboard(
        intent=intent,
        catalog_product_id=marketplace_listing["candidate"]["catalog_product_id"],
        declaration={
            "merchant_display_name": "Onboarding Review Test",
            "price_minor": 99_900,
            "billing_model": "NOT_DECLARED",
            "content_classification": "NOT_DECLARED",
            "purposes": [],
        },
        session_id=session,
    )
    assert payload["readiness"]["billing_model"] == "NOT_DECLARED"
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=payload["product"]["catalog_product_id"],
        session=session,
    )
    assert snapshot["result"]["decision"] == "REVIEW"
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0


def test_one_session_cannot_authorize_another_sessions_onboarded_listing(
    service: CommerceLabService, marketplace_listing: dict[str, Any], no_network: None
) -> None:
    owner = session_id(service)
    stranger = session_id(service)
    intent = marketplace_listing["intent"]
    payload = service.playground_onboard(
        intent=intent,
        catalog_product_id=marketplace_listing["candidate"]["catalog_product_id"],
        declaration={
            "merchant_display_name": "Onboarding Isolation Test",
            "price_minor": 99_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["home use"],
        },
        session_id=owner,
    )
    product_id = payload["product"]["catalog_product_id"]
    with pytest.raises(PlaygroundError) as error:
        service.playground_authorize(
            intent=intent,
            catalog_product_id=product_id,
            request_id="test_" + uuid4().hex,
            session_id=stranger,
        )
    assert error.value.code == "PRODUCT_NOT_FOUND"


def test_onboarding_rejects_a_declaration_that_is_not_complete(
    service: CommerceLabService, marketplace_listing: dict[str, Any]
) -> None:
    session = session_id(service)
    intent = marketplace_listing["intent"]
    listing_id = marketplace_listing["candidate"]["catalog_product_id"]
    for declaration in (
        {"merchant_display_name": "Partial"},
        {
            "merchant_display_name": "Bad Billing",
            "price_minor": 1000,
            "billing_model": "FREE",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": [],
        },
        {
            "merchant_display_name": "Bad Price",
            "price_minor": -5,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": [],
        },
        {
            "merchant_display_name": "Bad Purpose",
            "price_minor": 1000,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["whatever I feel like"],
        },
    ):
        with pytest.raises(PlaygroundError) as error:
            service.playground_onboard(
                intent=intent,
                catalog_product_id=listing_id,
                declaration=declaration,
                session_id=session,
            )
        assert error.value.code == "DECLARATION_INVALID"


def test_onboarding_is_bounded_per_session(
    service: CommerceLabService, marketplace_listing: dict[str, Any], no_network: None
) -> None:
    session = session_id(service)
    intent = marketplace_listing["intent"]
    listing_id = marketplace_listing["candidate"]["catalog_product_id"]
    for index in range(MAX_ONBOARDED_PER_SESSION):
        service.playground_onboard(
            intent=intent,
            catalog_product_id=listing_id,
            declaration={
                "merchant_display_name": f"Bounded Merchant {index}",
                "price_minor": 99_900,
                "billing_model": "ONE_TIME",
                "content_classification": "NO_RESTRICTED_CONTENT",
                "purposes": ["home use"],
            },
            session_id=session,
        )
    with pytest.raises(PlaygroundError) as error:
        service.playground_onboard(
            intent=intent,
            catalog_product_id=listing_id,
            declaration={
                "merchant_display_name": "One Too Many",
                "price_minor": 99_900,
                "billing_model": "ONE_TIME",
                "content_classification": "NO_RESTRICTED_CONTENT",
                "purposes": ["home use"],
            },
            session_id=session,
        )
    assert error.value.code == "ONBOARDING_LIMIT_REACHED"


# ------------------------------------------------------------- narration


def test_the_explanation_only_repeats_what_the_run_recorded() -> None:
    """Narration must not be able to disagree with the verdict it narrates."""

    from mandateguard.sandbox.intent import read_intent

    intent = read_intent("Buy a lamp under INR 2,000.")
    explanation = explain_decision(
        {
            "decision": "BLOCK",
            "buyer": {"price_minor": 799_900, "currency": "INR"},
            "authorization": {
                "deterministic": {
                    "tier_a": [],
                    "tier_b": [
                        {
                            "family": "B6",
                            "label": "Price ceiling",
                            "status": "FAIL",
                            "reason": "over the ceiling",
                        }
                    ],
                },
                "semantic": {"checks": []},
            },
            "execution": {"razorpay_calls": 0, "external_network_calls": 0},
        },
        intent,
    )
    assert explanation["decision"] == "BLOCK"
    assert explanation["headline"] == "MandateGuard stopped this before payment."
    assert explanation["failed_constraints"] == ["B6"]
    assert explanation["payment_reached"] is False
    assert explanation["offline_adapter_reached"] is False
    assert explanation["provider_calls"] == 0
    assert "INR 7,999.00 > INR 2,000.00 stated limit" in explanation["why"]


def test_narration_of_an_allow_does_not_invent_a_failure() -> None:
    from mandateguard.sandbox.intent import read_intent

    intent = read_intent("Buy a lamp under INR 2,000.")
    explanation = explain_decision(
        {
            "decision": "ALLOW",
            "buyer": {"price_minor": 129_900, "currency": "INR"},
            "authorization": {
                "deterministic": {"tier_a": [], "tier_b": []},
                "semantic": {"checks": []},
            },
            "execution": {"razorpay_calls": 1, "external_network_calls": 0},
        },
        intent,
    )
    assert explanation["failed_constraints"] == []
    # The public demo never reaches an external payment provider, whatever the
    # verdict, so this stays literal rather than being inferred from ALLOW.
    assert explanation["payment_reached"] is False
    assert explanation["offline_adapter_reached"] is True
    assert "Consent ACTIVE at the moment of decision" in explanation["why"]


# --------------------------------------------------------------- offline


def test_a_whole_journey_makes_no_external_network_call(
    service: CommerceLabService, no_network: None
) -> None:
    session = session_id(service)
    intent = "Buy a power bank under INR 3,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=8, session_id=session)
    snapshot = authorize(
        service,
        intent=intent,
        catalog_product_id=payload["candidates"][0]["catalog_product_id"],
        session=session,
    )
    counters = snapshot["result"]["observed_counters"]
    assert counters["openai_calls"] == 0
    assert counters["razorpay_http_calls"] == 0
    assert snapshot["result"]["execution"]["external_network_calls"] == 0
    assert snapshot["result"]["execution"]["environment"] == "OFFLINE_DEMO_TEST_DOUBLE"


def test_the_public_config_advertises_an_offline_sandbox(
    service: CommerceLabService,
) -> None:
    config = service.playground_config()
    assert config["execution"]["adapter"] == "OFFLINE_RAZORPAY_TEST_ADAPTER"
    assert config["execution"]["external_calls"] == 0
    assert config["execution"]["label"] == "SIMULATED OFFLINE ORDER"
    assert config["catalog"]["synthetic"] is True
    assert config["badge"] == "SIMULATED MERCHANT SANDBOX"
    assert config["session"]["purpose"] == "DEMO_SCOPING_NOT_AUTHENTICATION"


def test_no_scenario_can_force_an_allow(service: CommerceLabService) -> None:
    """There must be no path that produces a verdict without the controller.

    The service exposes runs, never decisions. If a `force`, `assume` or
    `expected` argument ever reaches the authorization path, this is where it
    should become visible.
    """

    import inspect

    source = inspect.getsource(type(service).playground_scenario)
    for forbidden in ("DecisionAction.ALLOW", "= \"ALLOW\"", "final_action ="):
        assert forbidden not in source
    plan_source = inspect.getsource(type(service)._start_planned_run)
    assert "decision" not in plan_source.lower()
    assert DecisionAction.ALLOW.value == "ALLOW"


def test_the_outcome_report_is_read_relative_to_the_service_root() -> None:
    """A service pointed at another tree must not render this repository's numbers.

    The measured mix describes one generated world evaluated in one checkout.
    Serving it from a deployment that does not carry it would be presenting a
    measurement of somewhere else as a measurement of here.
    """

    root = Path(tempfile.mkdtemp(prefix="mandateguard-empty-root-"))
    shutil.copytree("fixtures/agentic_commerce", root / "fixtures" / "agentic_commerce")
    shutil.copytree("fixtures/recovery", root / "fixtures" / "recovery")
    try:
        with CommerceLabService(
            repository_root=root, state_dir=root / "state"
        ) as elsewhere:
            config = elsewhere.playground_config()
            # The sandbox itself is generated from source and is always present.
            assert config["catalog"]["products"] > 2000
            # The report is not, and is reported as absent rather than borrowed.
            assert config["outcome_health"] is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_playground_works_without_the_historical_marketplace_catalog() -> None:
    """The judge journey must not depend on the 12 MB discovery artifacts."""

    root = Path(tempfile.mkdtemp(prefix="mandateguard-no-catalog-"))
    shutil.copytree("fixtures/agentic_commerce", root / "fixtures" / "agentic_commerce")
    shutil.copytree("fixtures/recovery", root / "fixtures" / "recovery")
    try:
        with CommerceLabService(
            repository_root=root, state_dir=root / "state"
        ) as lean:
            assert lean.discovery.available is False
            session = lean.open_judge_session()["session_id"]
            intent = "Buy a desk lamp under INR 2,000. No subscriptions."
            payload = lean.playground_search(
                intent=intent, top_k=6, session_id=session
            )
            assert payload["candidates"]
            run, _deduplicated, _session = lean.playground_authorize(
                intent=intent,
                catalog_product_id=payload["candidates"][0]["catalog_product_id"],
                request_id="lean_" + uuid4().hex,
                session_id=session,
            )
            assert run.completion.wait(60)
            result = run.snapshot()["result"]
            assert result["decision"] in {"ALLOW", "BLOCK", "REVIEW"}
            assert result["execution"]["external_network_calls"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_busy_visitor_keeps_access_to_their_own_older_runs(
    service: CommerceLabService, no_network: None
) -> None:
    """The session on the run is the authority, not a bounded recency list.

    A visitor's run list is capped, so a session that starts more runs than the
    cap drops its oldest identifiers. If access were gated on that list, the
    visitor would be told their own first run belonged to somebody else.
    """

    session = session_id(service)
    intent = "Buy a desk lamp under INR 3,000. No subscriptions."
    payload = service.playground_search(intent=intent, top_k=6, session_id=session)
    product_id = payload["candidates"][0]["catalog_product_id"]
    first, _deduplicated, _session = service.playground_authorize(
        intent=intent,
        catalog_product_id=product_id,
        request_id="busy_first_" + uuid4().hex,
        session_id=session,
    )
    assert first.completion.wait(60)

    live = service.playground.sessions.get(session)
    for index in range(MAX_RUNS_PER_SESSION + 2):
        service.playground.sessions.record_run(live, f"run_{index:032x}")
    assert not service.playground.sessions.owns_run(live, first.run_id)

    # Still theirs, and still refused to everybody else.
    service.authorize_run_access(first, session)
    with pytest.raises(PermissionError):
        service.authorize_run_access(first, session_id(service))
