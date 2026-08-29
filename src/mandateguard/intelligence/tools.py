"""Narrow commerce-only tools exposed to the AI buyer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from mandateguard.intelligence.models import (
    InterpretedPurchaseIntent,
    PurchaseProposal,
)
from mandateguard.intelligence.store import TrustedCommerceStore


ALLOWED_BUYER_TOOLS = frozenset(
    {
        "search_catalog",
        "get_product",
        "get_merchant_evidence",
        "propose_purchase",
    }
)


class CommerceToolError(RuntimeError):
    """A buyer attempted an undeclared or malformed commerce tool call."""


def _exact(value: object, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise CommerceToolError(f"{name} arguments do not match the strict schema")
    return value


def _optional_tuple(value: object, name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CommerceToolError(f"{name} must be an array of strings or null")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class BuyerDraft:
    proposal: PurchaseProposal
    interpreted_intent: InterpretedPurchaseIntent


@dataclass(frozen=True, slots=True)
class CommerceTools:
    store: TrustedCommerceStore

    def __post_init__(self) -> None:
        if not isinstance(self.store, TrustedCommerceStore):
            raise TypeError("store must be TrustedCommerceStore")

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]:
        nullable_string = {"type": ["string", "null"]}
        nullable_string_array = {
            "type": ["array", "null"],
            "items": {"type": "string"},
        }
        return (
            {
                "type": "function",
                "name": "search_catalog",
                "description": "Search only the registered synthetic commerce catalog.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query", "filters"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "filters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "currency",
                                "max_unit_price_minor",
                                "merchant_ids",
                                "sku_ids",
                                "recurring",
                                "limit",
                            ],
                            "properties": {
                                "currency": nullable_string,
                                "max_unit_price_minor": {
                                    "type": ["integer", "null"],
                                    "minimum": 0,
                                },
                                "merchant_ids": nullable_string_array,
                                "sku_ids": nullable_string_array,
                                "recurring": {"type": ["boolean", "null"]},
                                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "name": "get_product",
                "description": "Read one registered catalog product by merchant and SKU.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["merchant_id", "sku"],
                    "properties": {
                        "merchant_id": {"type": "string"},
                        "sku": {"type": "string"},
                    },
                },
            },
            {
                "type": "function",
                "name": "get_merchant_evidence",
                "description": (
                    "Inspect registered merchant/product evidence. Evidence IDs may be "
                    "requested in a proposal, but MandateGuard independently resolves them."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["merchant_id", "sku"],
                    "properties": {
                        "merchant_id": {"type": "string"},
                        "sku": {"type": "string"},
                    },
                },
            },
            {
                "type": "function",
                "name": "propose_purchase",
                "description": (
                    "Return a typed purchase proposal and interpreted user constraints. "
                    "This tool proposes only; it cannot authorize or execute payment."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["interpreted_intent", "proposal"],
                    "properties": {
                        "interpreted_intent": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "max_total_minor",
                                "quantity",
                                "currency",
                                "purpose",
                                "recurring_allowed",
                                "exclusions",
                                "merchant_allowlist",
                                "sku_allowlist",
                            ],
                            "properties": {
                                "max_total_minor": {"type": "integer", "minimum": 0},
                                "quantity": {"type": "integer", "minimum": 1},
                                "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                                "purpose": nullable_string,
                                "recurring_allowed": {"type": "boolean"},
                                "exclusions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 16,
                                },
                                "merchant_allowlist": nullable_string_array,
                                "sku_allowlist": nullable_string_array,
                            },
                        },
                        "proposal": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "merchant_id",
                                "sku",
                                "quantity",
                                "declared_total_minor",
                                "currency",
                                "reason",
                                "selected_evidence_ids",
                                "user_intent_summary",
                            ],
                            "properties": {
                                "merchant_id": {"type": "string"},
                                "sku": {"type": "string"},
                                "quantity": {"type": "integer", "minimum": 1},
                                "declared_total_minor": {
                                    "type": "integer",
                                    "minimum": 0,
                                },
                                "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                                "selected_evidence_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 32,
                                },
                                "user_intent_summary": nullable_string,
                            },
                        },
                    },
                },
            },
        )

    def dispatch(
        self, name: str, arguments: object
    ) -> Mapping[str, Any] | BuyerDraft:
        if name not in ALLOWED_BUYER_TOOLS:
            raise CommerceToolError("buyer requested a tool outside the commerce boundary")
        if name == "search_catalog":
            return self._search_catalog(arguments)
        if name == "get_product":
            return self._get_product(arguments)
        if name == "get_merchant_evidence":
            return self._get_merchant_evidence(arguments)
        return self._propose_purchase(arguments)

    def _search_catalog(self, arguments: object) -> Mapping[str, Any]:
        data = _exact(arguments, frozenset({"query", "filters"}), "search_catalog")
        filters = _exact(
            data["filters"],
            frozenset(
                {
                    "currency",
                    "max_unit_price_minor",
                    "merchant_ids",
                    "sku_ids",
                    "recurring",
                    "limit",
                }
            ),
            "search_catalog.filters",
        )
        products = self.store.search_catalog(
            data["query"],
            currency=filters["currency"],
            max_unit_price_minor=filters["max_unit_price_minor"],
            merchant_ids=_optional_tuple(filters["merchant_ids"], "merchant_ids"),
            sku_ids=_optional_tuple(filters["sku_ids"], "sku_ids"),
            recurring=filters["recurring"],
            limit=filters["limit"],
        )
        return {"products": [item.discovery_mapping() for item in products]}

    def _get_product(self, arguments: object) -> Mapping[str, Any]:
        data = _exact(arguments, frozenset({"merchant_id", "sku"}), "get_product")
        product = self.store.get_product(
            merchant_id=data["merchant_id"], sku=data["sku"]
        )
        return {"product": product.discovery_mapping()}

    def _get_merchant_evidence(self, arguments: object) -> Mapping[str, Any]:
        data = _exact(
            arguments,
            frozenset({"merchant_id", "sku"}),
            "get_merchant_evidence",
        )
        entries = self.store.evidence_for_product(
            merchant_id=data["merchant_id"], sku=data["sku"]
        )
        return {
            "evidence": [
                {
                    "evidence_id": entry.evidence_id,
                    "merchant_id": entry.merchant_id,
                    "sku": entry.sku,
                    "source_kind": entry.source_kind,
                    "text": entry.text,
                }
                for entry in entries
            ]
        }

    def _propose_purchase(self, arguments: object) -> BuyerDraft:
        data = _exact(
            arguments,
            frozenset({"interpreted_intent", "proposal"}),
            "propose_purchase",
        )
        try:
            interpreted = InterpretedPurchaseIntent.from_mapping(
                data["interpreted_intent"]
            )
            proposal = PurchaseProposal.from_mapping(data["proposal"])
        except (TypeError, ValueError) as exc:
            raise CommerceToolError(
                "propose_purchase arguments do not match the strict schema"
            ) from exc
        self.store.get_product(
            merchant_id=proposal.merchant_id, sku=proposal.sku
        )
        self.store.resolve_evidence_ids(
            proposal.selected_evidence_ids,
            merchant_id=proposal.merchant_id,
            sku=proposal.sku,
        )
        return BuyerDraft(proposal=proposal, interpreted_intent=interpreted)
