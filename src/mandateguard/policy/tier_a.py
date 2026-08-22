"""Tier A independently verifiable deterministic checks A1-A8."""

from __future__ import annotations

from datetime import datetime, timezone

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_payload_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.finding import Finding, TIER_A_FAMILIES, TaxonomyFamily
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction


SUPPORTED_TIER_A_FAMILIES = frozenset(
    {
        TaxonomyFamily.A1,
        TaxonomyFamily.A2,
        TaxonomyFamily.A3,
        TaxonomyFamily.A4,
        TaxonomyFamily.A5,
        TaxonomyFamily.A6,
        TaxonomyFamily.A7,
        TaxonomyFamily.A8,
    }
)
assert SUPPORTED_TIER_A_FAMILIES == TIER_A_FAMILIES


def _finding(
    family: TaxonomyFamily, message: str, **details: str | int | bool | None
) -> Finding:
    if family not in SUPPORTED_TIER_A_FAMILIES:
        raise ValueError("Tier A cannot emit a family outside A1-A8")
    return Finding.create(family, message, details)


def _canonical_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def evaluate_tier_a(
    *,
    mandate: Mandate,
    transaction: Transaction,
    catalog_snapshot: CatalogSnapshot,
    server_time: datetime,
    nonce_state: NonceLedgerState,
    committed_hashes: CommittedHashes,
) -> tuple[Finding, ...]:
    """Evaluate A1-A8 from explicitly supplied immutable inputs."""

    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")
    if not isinstance(catalog_snapshot, CatalogSnapshot):
        raise TypeError("catalog_snapshot must be CatalogSnapshot")
    if (
        not isinstance(server_time, datetime)
        or server_time.tzinfo is None
        or server_time.utcoffset() is None
    ):
        raise ValueError("server_time must be a timezone-aware datetime")
    if not isinstance(nonce_state, NonceLedgerState):
        raise TypeError("nonce_state must be NonceLedgerState")
    if not isinstance(committed_hashes, CommittedHashes):
        raise TypeError("committed_hashes must be CommittedHashes")

    payload = transaction.payload
    hard = mandate.payload.constraints.hard
    findings: list[Finding] = []

    # A1: declared line price vs independently retrieved catalog price.
    price_mismatches: list[str] = []
    if catalog_snapshot.currency != payload.order_currency:
        price_mismatches.append("catalog_currency")
    for line in payload.lines:
        item = catalog_snapshot.item_by_sku(line.sku)
        if item is not None and line.unit_price_minor != item.price_minor:
            price_mismatches.append(line.sku)
    if price_mismatches:
        findings.append(
            _finding(
                TaxonomyFamily.A1,
                "declared line price diverges from catalog state",
                mismatches=",".join(sorted(price_mismatches)),
            )
        )

    # A2: SKU existence and ownership.
    missing_skus: list[str] = []
    ownership_mismatches: list[str] = []
    for line in payload.lines:
        item = catalog_snapshot.item_by_sku(line.sku)
        if item is None:
            missing_skus.append(line.sku)
        elif item.merchant_id != payload.merchant_id:
            ownership_mismatches.append(line.sku)
    if missing_skus or ownership_mismatches:
        findings.append(
            _finding(
                TaxonomyFamily.A2,
                "SKU is absent from the catalog or not owned by the declared merchant",
                missing_skus=",".join(sorted(missing_skus)),
                ownership_mismatches=",".join(sorted(ownership_mismatches)),
            )
        )

    # A3: declared merchant vs independent catalog mapping.
    if payload.merchant_id != catalog_snapshot.merchant_id:
        findings.append(
            _finding(
                TaxonomyFamily.A3,
                "declared merchant does not match the catalog snapshot merchant",
                catalog_merchant=catalog_snapshot.merchant_id,
                declared_merchant=payload.merchant_id,
            )
        )

    # A4: every V1 nonce is single-use.
    if nonce_state.is_consumed(mandate.payload.nonce):
        findings.append(
            _finding(
                TaxonomyFamily.A4,
                "mandate nonce has already been consumed",
                nonce=mandate.payload.nonce,
            )
        )

    # A5: validity is checked only against injected PSP/server time.
    if server_time >= mandate.payload.expires_at:
        findings.append(
            _finding(
                TaxonomyFamily.A5,
                "mandate has expired",
                expires_at=_canonical_timestamp(mandate.payload.expires_at),
                server_time=_canonical_timestamp(server_time),
            )
        )

    # A6: current snapshots must match both PSP-side commitments.
    actual_transaction_hash = transaction_payload_sha256(transaction)
    actual_catalog_hash = catalog_snapshot_sha256(catalog_snapshot)
    mutated_snapshots: list[str] = []
    if actual_transaction_hash != committed_hashes.transaction_sha256:
        mutated_snapshots.append("transaction")
    if actual_catalog_hash != committed_hashes.catalog_snapshot_sha256:
        mutated_snapshots.append("catalog")
    if mutated_snapshots:
        findings.append(
            _finding(
                TaxonomyFamily.A6,
                "snapshot content does not match the PSP-side commitment",
                mutated_snapshots=",".join(mutated_snapshots),
            )
        )

    # A7: catalog price is independent; execution quantity is agent-supplied and hash-bound.
    unavailable_skus = [
        line.sku for line in payload.lines if catalog_snapshot.item_by_sku(line.sku) is None
    ]
    if catalog_snapshot.currency != mandate.payload.currency:
        findings.append(
            _finding(
                TaxonomyFamily.A7,
                "catalog-derived total cannot be compared across currencies",
                catalog_currency=catalog_snapshot.currency,
                mandate_currency=mandate.payload.currency,
            )
        )
    elif unavailable_skus:
        findings.append(
            _finding(
                TaxonomyFamily.A7,
                "catalog-derived total cannot be established for missing SKUs",
                missing_skus=",".join(sorted(unavailable_skus)),
            )
        )
    else:
        catalog_total_minor = sum(
            catalog_snapshot.item_by_sku(line.sku).price_minor * line.quantity
            for line in payload.lines
        )
        if catalog_total_minor > hard.max_total_minor:
            findings.append(
                _finding(
                    TaxonomyFamily.A7,
                    "catalog-derived total exceeds the mandate ceiling",
                    catalog_total_minor=catalog_total_minor,
                    mandate_max_total_minor=hard.max_total_minor,
                )
            )

    # A8: catalog recurrence state vs mandate permission.
    recurring_skus = sorted(
        {
            line.sku
            for line in payload.lines
            if (item := catalog_snapshot.item_by_sku(line.sku)) is not None and item.recurring
        }
    )
    if recurring_skus and not hard.recurring_allowed:
        findings.append(
            _finding(
                TaxonomyFamily.A8,
                "catalog marks a selected SKU as recurring but the mandate forbids recurrence",
                recurring_skus=",".join(recurring_skus),
            )
        )

    return tuple(findings)
