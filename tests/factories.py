"""Deterministic test-object factories; no wall clock or randomness."""

from __future__ import annotations

from datetime import datetime, timezone

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
)
from mandateguard.models.transaction import Transaction, TransactionLine, TransactionPayload


ISSUED_AT = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
SERVER_TIME = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def make_mandate(
    *,
    max_total_minor: int = 500_000,
    max_quantity: int = 5,
    recurring_allowed: bool = False,
    merchant_allowlist: tuple[str, ...] | None = ("merchant-1",),
    sku_allowlist: tuple[str, ...] | None = ("sku-1",),
    expires_at: datetime = EXPIRES_AT,
) -> Mandate:
    return Mandate(
        payload=MandatePayload(
            mandate_id="12345678-1234-5678-1234-567812345678",
            nonce="nonce_1234567890abcdef",
            issued_at=ISSUED_AT,
            expires_at=expires_at,
            subject_ref="subject-1",
            currency="INR",
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=max_total_minor,
                    max_quantity=max_quantity,
                    recurring_allowed=recurring_allowed,
                    merchant_allowlist=merchant_allowlist,
                    sku_allowlist=sku_allowlist,
                )
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY",
            issuer_id="test-issuer",
        ),
    )


def make_line(
    *,
    sku: str = "sku-1",
    effective_unit_price_minor: int = 100_00,
    quantity: int = 1,
    line_total_minor: int | None = None,
    recurring: bool = False,
) -> TransactionLine:
    return TransactionLine(
        sku=sku,
        effective_unit_price_minor=effective_unit_price_minor,
        quantity=quantity,
        line_total_minor=(
            effective_unit_price_minor * quantity
            if line_total_minor is None
            else line_total_minor
        ),
        recurring=recurring,
    )


def make_payload(
    *,
    lines: tuple[TransactionLine, ...] | None = None,
    merchant_id: str = "merchant-1",
    cart_currency: str = "INR",
    order_currency: str = "INR",
    declared_order_total_minor: int | None = None,
    declared_aggregate_quantity: int | None = None,
    cart_recurring: bool | None = None,
    order_recurring: bool | None = None,
) -> TransactionPayload:
    actual_lines = lines if lines is not None else (make_line(),)
    derived_recurring = any(line.recurring for line in actual_lines)
    return TransactionPayload(
        transaction_id="transaction-1",
        merchant_id=merchant_id,
        cart_currency=cart_currency,
        order_currency=order_currency,
        declared_order_total_minor=(
            sum(line.line_total_minor for line in actual_lines)
            if declared_order_total_minor is None
            else declared_order_total_minor
        ),
        declared_aggregate_quantity=(
            sum(line.quantity for line in actual_lines)
            if declared_aggregate_quantity is None
            else declared_aggregate_quantity
        ),
        cart_recurring=derived_recurring if cart_recurring is None else cart_recurring,
        order_recurring=derived_recurring if order_recurring is None else order_recurring,
        lines=actual_lines,
    )


def make_transaction(
    *,
    payload: TransactionPayload | None = None,
    declared_transaction_hash: str | None = None,
) -> Transaction:
    actual_payload = payload if payload is not None else make_payload()
    return Transaction(
        payload=actual_payload,
        declared_transaction_hash=(
            transaction_body_sha256(actual_payload)
            if declared_transaction_hash is None
            else declared_transaction_hash
        ),
    )


def make_catalog(
    *,
    price_minor: int = 100_00,
    recurring: bool = False,
    merchant_id: str = "merchant-1",
    currency: str = "INR",
    items: tuple[CatalogItem, ...] | None = None,
) -> CatalogSnapshot:
    actual_items = items
    if actual_items is None:
        actual_items = (
            CatalogItem(
                sku="sku-1",
                merchant_id=merchant_id,
                effective_unit_price_minor=price_minor,
                recurring=recurring,
            ),
        )
    return CatalogSnapshot(
        snapshot_id="catalog-snapshot-1",
        merchant_id=merchant_id,
        currency=currency,
        items=actual_items,
    )


def make_commitments(
    transaction: Transaction, catalog: CatalogSnapshot
) -> CommittedHashes:
    return CommittedHashes(
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
    )
