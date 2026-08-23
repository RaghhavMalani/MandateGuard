"""Tier A independently verifiable deterministic checks A1-A8."""

from __future__ import annotations

from datetime import datetime, timezone

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.finding import (
    Finding,
    TIER_A_FAMILIES,
    TaxonomyFamily,
    TierACheckResult,
    TierACheckStatus,
)
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


def _pass(family: TaxonomyFamily) -> TierACheckResult:
    if family not in SUPPORTED_TIER_A_FAMILIES:
        raise ValueError("Tier A cannot emit a family outside A1-A8")
    return TierACheckResult(family=family, status=TierACheckStatus.PASS)


def _fail(
    family: TaxonomyFamily, message: str, **details: str | int | bool | None
) -> TierACheckResult:
    if family not in SUPPORTED_TIER_A_FAMILIES:
        raise ValueError("Tier A cannot emit a family outside A1-A8")
    return TierACheckResult(
        family=family,
        status=TierACheckStatus.FAIL,
        finding=Finding.create(family, message, details),
    )


def _not_evaluable(family: TaxonomyFamily, reason: str) -> TierACheckResult:
    if family not in SUPPORTED_TIER_A_FAMILIES:
        raise ValueError("Tier A cannot emit a family outside A1-A8")
    return TierACheckResult(
        family=family,
        status=TierACheckStatus.NOT_EVALUABLE,
        reason=reason,
    )


def _canonical_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _catalog_commit_matches(
    catalog_snapshot: CatalogSnapshot | None,
    committed_hashes: CommittedHashes | None,
) -> bool:
    return (
        catalog_snapshot is not None
        and committed_hashes is not None
        and committed_hashes.catalog_snapshot_sha256 is not None
        and catalog_snapshot_sha256(catalog_snapshot)
        == committed_hashes.catalog_snapshot_sha256
    )


def _transaction_commit_matches(
    transaction: Transaction,
    committed_hashes: CommittedHashes | None,
) -> bool:
    return (
        committed_hashes is not None
        and committed_hashes.transaction_sha256 is not None
        and transaction_body_sha256(transaction) == committed_hashes.transaction_sha256
    )


def evaluate_tier_a(
    *,
    mandate: Mandate,
    transaction: Transaction,
    catalog_snapshot: CatalogSnapshot | None,
    server_time: datetime | None,
    nonce_state: NonceLedgerState | None,
    committed_hashes: CommittedHashes | None,
) -> tuple[TierACheckResult, ...]:
    """Evaluate A1-A8 from explicit inputs, preserving unavailable-evidence state."""

    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be Mandate")
    if not isinstance(transaction, Transaction):
        raise TypeError("transaction must be Transaction")
    if catalog_snapshot is not None and not isinstance(catalog_snapshot, CatalogSnapshot):
        raise TypeError("catalog_snapshot must be CatalogSnapshot or None")
    if server_time is not None and (
        not isinstance(server_time, datetime)
        or server_time.tzinfo is None
        or server_time.utcoffset() is None
    ):
        raise ValueError("server_time must be a timezone-aware datetime or None")
    if nonce_state is not None and not isinstance(nonce_state, NonceLedgerState):
        raise TypeError("nonce_state must be NonceLedgerState or None")
    if committed_hashes is not None and not isinstance(committed_hashes, CommittedHashes):
        raise TypeError("committed_hashes must be CommittedHashes or None")

    payload = transaction.payload
    hard = mandate.payload.constraints.hard
    results: list[TierACheckResult] = []

    # A1: exact declared/effective unit-price equality at the committed catalog snapshot.
    if catalog_snapshot is None:
        results.append(_not_evaluable(TaxonomyFamily.A1, "merchant catalog snapshot unavailable"))
    elif not _catalog_commit_matches(catalog_snapshot, committed_hashes):
        results.append(
            _not_evaluable(
                TaxonomyFamily.A1,
                "committed merchant catalog snapshot unavailable or does not match",
            )
        )
    elif catalog_snapshot.currency != payload.order_currency:
        results.append(
            _not_evaluable(
                TaxonomyFamily.A1,
                "authoritative effective unit prices unavailable in the declared order currency",
            )
        )
    else:
        missing_prices: list[str] = []
        price_mismatches: list[str] = []
        for line in payload.lines:
            item = catalog_snapshot.item_by_sku(line.sku)
            if item is None:
                missing_prices.append(line.sku)
            elif line.effective_unit_price_minor != item.effective_unit_price_minor:
                price_mismatches.append(line.sku)
        if price_mismatches:
            results.append(
                _fail(
                    TaxonomyFamily.A1,
                    "declared unit price is not exactly equal to authoritative effective unit price",
                    mismatched_skus=",".join(sorted(price_mismatches)),
                )
            )
        elif missing_prices:
            results.append(
                _not_evaluable(
                    TaxonomyFamily.A1,
                    "authoritative effective unit price unavailable for selected SKU",
                )
            )
        else:
            results.append(_pass(TaxonomyFamily.A1))

    # A2: SKU existence and ownership.
    if catalog_snapshot is None:
        results.append(_not_evaluable(TaxonomyFamily.A2, "merchant catalog snapshot unavailable"))
    elif not _catalog_commit_matches(catalog_snapshot, committed_hashes):
        results.append(
            _not_evaluable(
                TaxonomyFamily.A2,
                "committed merchant catalog snapshot unavailable or does not match",
            )
        )
    else:
        missing_skus: list[str] = []
        ownership_mismatches: list[str] = []
        for line in payload.lines:
            item = catalog_snapshot.item_by_sku(line.sku)
            if item is None:
                missing_skus.append(line.sku)
            elif item.merchant_id != payload.merchant_id:
                ownership_mismatches.append(line.sku)
        if missing_skus or ownership_mismatches:
            results.append(
                _fail(
                    TaxonomyFamily.A2,
                    "SKU is absent from the catalog or not owned by the declared merchant",
                    missing_skus=",".join(sorted(missing_skus)),
                    ownership_mismatches=",".join(sorted(ownership_mismatches)),
                )
            )
        else:
            results.append(_pass(TaxonomyFamily.A2))

    # A3: declared merchant vs independent catalog mapping.
    if catalog_snapshot is None:
        results.append(_not_evaluable(TaxonomyFamily.A3, "merchant catalog mapping unavailable"))
    elif not _catalog_commit_matches(catalog_snapshot, committed_hashes):
        results.append(
            _not_evaluable(
                TaxonomyFamily.A3,
                "committed merchant catalog mapping unavailable or does not match",
            )
        )
    elif payload.merchant_id != catalog_snapshot.merchant_id:
        results.append(
            _fail(
                TaxonomyFamily.A3,
                "declared merchant does not match the catalog snapshot merchant",
                catalog_merchant=catalog_snapshot.merchant_id,
                declared_merchant=payload.merchant_id,
            )
        )
    else:
        results.append(_pass(TaxonomyFamily.A3))

    # A4: every V1 nonce is single-use.
    if nonce_state is None:
        results.append(_not_evaluable(TaxonomyFamily.A4, "PSP nonce ledger unavailable"))
    elif nonce_state.is_consumed(mandate.payload.nonce):
        results.append(
            _fail(
                TaxonomyFamily.A4,
                "mandate nonce has already been consumed",
                nonce=mandate.payload.nonce,
            )
        )
    else:
        results.append(_pass(TaxonomyFamily.A4))

    # A5: validity is checked only against injected PSP/server time.
    if server_time is None:
        results.append(_not_evaluable(TaxonomyFamily.A5, "PSP server time unavailable"))
    elif server_time >= mandate.payload.expires_at:
        results.append(
            _fail(
                TaxonomyFamily.A5,
                "mandate has expired",
                expires_at=_canonical_timestamp(mandate.payload.expires_at),
                server_time=_canonical_timestamp(server_time),
            )
        )
    else:
        results.append(_pass(TaxonomyFamily.A5))

    # A6: current snapshots must match all available PSP-side commitments.
    actual_transaction_hash = transaction_body_sha256(transaction)
    mutated_snapshots: list[str] = []
    unavailable_commitments: list[str] = []
    if committed_hashes is None or committed_hashes.transaction_sha256 is None:
        unavailable_commitments.append("transaction")
    elif actual_transaction_hash != committed_hashes.transaction_sha256:
        mutated_snapshots.append("transaction")
    if catalog_snapshot is None:
        unavailable_commitments.append("catalog_snapshot")
    elif committed_hashes is None or committed_hashes.catalog_snapshot_sha256 is None:
        unavailable_commitments.append("catalog_commitment")
    elif catalog_snapshot_sha256(catalog_snapshot) != committed_hashes.catalog_snapshot_sha256:
        mutated_snapshots.append("catalog")
    if mutated_snapshots:
        results.append(
            _fail(
                TaxonomyFamily.A6,
                "snapshot content does not match the PSP-side commitment",
                mutated_snapshots=",".join(mutated_snapshots),
            )
        )
    elif unavailable_commitments:
        results.append(
            _not_evaluable(
                TaxonomyFamily.A6,
                "required PSP snapshot or commitment unavailable: "
                + ",".join(unavailable_commitments),
            )
        )
    else:
        results.append(_pass(TaxonomyFamily.A6))

    # A7: catalog price is independent; execution quantity is agent-supplied and hash-bound.
    if catalog_snapshot is None:
        results.append(_not_evaluable(TaxonomyFamily.A7, "merchant catalog snapshot unavailable"))
    elif not _catalog_commit_matches(catalog_snapshot, committed_hashes):
        results.append(
            _not_evaluable(
                TaxonomyFamily.A7,
                "committed merchant catalog snapshot unavailable or does not match",
            )
        )
    elif not _transaction_commit_matches(transaction, committed_hashes):
        results.append(
            _not_evaluable(
                TaxonomyFamily.A7,
                "committed execution quantities unavailable or do not match",
            )
        )
    elif catalog_snapshot.currency != mandate.payload.currency:
        results.append(
            _not_evaluable(
                TaxonomyFamily.A7,
                "catalog-derived total unavailable in the mandate currency",
            )
        )
    else:
        missing_prices = [
            line.sku for line in payload.lines if catalog_snapshot.item_by_sku(line.sku) is None
        ]
        if missing_prices:
            results.append(
                _not_evaluable(
                    TaxonomyFamily.A7,
                    "authoritative effective unit price unavailable for selected SKU",
                )
            )
        else:
            catalog_total_minor = sum(
                catalog_snapshot.item_by_sku(line.sku).effective_unit_price_minor
                * line.quantity
                for line in payload.lines
            )
            if catalog_total_minor > hard.max_total_minor:
                results.append(
                    _fail(
                        TaxonomyFamily.A7,
                        "catalog-derived total exceeds the mandate ceiling",
                        catalog_total_minor=catalog_total_minor,
                        mandate_max_total_minor=hard.max_total_minor,
                    )
                )
            else:
                results.append(_pass(TaxonomyFamily.A7))

    # A8: catalog recurrence state vs mandate permission.
    if catalog_snapshot is None:
        results.append(_not_evaluable(TaxonomyFamily.A8, "merchant catalog snapshot unavailable"))
    elif not _catalog_commit_matches(catalog_snapshot, committed_hashes):
        results.append(
            _not_evaluable(
                TaxonomyFamily.A8,
                "committed merchant catalog snapshot unavailable or does not match",
            )
        )
    else:
        missing_recurrence_skus = sorted(
            {line.sku for line in payload.lines if catalog_snapshot.item_by_sku(line.sku) is None}
        )
        recurring_skus = sorted(
            {
                line.sku
                for line in payload.lines
                if (item := catalog_snapshot.item_by_sku(line.sku)) is not None and item.recurring
            }
        )
        if recurring_skus and not hard.recurring_allowed:
            results.append(
                _fail(
                    TaxonomyFamily.A8,
                    "catalog marks a selected SKU as recurring but the mandate forbids recurrence",
                    recurring_skus=",".join(recurring_skus),
                )
            )
        elif missing_recurrence_skus:
            results.append(
                _not_evaluable(
                    TaxonomyFamily.A8,
                    "catalog recurrence state unavailable for selected SKU",
                )
            )
        else:
            results.append(_pass(TaxonomyFamily.A8))

    return tuple(results)
