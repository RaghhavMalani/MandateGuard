"""The mandate the browser draws must be the mandate the controller was given.

A public black-box review found the workspace printing "One-time payment only"
for an instruction that stated no recurrence at all. The controller had parsed
no such constraint; the table invented it. A reader who checks the interface
against their own words would have been told MandateGuard was enforcing
something it was not, and the only reason nothing worse happened is that the
table decides nothing.

Two things guard against that returning. This module pins what the *reader*
produces for each recurrence stance, and writes those payloads into
``fixtures/playground/mandate_render_cases.json``. ``tests/ui/playground.test.mjs``
renders the browser's mandate table from that same file. Neither side can drift
without the other failing: change the reader and this test fails, change the
table and the UI test fails, and no hand-written object stands between them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandateguard.sandbox.intent import read_intent
from mandateguard.sandbox.templates import BRANDS


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "playground" / "mandate_render_cases.json"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_render_fixture_is_what_the_reader_produces_today(fixture: dict) -> None:
    """Every payload the browser is tested against came out of the real reader."""

    assert fixture["fixture_version"] == "playground-mandate-render-v1"
    assert fixture["cases"], "the fixture carries no cases"
    for case in fixture["cases"]:
        intent = read_intent(case["instruction"], known_brands=BRANDS)
        assert intent.to_mapping() == case["mandate"], (
            f"{case['case_id']}: the reader no longer produces the payload the "
            "browser is tested against. Regenerate the fixture and re-check the "
            "rendered wording."
        )


def test_a_bare_instruction_states_no_recurrence_constraint() -> None:
    """"Buy wireless headphones under 5000" restricts billing not at all.

    ``recurring_allowed`` is False here because silence is not consent to be
    billed again next month - the mandate does not permit a renewing charge.
    But no recurrence constraint was *stated*, and those are different claims.
    The interface has to read ``recurrence_stated`` to tell them apart, which
    is exactly the distinction the false "One-time payment only" collapsed.
    """

    intent = read_intent("Buy wireless headphones under 5000", known_brands=BRANDS)
    assert intent.recurrence_stated is False
    assert intent.recurring_allowed is False
    mapping = intent.to_mapping()
    assert mapping["recurrence_stated"] is False
    assert mapping["recurring_allowed"] is False


def test_an_explicit_refusal_of_subscriptions_states_one_time_only() -> None:
    intent = read_intent(
        "Buy wireless headphones under 5000. No subscriptions.", known_brands=BRANDS
    )
    assert intent.recurrence_stated is True
    assert intent.recurring_allowed is False


def test_an_accepted_subscription_states_recurrence_is_allowed() -> None:
    intent = read_intent(
        "Buy a streaming subscription under 500. A monthly subscription is fine.",
        known_brands=BRANDS,
    )
    assert intent.recurrence_stated is True
    assert intent.recurring_allowed is True


def test_the_mandate_payload_carries_the_fields_the_table_reads() -> None:
    """The browser reads these five keys. Renaming one silently empties a row."""

    mapping = read_intent(
        "Buy a desk lamp under 2000. No subscriptions.", known_brands=BRANDS
    ).to_mapping()
    for key in (
        "recurrence_stated",
        "recurring_allowed",
        "product_family",
        "max_total_minor",
        "exclusions",
    ):
        assert key in mapping, key
    family = mapping["product_family"]
    assert family is not None
    for key in ("label", "allowed_category_ids", "available_in_sandbox"):
        assert key in family, key


def test_an_instruction_naming_no_product_family_asserts_none() -> None:
    """No family stated is None, not an empty allowlist.

    The difference decides an authorization. None asserts no family constraint
    at all; an empty tuple asserts that no family is authorized and blocks
    every selection. An instruction that simply names no product must produce
    the first, or an ordinary vague request would become unpurchasable.
    """

    mapping = read_intent("Buy something nice under 2000", known_brands=BRANDS).to_mapping()
    assert mapping["product_family"] is None


def test_a_family_absent_from_the_sandbox_asserts_an_empty_allowlist() -> None:
    """"Smartphone" is understood, and authorizes nothing in this world."""

    intent = read_intent("Buy a smartphone under 50000", known_brands=BRANDS)
    assert intent.product_family_allowlist == ()
    assert intent.product_family_available is False
    family = intent.to_mapping()["product_family"]
    assert family["available_in_sandbox"] is False
    assert family["allowed_category_ids"] == []
