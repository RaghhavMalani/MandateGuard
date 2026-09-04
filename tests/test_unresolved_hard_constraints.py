"""A hard restriction a person typed can never quietly become no restriction.

The intent reader recognises a closed set of phrasings. That is the right
design - inventing constraints from unrecognised text is how a parser starts
authorizing purchases nobody sanctioned - but it has a failure mode that is
invisible from inside the parser: an instruction can state a restriction the
parser has no pattern for, and the resulting mandate simply does not carry it.

"no leather" is read as an exclusion and reaches Tier C. "vegan materials only"
matched nothing, produced nothing, and once left behind a mandate that
authorized any backpack in the catalogue. Same buyer, same kind of restriction,
opposite safety outcome, and no signal anywhere that the second one had been
dropped.

These tests hold that line permanently. They are written against the *outcome*
- no ALLOW, no capability, no adapter call - rather than against the cue list,
so that widening the recognised grammar later is free while removing the
guarantee is not.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from mandateguard.product.playground import PlaygroundError
from mandateguard.product.service import CommerceLabService
from mandateguard.sandbox.coverage import (
    AMBIGUOUS,
    COMPLETE,
    UNRESOLVED_HARD_CONSTRAINT,
    assess_coverage,
)
from mandateguard.sandbox.intent import SandboxIntentError, read_intent
from mandateguard.sandbox.scenarios import SCENARIOS
from mandateguard.sandbox.templates import BRANDS


@pytest.fixture
def service(tmp_path: Path):
    instance = CommerceLabService(state_dir=tmp_path / "state")
    try:
        yield instance
    finally:
        instance.close()


#: Restrictions the parser has no enforceable mapping for. Each one is a hard
#: requirement in plain English, and none of them may reach an ALLOW.
UNRESOLVED_INSTRUCTIONS: tuple[tuple[str, str], ...] = (
    ("Find a backpack under INR 3,000, vegan materials only.", "vegan materials only"),
    ("Buy headphones under INR 8,000, nothing refurbished.", "nothing refurbished"),
    ("Get a laptop stand under INR 3,000, must be aluminium.", "must be aluminium"),
    ("Buy a power bank under INR 2,000, USB-C only.", "USB-C only"),
    ("Buy running shoes under INR 6,000, waterproof only.", "waterproof only"),
    ("Buy a desk lamp under INR 2,000, warm light only.", "warm light only"),
)

#: Instructions whose every restriction *is* recognised. These must keep
#: working exactly as before: ordinary prose must not start asking questions.
RESOLVED_INSTRUCTIONS: tuple[str, ...] = (
    "Buy wireless headphones under INR 5,000. No subscriptions.",
    "Buy a desk lamp under INR 2,000.",
    "Find running shoes below INR 6,000.",
    "Buy shoes under INR 5,000, no leather.",
    "Get a smartwatch under INR 10,000, no Brand X.",
    "Find a gift under INR 3,000, nothing involving alcohol.",
    "Find a finance course under INR 4,000, no crypto.",
)


# ---------------------------------------------------------------------------
# What the coverage layer reports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("instruction", "quoted"), UNRESOLVED_INSTRUCTIONS)
def test_an_unrecognised_restriction_is_reported_not_dropped(
    instruction: str, quoted: str
) -> None:
    intent = read_intent(instruction, known_brands=BRANDS)
    coverage = intent.coverage
    assert coverage.coverage_status == UNRESOLVED_HARD_CONSTRAINT
    assert coverage.blocks_authorization is True
    assert quoted in coverage.quoted
    # The words are quoted back verbatim. A person can only clarify a
    # requirement they can recognise as their own.
    assert quoted in coverage.clarification_message()


@pytest.mark.parametrize("instruction", RESOLVED_INSTRUCTIONS)
def test_ordinary_prose_does_not_acquire_a_question(instruction: str) -> None:
    intent = read_intent(instruction, known_brands=BRANDS)
    assert intent.coverage.coverage_status == COMPLETE
    assert intent.coverage.blocks_authorization is False
    assert intent.coverage.unresolved_constraint_spans == ()


def test_a_recognised_exclusion_accounts_for_its_own_cue() -> None:
    """"no leather" must not be reported as unresolved *and* asserted."""

    intent = read_intent("Buy shoes under INR 5,000, no leather.", known_brands=BRANDS)
    assert intent.exclusions == ("leather",)
    assert intent.coverage.coverage_status == COMPLETE
    assert "EXCLUSION: leather" in intent.coverage.recognized_constraints


def test_an_exclusion_the_cleaner_discards_is_still_reported() -> None:
    """A match that produced no constraint may not account for its own cue.

    ``_clean_exclusion`` rejects what it cannot bound, and when it does the
    stated exclusion is gone. Accounting for the pattern match regardless would
    let that drop hide behind the very grammar that failed to keep it.
    """

    # "without a x" matches the exclusion grammar, and then the cleaner strips
    # the article and rejects what is left as too short to be a constraint.
    intent = read_intent(
        "Buy a desk lamp under INR 2,000 without a x", known_brands=BRANDS
    )
    assert intent.exclusions == ()
    assert intent.coverage.blocks_authorization is True


def test_an_unresolved_comparator_is_ambiguous_and_still_blocks() -> None:
    coverage = assess_coverage(
        "Buy a bag over the counter",
        parsed=read_intent(
            "Buy a bag over the counter", declared_ceiling_minor=200_000
        ).parsed,
    )
    assert coverage.coverage_status == AMBIGUOUS
    # Weaker evidence of intent, identical consequence.
    assert coverage.blocks_authorization is True


def test_every_registered_scenario_is_fully_covered() -> None:
    """A shipped scenario may never depend on a dropped restriction."""

    for scenario in SCENARIOS:
        intent = read_intent(
            scenario.intent,
            known_brands=BRANDS,
            declared_ceiling_minor=scenario.declared_ceiling_minor,
        )
        assert intent.coverage.coverage_status == COMPLETE, scenario.scenario_id


def test_the_frozen_judge_query_set_is_fully_covered() -> None:
    """The measured query set must not silently become a set of questions."""

    import json

    root = Path(__file__).resolve().parents[1]
    raw = json.loads(
        (root / "fixtures" / "playground" / "judge_queries.json").read_text(
            encoding="utf-8"
        )
    )
    for query in raw["queries"]:
        intent = read_intent(query["text"], known_brands=BRANDS)
        assert intent.coverage.coverage_status == COMPLETE, query["id"]


# ---------------------------------------------------------------------------
# What the product does about it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("instruction", "quoted"), UNRESOLVED_INSTRUCTIONS)
def test_an_unresolved_restriction_cannot_reach_authorization(
    service: CommerceLabService, instruction: str, quoted: str
) -> None:
    """Search still works. Authorization refuses. Nothing is issued.

    This is the whole finding, held permanently: before the coverage layer
    existed, each of these instructions produced an ALLOW over a product that
    was never checked against the restriction the buyer wrote.
    """

    search = service.playground_search(intent=instruction, top_k=8)
    # Retrieval is advisory and keeps working; hiding the products would teach
    # a person nothing about why they cannot buy one.
    assert search["candidates"]
    assert search["clarification_required"] is True
    assert quoted in search["clarification_message"]
    assert search["constraint_coverage"]["coverage_status"] == UNRESOLVED_HARD_CONSTRAINT

    with pytest.raises(PlaygroundError) as raised:
        service.playground_authorize(
            intent=instruction,
            catalog_product_id=search["candidates"][0]["catalog_product_id"],
            request_id="unresolved_" + uuid4().hex,
            session_id=search["session"]["session_id"],
        )
    assert raised.value.code == "INPUT_CLARIFICATION_REQUIRED"


@pytest.mark.parametrize(("instruction", "_quoted"), UNRESOLVED_INSTRUCTIONS)
def test_a_refused_instruction_creates_no_run_and_no_adapter_call(
    service: CommerceLabService, instruction: str, _quoted: str
) -> None:
    """capability = none, adapter calls = 0, external calls = 0.

    Asserted by absence rather than by inspection: the refusal happens before a
    run object exists, so there is nothing for a capability to be issued
    against and no execution gate to reach.
    """

    search = service.playground_search(intent=instruction, top_k=8)
    before = len(service._runs)
    with pytest.raises(PlaygroundError):
        service.playground_authorize(
            intent=instruction,
            catalog_product_id=search["candidates"][0]["catalog_product_id"],
            request_id="unresolved_" + uuid4().hex,
            session_id=search["session"]["session_id"],
        )
    assert len(service._runs) == before


@pytest.mark.parametrize(("instruction", "_quoted"), UNRESOLVED_INSTRUCTIONS)
def test_the_mandate_builder_refuses_the_same_instruction_independently(
    instruction: str, _quoted: str
) -> None:
    """Defence in depth: the refusal does not depend on the Playground.

    A future caller that reaches ``interpreted()`` by some other route must hit
    the same wall, so the guarantee survives a refactor of the surface above it.
    """

    intent = read_intent(instruction, known_brands=BRANDS)
    with pytest.raises(SandboxIntentError) as raised:
        intent.interpreted()
    assert raised.value.code == "INPUT_CLARIFICATION_REQUIRED"


@pytest.mark.parametrize("instruction", RESOLVED_INSTRUCTIONS)
def test_fully_covered_instructions_still_reach_the_controller(
    service: CommerceLabService, instruction: str
) -> None:
    """The fix may not cost ordinary prose its verdict.

    These reach a real decision. Which decision is the controller's business -
    a stated exclusion with no merchant evidence behind it is a REVIEW, and
    that was already true - but a decision is reached rather than a question
    asked.
    """

    search = service.playground_search(intent=instruction, top_k=8)
    assert search["clarification_required"] is False
    if not search["candidates"]:
        # A category the sandbox does not stock is an honest empty result, not
        # a question about the wording. It must stay the explained no-match it
        # already was.
        assert search["no_match_message"]
        return
    run, _deduplicated, _session = service.playground_authorize(
        intent=instruction,
        catalog_product_id=search["candidates"][0]["catalog_product_id"],
        request_id="covered_" + uuid4().hex,
        session_id=search["session"]["session_id"],
    )
    assert run.completion.wait(30)
    snapshot = service.playground_run_snapshot(run)
    assert snapshot["result"]["decision"] in {"ALLOW", "BLOCK", "REVIEW"}


def test_the_coverage_layer_maps_nothing_onto_meaning() -> None:
    """It reports unaccounted words. It never turns them into a constraint.

    This is the ML-authority line drawn one module further out: an unresolved
    phrase may become a question, and may never become an enforceable
    restriction that something downstream believes was checked.
    """

    intent = read_intent(
        "Find a backpack under INR 3,000, vegan materials only.", known_brands=BRANDS
    )
    assert intent.exclusions == ()
    assert intent.purpose is None
    mapping = intent.to_mapping()
    assert mapping["exclusions"] == []
    assert mapping["coverage"]["authority"] == (
        "NONE_DETECTION_ONLY_NEVER_INTERPRETATION"
    )
    # Nothing named "vegan" became a constraint anywhere in the mandate.
    assert not any(
        "vegan" in str(value).casefold()
        for key, value in mapping.items()
        if key not in {"raw_text", "search_text", "coverage"}
    )
