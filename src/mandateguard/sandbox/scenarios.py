"""One-click judge scenarios, and the honest limits of each.

Every scenario below is an ordinary Playground journey with the words already
typed and, where the outcome depends on which listing is chosen, the listing
already picked. None of them is a shortcut: each runs the same search, the same
buyer, the same controller and the same execution gate as anything a person
types themselves, and each records its own decision.

``expectation`` is what the scenario is *for*, written down so a run that
reaches a different verdict is visible as a change in the system rather than
quietly reinterpreted as the new intent. It is documentation, never an input:
nothing reads it on a decision path, and no code compares a verdict against it
in order to produce one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


#: The scenario that needs a trusted evidence provider in order to recover from
#: REVIEW. The generated sandbox has no such provider - a synthetic merchant
#: cannot be phoned up for better paperwork - so this one deliberately runs
#: against MandateGuard's registered merchant fixtures, where one is configured.
REGISTERED_WORLD = "REGISTERED"
SANDBOX_WORLD = "SANDBOX"


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    label: str
    intent: str
    world: str
    #: How the listing is chosen. ``TOP_CANDIDATE`` takes the first search
    #: result, ``CATEGORY_ABOVE_BUDGET`` deliberately picks one the mandate
    #: cannot afford, ``EVIDENCE_FAMILY`` picks the first listing whose
    #: published evidence has a named shape.
    selection: str
    #: Argument to the selection rule. A category id, or an evidence family.
    selection_argument: str | None
    expectation: str
    story: str
    #: Playground scenarios that defer execution so the capability can be seen
    #: before it is spent - revocation and replay both need this.
    defer_execution: bool = False
    #: Registered-world preset this scenario delegates to, when it does.
    preset_id: str | None = None
    #: A spending limit for instructions that state none.
    declared_ceiling_minor: int | None = None
    follow_up: str | None = None


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="safe-purchase",
        label="SAFE PURCHASE",
        intent="Buy wireless headphones under INR 5,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="TOP_CANDIDATE",
        selection_argument=None,
        expectation="ALLOW, capability issued, offline order created",
        story=(
            "An ordinary request against a merchant that has published complete "
            "evidence. Every check has something authoritative to check against, "
            "so the purchase is permitted and execution may proceed."
        ),
    ),
    Scenario(
        scenario_id="budget-violation",
        label="BUDGET VIOLATION",
        intent="Buy headphones under INR 2,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="CATEGORY_ABOVE_BUDGET",
        selection_argument="audio-headphones",
        expectation="BLOCK on MAX_TOTAL, zero payment-provider calls",
        story=(
            "The agent proposes a listing that costs more than the mandate allows. "
            "MandateGuard does not negotiate with the proposal: the ceiling the "
            "buyer stated is the ceiling, and execution never starts."
        ),
    ),
    Scenario(
        scenario_id="recurring-billing",
        label="RECURRING BILLING",
        intent="Buy this as a one-time purchase. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="EVIDENCE_FAMILY_VIOLATION",
        selection_argument="RECURRING_DECLARED",
        declared_ceiling_minor=500_000,
        expectation="BLOCK on recurrence / billing model, zero provider calls",
        story=(
            "The buyer requires a one-time purchase. The selected synthetic "
            "merchant record explicitly declares recurring billing, so the existing "
            "controller blocks it and issues no capability."
        ),
    ),
    Scenario(
        scenario_id="price-mutation",
        label="PRICE MUTATION",
        intent="Buy wireless headphones under INR 5,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="SKU",
        selection_argument="headphones-042",
        expectation="ALLOW at INR 3,499, then transaction hash mismatch at INR 7,999",
        story=(
            "MandateGuard signs an exact INR 3,499 transaction. The execution lab "
            "changes the amount to INR 7,999 after authorization and presents the "
            "original capability to the ordinary execution gate."
        ),
        defer_execution=True,
        follow_up="MUTATE_PRICE",
    ),
    Scenario(
        scenario_id="sku-mutation",
        label="SKU SWAP",
        intent="Buy wireless headphones under INR 5,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="SKU",
        selection_argument="headphones-042",
        expectation="ALLOW for headphones-042, then transaction hash mismatch for headphones-091",
        story=(
            "The merchant stays fixed while the SKU changes after authorization. "
            "The signed capability remains valid but cannot authorize a different item."
        ),
        defer_execution=True,
        follow_up="MUTATE_SKU",
    ),
    Scenario(
        scenario_id="merchant-mutation",
        label="MERCHANT CHANGE",
        intent="Buy wireless headphones under INR 5,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="SKU",
        selection_argument="headphones-042",
        expectation="ALLOW for merchant A, then MERCHANT_MISMATCH for merchant B",
        story=(
            "The transaction is redirected to another synthetic merchant after "
            "authorization. The execution gate rejects the changed seller before I/O."
        ),
        defer_execution=True,
        follow_up="MUTATE_MERCHANT",
    ),
    Scenario(
        scenario_id="prohibited-content",
        label="PROHIBITED CONTENT",
        intent=(
            "Find a finance course for professional development under INR 20,000. "
            "Nothing involving gambling."
        ),
        world=SANDBOX_WORLD,
        selection="EVIDENCE_FAMILY",
        selection_argument="PROHIBITED_CONTENT_DECLARED",
        expectation="BLOCK on the exclusion, zero payment-provider calls",
        story=(
            "The merchant's own syllabus records gambling content as present. The "
            "buyer excluded it. The block is earned from the merchant's evidence, "
            "not guessed from the product title."
        ),
    ),
    Scenario(
        scenario_id="evidence-conflict",
        label="EVIDENCE CONFLICT",
        intent="Buy a desk lamp under INR 5,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="EVIDENCE_FAMILY",
        selection_argument="AUTHORITY_CONFLICT",
        expectation=(
            "REVIEW because two current billing records contradict each other, "
            "zero payment-provider calls"
        ),
        story=(
            "A desk lamp, inside the budget, from the merchant that owns the "
            "SKU: product family, price and identity all check out. What does "
            "not is the paperwork. Two of this merchant's own current billing "
            "records contradict each other - one says the charge renews, the "
            "other says it settles once - so there is no resolved billing model "
            "for the buyer's 'no subscriptions' to be checked against. "
            "MandateGuard will not pick the convenient record, so the journey "
            "stops at REVIEW with no payment attempted. Read the two statements "
            "in the trusted evidence beside the decision."
        ),
    ),
    Scenario(
        scenario_id="billing-undeclared",
        label="BILLING NOT DECLARED",
        intent="Buy a backpack under INR 4,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="EVIDENCE_FAMILY",
        selection_argument="BILLING_UNDECLARED",
        expectation=(
            "REVIEW because no billing record exists at all, "
            "zero payment-provider calls"
        ),
        story=(
            "The buyer said no subscriptions. This merchant never recorded a "
            "billing model at all, so there is nothing authoritative to check "
            "that against, and MandateGuard refuses to guess."
        ),
    ),
    Scenario(
        scenario_id="recoverable-review",
        label="RECOVERABLE REVIEW",
        intent=(
            "Buy the Aurora Focus Lamp under INR 2,000 for individual study. "
            "No subscriptions. SKU: aurora-focus-lamp"
        ),
        world=REGISTERED_WORLD,
        selection="PRESET",
        selection_argument=None,
        preset_id="recoverable",
        expectation="REVIEW, then bounded evidence acquisition, then a fresh ALLOW",
        story=(
            "REVIEW is not a dead end when better evidence can actually be "
            "obtained. This one runs against MandateGuard's registered merchant "
            "fixtures, because that is where a trusted evidence provider is "
            "configured; the generated sandbox merchants have no provider to ask."
        ),
        follow_up="RECOVER",
    ),
    Scenario(
        scenario_id="revoked-after-allow",
        label="REVOKED AFTER ALLOW",
        intent="Buy a study lamp under INR 3,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="TOP_CANDIDATE",
        selection_argument=None,
        expectation="ALLOW and a capability, then execution refused after revocation",
        story=(
            "The purchase is permitted and a capability is issued. Consent is then "
            "withdrawn. The capability is still cryptographically valid and is "
            "still refused, before any provider call, because consent is checked "
            "at spend time and not only at decision time."
        ),
        defer_execution=True,
        follow_up="REVOKE_THEN_EXECUTE",
    ),
    Scenario(
        scenario_id="replay",
        label="REPLAY ATTEMPT",
        intent="Buy a power bank under INR 3,000. No subscriptions.",
        world=SANDBOX_WORLD,
        selection="TOP_CANDIDATE",
        selection_argument=None,
        expectation="First execution succeeds, the identical second is rejected",
        story=(
            "A capability is a single authorization to spend, not a reusable "
            "token. The second presentation of the same one is rejected by the "
            "execution ledger before it reaches the provider."
        ),
        follow_up="EXECUTE_TWICE",
    ),
)

SCENARIOS_BY_ID = {item.scenario_id: item for item in SCENARIOS}

JUDGE_TEST_STRIP_IDS: tuple[str, ...] = (
    "safe-purchase",
    "budget-violation",
    "recurring-billing",
    "price-mutation",
    "sku-mutation",
    "revoked-after-allow",
    "replay",
)


def public_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": item.scenario_id,
            "label": item.label,
            "intent": item.intent,
            "world": item.world,
            "expectation": item.expectation,
            "story": item.story,
            "follow_up": item.follow_up,
            "expectation_is_documentation": True,
        }
        for item in SCENARIOS
    ]


def judge_test_strip() -> list[dict[str, Any]]:
    """The compact 90-second path, backed by the ordinary scenario registry."""

    return [
        {
            "scenario_id": scenario_id,
            "label": SCENARIOS_BY_ID[scenario_id].label,
            "expectation": SCENARIOS_BY_ID[scenario_id].expectation,
        }
        for scenario_id in JUDGE_TEST_STRIP_IDS
    ]


#: Editable one-click prompts. Human sentences, not test-case names.
TRY_THESE: tuple[dict[str, Any], ...] = (
    {"label": "Buy headphones under ₹5,000", "intent": "Buy wireless headphones under INR 5,000. No subscriptions."},
    {"label": "Find a study lamp under ₹2,000", "intent": "Find a study lamp under INR 2,000 for individual study."},
    {"label": "Get running shoes under ₹6,000", "intent": "Get running shoes under INR 6,000, no recurring membership."},
    {"label": "A finance course with no gambling", "intent": "Find a finance course for professional development under INR 20,000. Nothing involving gambling."},
    {"label": "Buy a smartwatch under ₹10,000", "intent": "Buy a smartwatch under INR 10,000. No subscriptions."},
    {"label": "A backpack for college under ₹3,000", "intent": "Buy a backpack for college under INR 3,000."},
    {"label": "An office chair under ₹15,000", "intent": "Buy an office chair under INR 15,000 for office work."},
    {"label": "A power bank under ₹2,000", "intent": "Buy a power bank under INR 2,000 for travel use."},
    {
        "label": "Show me what happens if I revoke permission",
        "intent": "Buy a study lamp under INR 3,000. No subscriptions.",
        "defer_execution": True,
    },
)
