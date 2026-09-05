"""An unrelated product with perfect paperwork is still an unrelated product.

Before this was enforced, MandateGuard would authorize the following, and did:

    "Buy a desk lamp under 2,000."      -> an office monitor riser  -> ALLOW
    "Buy wireless headphones under 5,000." -> an office chair       -> ALLOW
    "Buy running shoes under 6,000."    -> a desk lamp              -> ALLOW
    "Buy a water bottle under 2,000."   -> a keyboard               -> ALLOW
    "Buy a camera under 60,000."        -> a backpack               -> ALLOW

Every one of those was ALLOW with A2 PASS and no finding at all. Nothing was
broken in the sense the checks understood: the price matched the catalogue, the
merchant owned the SKU, the evidence was complete and current, and the total was
inside the ceiling. The purchase was simply not the one the person asked for,
and no check was looking at that.

The repair is a deterministic product-family constraint, read from the frozen
category vocabulary, bound into the signed mandate, and compared by A2 against
the family recorded in the *committed server-side catalogue*. Two properties of
that arrangement are what these tests are here to keep:

**Nothing about the selected product can widen it.** The mandate is built before
a product is chosen, and the comparison reads the catalogue, not the listing
text, not the ranker's opinion, and not a model's score. A ranker is free to
propose anything; it cannot make the proposal authorized.

**Not knowing is not the same as agreeing.** Where the server owns no family for
a selected SKU, A2 records NOT_EVALUABLE and the run reaches REVIEW. It never
falls through to PASS.

The forced-substitution matrix below drives the substitute straight into the
ordinary server-owned selection path by identifier, so it cannot be defeated by
a ranker that happens to behave on the day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator
from uuid import uuid4

import pytest

from mandateguard.intelligence.orchestration import build_mandate_from_intent
from mandateguard.product.service import CommerceLabService
from mandateguard.sandbox.intent import read_intent
from mandateguard.sandbox.scenarios import SCENARIOS
from mandateguard.sandbox.templates import BRANDS, EvidenceFamily
from mandateguard.sandbox.universe import TAXONOMY_CATEGORY_IDS


#: (instruction, family the words name, family substituted in its place).
#: Straight from the release report, plus the desk-lamp case that exposed it.
SUBSTITUTIONS: tuple[tuple[str, str, str], ...] = (
    ("Buy a desk lamp under 2000.", "lighting-desk-lamps", "office-accessories"),
    ("Buy wireless headphones under 5000.", "audio-headphones", "furniture-office-chairs"),
    ("Buy running shoes under 6000.", "footwear-running", "lighting-desk-lamps"),
    ("Buy a water bottle under 2000.", "drinkware-water-bottles", "computing-keyboards"),
    ("Buy a camera under 60000.", "cameras", "bags-backpacks"),
)


@pytest.fixture(scope="module")
def service() -> Iterator[CommerceLabService]:
    state_dir = Path(tempfile.mkdtemp(prefix="mandateguard-product-family-"))
    instance = CommerceLabService(state_dir=state_dir)
    try:
        yield instance
    finally:
        instance.close()
        shutil.rmtree(state_dir, ignore_errors=True)


def evidence_complete_listing(service: CommerceLabService, category_id: str, ceiling: int):
    """An in-budget, non-recurring listing whose published evidence is complete.

    Chosen by a property of the world - what it costs and what its merchant
    published - never by an expected outcome. Nothing here knows what will
    block.
    """

    for product in service.playground._universe.products:  # noqa: SLF001
        if product.category_id != category_id:
            continue
        if product.evidence_family is not EvidenceFamily.COMPLETE:
            continue
        if product.recurring or product.price_minor > ceiling:
            continue
        return product
    raise AssertionError(f"no evidence-complete listing in {category_id} under {ceiling}")


def authorize(service: CommerceLabService, *, intent: str, catalog_product_id: str) -> dict[str, Any]:
    session = service.open_judge_session()["session_id"]
    run, _deduplicated, _session = service.playground_authorize(
        intent=intent,
        catalog_product_id=catalog_product_id,
        request_id="family_" + uuid4().hex,
        session_id=session,
    )
    assert run.completion.wait(60), "the authorization run did not finish"
    return service.playground_run_snapshot(run)


def tier_a_row(snapshot: dict[str, Any], family: str) -> dict[str, Any]:
    rows = snapshot["result"]["authorization"]["deterministic"]["tier_a"]
    return next(row for row in rows if row["family"] == family)


# --------------------------------------------------------------- the invariant


@pytest.mark.parametrize(
    ("instruction", "requested", "substituted"),
    SUBSTITUTIONS,
    ids=[f"{item[1]}->{item[2]}" for item in SUBSTITUTIONS],
)
def test_an_unrelated_product_family_is_never_authorized(
    service: CommerceLabService, instruction: str, requested: str, substituted: str
) -> None:
    """The whole release-blocking matrix, driven by identifier, not by ranking."""

    reading = service.playground.read(instruction)
    assert reading.product_family_allowlist == (requested,), (
        "the instruction no longer names the family this case is about"
    )
    listing = evidence_complete_listing(service, substituted, reading.max_total_minor)
    snapshot = authorize(
        service, intent=instruction, catalog_product_id=listing.catalog_product_id
    )

    assert snapshot["result"]["decision"] == "BLOCK"
    row = tier_a_row(snapshot, "A2")
    assert row["status"] == "FAIL"
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0
    assert snapshot["result"]["execution"]["external_network_calls"] == 0


@pytest.mark.parametrize(
    ("instruction", "requested", "substituted"),
    SUBSTITUTIONS,
    ids=[f"{item[1]}->{item[2]}" for item in SUBSTITUTIONS],
)
def test_the_same_listing_passes_a2_when_its_family_is_the_one_asked_for(
    service: CommerceLabService, instruction: str, requested: str, substituted: str
) -> None:
    """The block is the mismatch, not the listing.

    Each substituted listing above is re-offered against an instruction that
    names *its* family. Same SKU, same merchant, same evidence, same price -
    only the requested family differs. A2 passes, which is what makes the
    failure above a statement about authorization rather than about a listing
    that was somehow defective.
    """

    reading = service.playground.read(instruction)
    listing = evidence_complete_listing(service, substituted, reading.max_total_minor)
    matching = f"Buy a {listing.category_label.lower()} under 100000."
    matching_reading = service.playground.read(matching)
    assert matching_reading.product_family_allowlist is not None
    assert substituted in matching_reading.product_family_allowlist

    snapshot = authorize(
        service, intent=matching, catalog_product_id=listing.catalog_product_id
    )
    assert tier_a_row(snapshot, "A2")["status"] == "PASS"


def test_the_constraint_is_carried_by_the_mandate_the_controller_signed(
    service: CommerceLabService,
) -> None:
    """A2 reads the mandate, so the mandate has to be where the family lives.

    A constraint that only existed in the reading, or only in the trace, would
    be a description of an intention rather than a limit on an authorization.
    This follows it the whole way: reading, interpreted intent, the hard
    constraints of a built mandate, and the trace the finished run recorded.
    """

    instruction = "Buy a desk lamp under 2000."
    interpreted = service.playground.read(instruction).interpreted()
    assert interpreted.product_family_allowlist == ("lighting-desk-lamps",)
    assert interpreted.to_mapping()["product_family_allowlist"] == ["lighting-desk-lamps"]

    mandate = build_mandate_from_intent(
        user_intent=instruction,
        interpreted=interpreted,
        evaluated_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        mandate_identity_seed="product-family-regression",
    )
    assert mandate.payload.constraints.hard.product_family_allowlist == (
        "lighting-desk-lamps",
    )

    listing = evidence_complete_listing(service, "office-accessories", 200_000)
    snapshot = authorize(
        service, intent=instruction, catalog_product_id=listing.catalog_product_id
    )
    recorded = snapshot["result"]["raw_trace"]["buyer"]["interpreted_intent"]
    assert recorded["product_family_allowlist"] == ["lighting-desk-lamps"]


def test_a_family_the_sandbox_does_not_stock_authorizes_nothing(
    service: CommerceLabService,
) -> None:
    """"Smartphone" is understood, and every substitute for it is a substitute.

    The empty allowlist is a constraint, not a missing one. Reading it as
    "no family stated" is precisely the bug this file exists to prevent.
    """

    instruction = "Buy a smartphone under 50000."
    reading = service.playground.read(instruction)
    assert reading.product_family_allowlist == ()
    assert reading.product_family_available is False

    listing = evidence_complete_listing(service, "audio-headphones", reading.max_total_minor)
    snapshot = authorize(
        service, intent=instruction, catalog_product_id=listing.catalog_product_id
    )
    assert snapshot["result"]["decision"] == "BLOCK"
    assert tier_a_row(snapshot, "A2")["status"] == "FAIL"


def test_an_instruction_that_names_no_product_asserts_no_family_constraint(
    service: CommerceLabService,
) -> None:
    """Deliberate, and pinned so it stays deliberate.

    Nothing was requested, so nothing was contradicted, and A2 passes on the
    family. This is the one shape where an unrelated product is not a
    substitution: there is no product family to be unrelated *to*. Every other
    constraint - ceiling, recurrence, exclusions, merchant binding - still
    applies in full.
    """

    instruction = "Buy something nice under 50000."
    reading = service.playground.read(instruction)
    assert reading.product_family_allowlist is None

    listing = evidence_complete_listing(service, "audio-headphones", reading.max_total_minor)
    snapshot = authorize(
        service, intent=instruction, catalog_product_id=listing.catalog_product_id
    )
    assert tier_a_row(snapshot, "A2")["status"] == "PASS"


def test_a_word_that_names_several_shelves_authorizes_all_of_them(
    service: CommerceLabService,
) -> None:
    """"Shoes" means both footwear shelves, and must not be narrowed to one.

    Guessing which shelf the person meant would refuse ordinary purchases the
    instruction plainly permits. Widening to the set the word honestly covers
    is safe; the shelves it does not cover are still refused.
    """

    reading = service.playground.read("Buy shoes under 6000.")
    assert reading.product_family_allowlist == ("footwear-casual", "footwear-running")

    for category_id in reading.product_family_allowlist:
        listing = evidence_complete_listing(service, category_id, reading.max_total_minor)
        snapshot = authorize(
            service,
            intent="Buy shoes under 6000.",
            catalog_product_id=listing.catalog_product_id,
        )
        assert tier_a_row(snapshot, "A2")["status"] == "PASS", category_id

    unrelated = evidence_complete_listing(service, "computing-keyboards", 600_000)
    snapshot = authorize(
        service, intent="Buy shoes under 6000.", catalog_product_id=unrelated.catalog_product_id
    )
    assert tier_a_row(snapshot, "A2")["status"] == "FAIL"


def test_an_unknown_family_reaches_review_rather_than_passing(
    service: CommerceLabService,
) -> None:
    """A listing whose family nothing server-side vouches for is not agreement.

    A simulated merchant onboarded from a crawled marketplace row publishes a
    price, a billing model, a content classification and intended uses - and
    every one of those clears here. What it cannot publish is a server-owned
    product family, because its only category words came off the crawled page.
    Trusting those would hand the substitution back to whoever writes the
    title, so A2 records the identity as unavailable and the run stops at
    REVIEW.
    """

    if not service.discovery.available:
        pytest.skip("the historical discovery catalog is not built in this checkout")
    instruction = "Buy a study lamp under INR 2,000. No subscriptions."
    candidates = service.discovery_search(intent=instruction, top_k=4)["candidates"]
    if not candidates:
        pytest.skip("no marketplace candidate to onboard")

    session = service.open_judge_session()["session_id"]
    created = service.playground_onboard(
        intent=instruction,
        catalog_product_id=candidates[0]["catalog_product_id"],
        declaration={
            "merchant_display_name": "Family Boundary Test",
            "price_minor": 99_900,
            "billing_model": "ONE_TIME",
            "content_classification": "NO_RESTRICTED_CONTENT",
            "purposes": ["individual study", "home use"],
        },
        session_id=session,
    )
    assert created["product"]["category_id"] not in TAXONOMY_CATEGORY_IDS

    run, _deduplicated, _session = service.playground_authorize(
        intent=instruction,
        catalog_product_id=created["product"]["catalog_product_id"],
        request_id="family_onboard_" + uuid4().hex,
        session_id=session,
    )
    assert run.completion.wait(60)
    snapshot = service.playground_run_snapshot(run)
    assert snapshot["result"]["decision"] == "REVIEW"
    row = tier_a_row(snapshot, "A2")
    assert row["status"] == "NOT_EVALUABLE"
    assert row["reason"] == (
        "server-owned product-family identity unavailable for selected SKU"
    )
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0


# ------------------------------------------------------- how it is derived


def test_the_family_is_read_from_the_words_alone(service: CommerceLabService) -> None:
    """No model, no merchant prose, and nothing about the chosen product.

    The reader runs before any listing exists, so there is nothing for a
    ranked result to contribute. Reading the same instruction twice gives the
    same answer, and reading it through the low-level reader gives the same
    answer as reading it through the surface - there is no second path where
    the constraint could go missing.
    """

    instruction = "Buy a desk lamp under 2000."
    direct = read_intent(instruction, known_brands=BRANDS)
    through_surface = service.playground.read(instruction)
    assert direct.product_family_allowlist == ("lighting-desk-lamps",)
    assert through_surface.product_family_allowlist == direct.product_family_allowlist
    assert through_surface.product_family_match == direct.product_family_match

    again = read_intent(instruction, known_brands=BRANDS)
    assert again.product_family_allowlist == direct.product_family_allowlist


def test_every_authorized_family_is_one_the_catalogue_actually_defines(
    service: CommerceLabService,
) -> None:
    """A constraint naming a category the catalogue never files anything under
    would block every selection for reasons no one could act on."""

    for instruction, requested, _substituted in SUBSTITUTIONS:
        allowlist = service.playground.read(instruction).product_family_allowlist
        assert allowlist is not None
        for category_id in allowlist:
            assert category_id in TAXONOMY_CATEGORY_IDS, category_id
        assert requested in allowlist


def test_a_generated_listing_reports_the_family_its_catalogue_filed_it_under(
    service: CommerceLabService,
) -> None:
    """A2 compares against the committed catalogue, so that is what must carry it."""

    for product in list(service.playground._universe.products)[:200]:  # noqa: SLF001
        commerce = product.commerce_product()
        assert commerce.product_family == product.category_id
        assert commerce.product_family in TAXONOMY_CATEGORY_IDS


# ------------------------------------------------------- the guided journeys


def test_every_guided_journey_proposes_a_product_its_own_mandate_permits(
    service: CommerceLabService,
) -> None:
    """The Evidence Conflict journey once offered a monitor riser for a desk lamp.

    It reached REVIEW, so it looked like it was working. It was demonstrating
    the wrong thing: an unrelated product, not the conflicting paperwork the
    journey is named for. Every sandbox journey now has to propose something
    the instruction it ships with actually authorizes.
    """

    checked = 0
    for scenario in SCENARIOS:
        if scenario.world != "SANDBOX":
            continue
        intent, product = service.playground.scenario_selection(scenario)
        if intent.product_family_allowlist is None:
            continue
        assert product.category_id in intent.product_family_allowlist, (
            f"{scenario.scenario_id} proposes {product.category_id!r} for a mandate "
            f"that authorizes {intent.product_family_allowlist!r}"
        )
        checked += 1
    assert checked >= 5, "the sandbox journeys stopped naming product families"


def test_the_evidence_conflict_journey_is_about_conflicting_evidence(
    service: CommerceLabService,
) -> None:
    """A desk lamp, in budget, from the merchant that owns it - and REVIEW anyway."""

    scenario = next(item for item in SCENARIOS if item.scenario_id == "evidence-conflict")
    intent, product = service.playground.scenario_selection(scenario)

    assert intent.product_family_allowlist == ("lighting-desk-lamps",)
    assert product.category_id == "lighting-desk-lamps"
    assert product.evidence_family is EvidenceFamily.AUTHORITY_CONFLICT
    assert intent.max_total_minor is not None
    assert product.price_minor <= intent.max_total_minor

    session = service.open_judge_session()["session_id"]
    run, _deduplicated, _session, _scenario = service.playground_scenario(
        scenario_id="evidence-conflict",
        request_id="family_conflict_" + uuid4().hex,
        session_id=session,
    )
    assert run.completion.wait(60)
    snapshot = service.playground_run_snapshot(run)

    assert snapshot["result"]["decision"] == "REVIEW"
    # Product family, price, merchant binding and SKU ownership all clear. The
    # only thing that does not resolve is the merchant's own billing record.
    deterministic = snapshot["result"]["authorization"]["deterministic"]
    unresolved = [
        row
        for row in deterministic["tier_a"] + deterministic["tier_b"]
        if row["status"] != "PASS"
    ]
    assert unresolved == [], unresolved
    conflict_text = " ".join(
        entry["text"] for entry in snapshot["playground_selection"]["trusted_evidence"]
    )
    assert "the two registered records disagree" in conflict_text
    assert snapshot["result"]["execution"]["razorpay_calls"] == 0
