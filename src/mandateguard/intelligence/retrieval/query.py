"""Deterministic audit query and mandate-document construction."""

from __future__ import annotations

from hashlib import sha256

from mandateguard.core.canonical import canonical_json_text
from mandateguard.intelligence.models import (
    CommerceProduct,
    PurchaseProposal,
    RetrievalDocument,
    RetrievalSource,
)
from mandateguard.models.mandate import Mandate


def build_retrieval_query(
    *,
    user_intent: str,
    mandate: Mandate,
    proposal: PurchaseProposal,
    product: CommerceProduct,
) -> tuple[str, str]:
    """Bind intent, mandate, candidate, and trusted product context."""

    if not isinstance(user_intent, str) or not user_intent.strip():
        raise ValueError("user_intent must be non-empty")
    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(proposal, PurchaseProposal):
        raise TypeError("proposal must be PurchaseProposal")
    if not isinstance(product, CommerceProduct):
        raise TypeError("product must be CommerceProduct")
    hard = mandate.payload.constraints.hard
    value = {
        "user_intent": user_intent.strip(),
        "mandate": {
            "currency": mandate.payload.currency,
            "hard": {
                "max_total_minor": hard.max_total_minor,
                "max_quantity": hard.max_quantity,
                "recurring_allowed": hard.recurring_allowed,
                "merchant_allowlist": hard.merchant_allowlist,
                "sku_allowlist": hard.sku_allowlist,
            },
            "semantic": [
                {
                    "constraint_id": item.constraint_id,
                    "kind": item.kind,
                    "text": item.text,
                }
                for item in mandate.payload.constraints.semantic
            ],
        },
        "candidate": {
            "merchant_id": proposal.merchant_id,
            "sku": proposal.sku,
            "quantity": proposal.quantity,
            "declared_total_minor": proposal.declared_total_minor,
            "currency": proposal.currency,
            "requested_evidence_ids": proposal.selected_evidence_ids,
        },
        "trusted_product_context": product.discovery_mapping(),
    }
    query = canonical_json_text(value)
    return query, sha256(query.encode("utf-8")).hexdigest()


def mandate_documents(mandate: Mandate) -> tuple[RetrievalDocument, ...]:
    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    hard = mandate.payload.constraints.hard
    documents = [
        RetrievalDocument(
            document_id="mandate.hard",
            source_type=RetrievalSource.MANDATE_CLAUSE,
            text=(
                f"currency {mandate.payload.currency}; maximum total minor "
                f"{hard.max_total_minor}; maximum quantity {hard.max_quantity}; "
                f"recurring allowed {hard.recurring_allowed}; merchant allowlist "
                f"{hard.merchant_allowlist}; SKU allowlist {hard.sku_allowlist}"
            ),
        )
    ]
    documents.extend(
        RetrievalDocument(
            document_id=f"mandate.{constraint.constraint_id}",
            source_type=RetrievalSource.MANDATE_CLAUSE,
            text=f"{constraint.kind}: {constraint.text}",
        )
        for constraint in mandate.payload.constraints.semantic
    )
    return tuple(documents)
