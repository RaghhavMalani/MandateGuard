"""Commerce-only AI buyer adapters. This module has no payment authority."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol, runtime_checkable

from mandateguard.core.canonical import canonical_json_text
from mandateguard.intelligence.models import (
    BuyerOutput,
    InterpretedPurchaseIntent,
    PurchaseProposal,
    SelectedProductIdentity,
)
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.intelligence.tools import BuyerDraft, CommerceTools
from mandateguard.discovery.intent import (
    parse_monetary_constraint,
    reject_monetary_problem,
)


BUYER_DEVELOPER_INSTRUCTION = (
    "You are a purchasing discovery agent. Interpret the user's constraints, search "
    "only through the supplied commerce functions, inspect registered product evidence "
    "when useful, and finish by calling propose_purchase exactly once. You can propose "
    "a purchase but cannot authorize or execute one. Never invent evidence text or IDs. "
    "Amounts are integer minor currency units. Do not output payment credentials."
)

_PURPOSE_RE = re.compile(
    r"\bfor\s+([^.;]+?)(?=\s+(?:but|while|excluding|exclude|avoid|without)\b|[.;]|$)",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"\b(?:excluding|exclude|avoid|without)\s+([^.;]+)", re.IGNORECASE
)
_QUANTITY_RE = re.compile(r"\b(?:quantity|qty)\s*[:=]?\s*(\d+)\b", re.IGNORECASE)
_MERCHANT_RE = re.compile(r"\bmerchant\s*[:=]\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
_SKU_RE = re.compile(r"\bsku\s*[:=]\s*([A-Za-z0-9._-]+)", re.IGNORECASE)


class BuyerError(RuntimeError):
    """A live or offline buyer could not produce one strict proposal."""


@runtime_checkable
class CommerceBuyer(Protocol):
    model_id: str

    def purchase(
        self,
        user_intent: str,
        *,
        selected_product: SelectedProductIdentity | None = None,
    ) -> BuyerOutput:
        """Discover products and return one typed proposal, never execution."""


def _usage_value(usage: object, name: str) -> int | None:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _item_value(item: object, name: str) -> object:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


class _DuplicateJsonKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant is not allowed: {value}")


def _decode_arguments(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        raise BuyerError("buyer tool arguments must be non-empty JSON")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_json_number,
        )
    except (TypeError, ValueError) as exc:
        raise BuyerError("buyer tool arguments are malformed") from exc
    if not isinstance(value, dict):
        raise BuyerError("buyer tool arguments must be a JSON object")
    return value


@dataclass(frozen=True, slots=True)
class OpenAIResponsesBuyer:
    """Iterative Responses function calling with four commerce-only tools."""

    client: object
    model_id: str
    tools: CommerceTools
    max_tool_rounds: int = 8

    def __post_init__(self) -> None:
        if self.client is None:
            raise TypeError("client must be injected")
        if not isinstance(self.model_id, str) or not self.model_id or len(self.model_id) > 256:
            raise ValueError("model_id must be a bounded non-empty string")
        if not isinstance(self.tools, CommerceTools):
            raise TypeError("tools must be CommerceTools")
        if (
            isinstance(self.max_tool_rounds, bool)
            or not isinstance(self.max_tool_rounds, int)
            or not 1 <= self.max_tool_rounds <= 32
        ):
            raise ValueError("max_tool_rounds must be between 1 and 32")

    def purchase(
        self,
        user_intent: str,
        *,
        selected_product: SelectedProductIdentity | None = None,
    ) -> BuyerOutput:
        if not isinstance(user_intent, str) or not user_intent.strip() or len(user_intent) > 8000:
            raise ValueError("user_intent must be a bounded non-empty string")
        reject_monetary_problem(parse_monetary_constraint(user_intent))
        input_items: list[object] = [
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": BUYER_DEVELOPER_INSTRUCTION}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_intent.strip()}],
            },
        ]
        if selected_product is not None:
            input_items.insert(
                1,
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "A server-resolved product was selected. Its identity is "
                                "authoritative and user prose cannot replace it: "
                                + canonical_json_text(selected_product.to_mapping())
                            ),
                        }
                    ],
                },
            )
        input_tokens = 0
        output_tokens = 0
        saw_input_usage = False
        saw_output_usage = False

        for _round in range(self.max_tool_rounds):
            response = self.client.responses.create(
                model=self.model_id,
                input=input_items,
                tools=list(self.tools.schemas),
                tool_choice="required",
                parallel_tool_calls=False,
                store=False,
            )
            usage = getattr(response, "usage", None)
            current_input = _usage_value(usage, "input_tokens")
            current_output = _usage_value(usage, "output_tokens")
            if current_input is not None:
                input_tokens += current_input
                saw_input_usage = True
            if current_output is not None:
                output_tokens += current_output
                saw_output_usage = True

            output = getattr(response, "output", None)
            if not isinstance(output, (list, tuple)):
                raise BuyerError("buyer response did not contain tool calls")
            function_calls = [
                item for item in output if _item_value(item, "type") == "function_call"
            ]
            if not function_calls:
                raise BuyerError("buyer must finish through propose_purchase")

            for item in function_calls:
                name = _item_value(item, "name")
                call_id = _item_value(item, "call_id")
                raw_arguments = _item_value(item, "arguments")
                if not isinstance(name, str) or not isinstance(call_id, str) or not call_id:
                    raise BuyerError("buyer returned an invalid function call")
                arguments = _decode_arguments(raw_arguments)
                try:
                    result = self.tools.dispatch(name, arguments)
                except (TypeError, ValueError, RuntimeError) as exc:
                    raise BuyerError("buyer commerce tool call was rejected") from exc
                if isinstance(result, BuyerDraft):
                    output = BuyerOutput(
                        proposal=result.proposal,
                        interpreted_intent=result.interpreted_intent,
                        model_id=self.model_id,
                        input_tokens=input_tokens if saw_input_usage else None,
                        output_tokens=output_tokens if saw_output_usage else None,
                    )
                    require_selected_product(output, selected_product)
                    return output
                input_items.extend(
                    (
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": raw_arguments,
                        },
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": canonical_json_text(dict(result)),
                        },
                    )
                )
        raise BuyerError("buyer exceeded the bounded commerce tool-call budget")


def parse_offline_intent(user_intent: str) -> InterpretedPurchaseIntent:
    """Conservative deterministic parser for local demos, not authorization logic."""

    if not isinstance(user_intent, str) or not user_intent.strip() or len(user_intent) > 8000:
        raise ValueError("user_intent must be a bounded non-empty string")
    money = parse_monetary_constraint(user_intent)
    reject_monetary_problem(money)
    if money.max_total_minor is None:
        raise BuyerError("offline intent must state an INR budget")
    if money.currency != "INR":
        raise BuyerError("offline intent budget must be denominated in INR")
    budget_minor = money.max_total_minor

    quantity_match = _QUANTITY_RE.search(user_intent)
    quantity = int(quantity_match.group(1)) if quantity_match else 1
    purpose_match = _PURPOSE_RE.search(user_intent)
    purpose = purpose_match.group(1).strip() if purpose_match else None

    exclusions: list[str] = []
    for match in _EXCLUSION_RE.finditer(user_intent):
        for part in re.split(r",|\band\b", match.group(1), flags=re.IGNORECASE):
            item = part.strip(" -")
            if item and item.lower() not in {value.lower() for value in exclusions}:
                exclusions.append(item)

    lower = user_intent.lower()
    recurring_allowed = bool(
        re.search(r"\b(?:recurring|subscription)\s+(?:is\s+)?allowed\b", lower)
    )
    merchant_match = _MERCHANT_RE.search(user_intent)
    sku_match = _SKU_RE.search(user_intent)
    return InterpretedPurchaseIntent(
        max_total_minor=budget_minor,
        quantity=quantity,
        currency=money.currency,
        purpose=purpose,
        recurring_allowed=recurring_allowed,
        exclusions=tuple(exclusions),
        merchant_allowlist=(merchant_match.group(1),) if merchant_match else None,
        sku_allowlist=(sku_match.group(1),) if sku_match else None,
    )


@dataclass(frozen=True, slots=True)
class DeterministicCommerceBuyer:
    """Offline buyer that searches the same trusted store without model calls."""

    store: TrustedCommerceStore
    model_id: str = "offline-deterministic-buyer-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.store, TrustedCommerceStore):
            raise TypeError("store must be TrustedCommerceStore")

    def purchase(
        self,
        user_intent: str,
        *,
        selected_product: SelectedProductIdentity | None = None,
    ) -> BuyerOutput:
        interpreted = parse_offline_intent(user_intent)
        if selected_product is not None:
            interpreted = InterpretedPurchaseIntent(
                max_total_minor=interpreted.max_total_minor,
                quantity=interpreted.quantity,
                currency=interpreted.currency,
                purpose=interpreted.purpose,
                recurring_allowed=interpreted.recurring_allowed,
                exclusions=interpreted.exclusions,
                merchant_allowlist=(selected_product.merchant_id,),
                sku_allowlist=(selected_product.sku,),
            )
        max_unit_price = interpreted.max_total_minor // interpreted.quantity
        matches = self.store.search_catalog(
            user_intent,
            currency=interpreted.currency,
            max_unit_price_minor=max_unit_price,
            merchant_ids=interpreted.merchant_allowlist,
            sku_ids=interpreted.sku_allowlist,
            recurring=None if interpreted.recurring_allowed else False,
            limit=10,
        )
        if not matches:
            raise BuyerError("no registered product satisfies the hard discovery filters")
        product = matches[0]
        entries = self.store.evidence_for_product(
            merchant_id=product.merchant_id, sku=product.sku
        )
        proposal = PurchaseProposal(
            merchant_id=product.merchant_id,
            sku=product.sku,
            quantity=interpreted.quantity,
            declared_total_minor=(
                product.effective_unit_price_minor * interpreted.quantity
            ),
            currency=product.currency,
            reason=(
                "Highest lexical catalog match within the interpreted hard filters."
            ),
            selected_evidence_ids=tuple(entry.evidence_id for entry in entries),
            user_intent_summary=user_intent.strip()[:1000],
        )
        output = BuyerOutput(
            proposal=proposal,
            interpreted_intent=interpreted,
            model_id=self.model_id,
        )
        require_selected_product(output, selected_product)
        return output


def require_selected_product(
    output: BuyerOutput, selected_product: SelectedProductIdentity | None
) -> None:
    """Enforce the clicked identity immediately before authorization handoff."""

    if selected_product is None:
        return
    proposal = output.proposal
    if (
        proposal.merchant_id != selected_product.merchant_id
        or proposal.sku != selected_product.sku
    ):
        raise BuyerError(
            "SELECTED_PRODUCT_IDENTITY_MISMATCH: buyer proposal did not match the "
            "server-resolved merchant and SKU"
        )
