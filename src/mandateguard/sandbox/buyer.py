"""The sandbox's commerce agent: it proposes, and it proposes only.

The buyer's job is to turn "a desk lamp under two thousand" plus one chosen
listing into a typed proposal naming a merchant, a SKU, a quantity and a total.
It reaches the catalogue through the same four-function commerce tool boundary
the registered buyer uses, so it can look products up and read merchant evidence
and can do nothing else. It holds no payment authority of any kind.

One deliberate difference from the registered-fixture buyer is worth stating,
because it looks at first like a weakening and is the opposite. That buyer
refuses to propose a listing that breaks the interpreted budget or recurrence
filters, and raises. In a demo that turns "you chose a product above your
budget" into a run error instead of into the BLOCK it actually is. This buyer
proposes what the person selected and lets the controller answer, which is both
the more honest architecture - an agent's optimism is exactly what MandateGuard
exists to check - and the only way somebody can watch a budget violation being
stopped rather than being told the run failed.

The identity is still pinned: prices come from the trusted store rather than the
browser, and ``require_selected_product`` runs on every output, so a proposal
that does not match the server-resolved merchant and SKU never reaches
authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mandateguard.intelligence.buyer import BuyerError, require_selected_product
from mandateguard.intelligence.models import (
    BuyerOutput,
    InterpretedPurchaseIntent,
    SelectedProductIdentity,
)
from mandateguard.intelligence.tools import BuyerDraft, CommerceTools

from mandateguard.sandbox.intent import SandboxIntent


BUYER_MODEL_ID = "sandbox-deterministic-buyer-v1"


@dataclass(frozen=True, slots=True)
class SandboxBuyer:
    """Propose exactly the listing the person chose, priced from the store."""

    tools: CommerceTools
    intent: SandboxIntent
    model_id: str = BUYER_MODEL_ID

    def __post_init__(self) -> None:
        if not isinstance(self.tools, CommerceTools):
            raise TypeError("tools must be CommerceTools")
        if not isinstance(self.intent, SandboxIntent):
            raise TypeError("intent must be SandboxIntent")

    def purchase(
        self,
        user_intent: str,
        *,
        selected_product: SelectedProductIdentity | None = None,
    ) -> BuyerOutput:
        if selected_product is None:
            raise BuyerError(
                "the sandbox buyer proposes the listing a person selected; "
                "no selection was supplied"
            )
        found = self.tools.dispatch(
            "get_product",
            {"merchant_id": selected_product.merchant_id, "sku": selected_product.sku},
        )
        product = found.get("product") if isinstance(found, Mapping) else None
        if not isinstance(product, Mapping):
            raise BuyerError("the selected sandbox listing could not be resolved")

        interpreted: InterpretedPurchaseIntent = self.intent.interpreted(
            merchant_allowlist=(str(product["merchant_id"]),),
            sku_allowlist=(str(product["sku"]),),
        )
        evidence_result = self.tools.dispatch(
            "get_merchant_evidence",
            {"merchant_id": product["merchant_id"], "sku": product["sku"]},
        )
        evidence = (
            evidence_result.get("evidence")
            if isinstance(evidence_result, Mapping)
            else None
        )
        if not isinstance(evidence, list) or not evidence:
            raise BuyerError("the selected listing has no registered evidence")

        draft = self.tools.dispatch(
            "propose_purchase",
            {
                "interpreted_intent": interpreted.to_mapping(),
                "proposal": {
                    "merchant_id": product["merchant_id"],
                    "sku": product["sku"],
                    "quantity": interpreted.quantity,
                    # Priced from the trusted store. A browser-supplied price is
                    # never consulted, so mutating one changes nothing.
                    "declared_total_minor": (
                        int(product["effective_unit_price_minor"]) * interpreted.quantity
                    ),
                    "currency": product["currency"],
                    "reason": (
                        "Chosen from the sandbox catalogue for this instruction. "
                        "Price and terms re-read from the merchant record."
                    ),
                    "selected_evidence_ids": [item["evidence_id"] for item in evidence],
                    "user_intent_summary": user_intent.strip()[:1000],
                },
            },
        )
        if not isinstance(draft, BuyerDraft):
            raise BuyerError("the sandbox buyer did not produce a typed proposal")
        output = BuyerOutput(
            proposal=draft.proposal,
            interpreted_intent=draft.interpreted_intent,
            model_id=self.model_id,
        )
        require_selected_product(output, selected_product)
        return output
