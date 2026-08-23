"""Tier B self-reported consistency and mandate-conformance checks B1-B10."""

from __future__ import annotations

from mandateguard.core.hashing import transaction_body_sha256
from mandateguard.models.finding import Finding, TIER_B_FAMILIES, TaxonomyFamily
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction


SUPPORTED_TIER_B_FAMILIES = frozenset(
    {
        TaxonomyFamily.B1,
        TaxonomyFamily.B2,
        TaxonomyFamily.B3,
        TaxonomyFamily.B4,
        TaxonomyFamily.B5,
        TaxonomyFamily.B6,
        TaxonomyFamily.B7,
        TaxonomyFamily.B8,
        TaxonomyFamily.B9,
        TaxonomyFamily.B10,
    }
)
assert SUPPORTED_TIER_B_FAMILIES == TIER_B_FAMILIES


def _finding(
    family: TaxonomyFamily, message: str, **details: str | int | bool | None
) -> Finding:
    if family not in SUPPORTED_TIER_B_FAMILIES:
        raise ValueError("Tier B cannot emit a family outside B1-B10")
    return Finding.create(family, message, details)


def evaluate_tier_b(*, mandate: Mandate, transaction: Transaction) -> tuple[Finding, ...]:
    """Evaluate B1-B10 using only declared mandate and transaction state."""

    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")

    payload = transaction.payload
    hard = mandate.payload.constraints.hard
    findings: list[Finding] = []

    # B1: sum of declared line totals vs the declared order total.
    line_sum_minor = sum(line.line_total_minor for line in payload.lines)
    if line_sum_minor != payload.declared_order_total_minor:
        findings.append(
            _finding(
                TaxonomyFamily.B1,
                "declared line totals do not reconcile with the declared order total",
                declared_order_total_minor=payload.declared_order_total_minor,
                line_sum_minor=line_sum_minor,
            )
        )

    # B2: aggregate quantity vs line-item quantities.
    line_quantity = sum(line.quantity for line in payload.lines)
    if line_quantity != payload.declared_aggregate_quantity:
        findings.append(
            _finding(
                TaxonomyFamily.B2,
                "declared aggregate quantity does not equal line-item quantities",
                declared_aggregate_quantity=payload.declared_aggregate_quantity,
                line_quantity=line_quantity,
            )
        )

    # B3: mandate, cart, and order currencies.
    currencies = {
        mandate.payload.currency,
        payload.cart_currency,
        payload.order_currency,
    }
    if len(currencies) != 1:
        findings.append(
            _finding(
                TaxonomyFamily.B3,
                "mandate, cart, and order currencies are inconsistent",
                cart_currency=payload.cart_currency,
                mandate_currency=mandate.payload.currency,
                order_currency=payload.order_currency,
            )
        )

    # B4: self-reported line, cart, and order recurrence fields.
    line_recurring = any(line.recurring for line in payload.lines)
    if not (
        payload.cart_recurring == payload.order_recurring == line_recurring
    ):
        findings.append(
            _finding(
                TaxonomyFamily.B4,
                "self-reported recurrence fields are inconsistent",
                cart_recurring=payload.cart_recurring,
                line_recurring=line_recurring,
                order_recurring=payload.order_recurring,
            )
        )

    # B5: transaction payload vs its agent-declared commitment.
    actual_transaction_hash = transaction_body_sha256(transaction)
    if actual_transaction_hash != transaction.declared_transaction_hash:
        findings.append(
            _finding(
                TaxonomyFamily.B5,
                "canonical transaction hash does not match its declared commitment",
                actual_sha256=actual_transaction_hash,
                declared_sha256=transaction.declared_transaction_hash,
            )
        )

    # B6-B10: conformance of declared fields to the mandate.
    if payload.declared_order_total_minor > hard.max_total_minor:
        findings.append(
            _finding(
                TaxonomyFamily.B6,
                "declared order total exceeds the mandate ceiling",
                declared_order_total_minor=payload.declared_order_total_minor,
                mandate_max_total_minor=hard.max_total_minor,
            )
        )

    if payload.declared_aggregate_quantity > hard.max_quantity:
        findings.append(
            _finding(
                TaxonomyFamily.B7,
                "declared aggregate quantity exceeds the mandate ceiling",
                declared_aggregate_quantity=payload.declared_aggregate_quantity,
                mandate_max_quantity=hard.max_quantity,
            )
        )

    any_declared_recurring = (
        payload.cart_recurring
        or payload.order_recurring
        or any(line.recurring for line in payload.lines)
    )
    if any_declared_recurring and not hard.recurring_allowed:
        findings.append(
            _finding(
                TaxonomyFamily.B8,
                "declared recurrence is forbidden by the mandate",
                recurring_allowed=hard.recurring_allowed,
            )
        )

    if (
        hard.merchant_allowlist is not None
        and payload.merchant_id not in hard.merchant_allowlist
    ):
        findings.append(
            _finding(
                TaxonomyFamily.B9,
                "declared merchant is not in the mandate allowlist",
                declared_merchant=payload.merchant_id,
            )
        )

    if hard.sku_allowlist is not None:
        disallowed_skus = sorted(
            {line.sku for line in payload.lines if line.sku not in hard.sku_allowlist}
        )
        if disallowed_skus:
            findings.append(
                _finding(
                    TaxonomyFamily.B10,
                    "declared SKU is not in the mandate allowlist",
                    disallowed_skus=",".join(disallowed_skus),
                )
            )

    return tuple(findings)
