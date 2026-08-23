"""Registered deterministic mutation recipes for the Tier A/B benchmark corpus.

Every recipe is a pure function of ``(family_id, case_class, index)``. There is
no ambient randomness, no wall clock, no filesystem order, and no use of the
process-randomized ``hash()`` builtin: all derived identifiers come from
SHA-256 over an explicit recipe key.

The label of a case is fixed by the recipe that built it, never by a detector
result. ``V`` recipes mutate the target invariant, so the ground truth is
``violation``; ``P`` recipes leave every Tier A/B invariant satisfied, so the
ground truth is ``benign``; ``NE`` recipes withhold the evidence the target
Tier A check requires without introducing any known failure, so the ground
truth is ``benign`` with a ``REVIEW`` action.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable

from mandateguard.benchmark.models import (
    GENERATOR_VERSION,
    EvaluationInputs,
    TIER_A_FAMILIES,
)
from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    sha256_canonical,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
)
from mandateguard.models.transaction import Transaction, TransactionLine, TransactionPayload


EPOCH = datetime(2026, 1, 5, tzinfo=timezone.utc)
CURRENCIES = ("INR", "USD", "EUR")

COMMITMENT_ACTUAL = "actual"
COMMITMENT_ABSENT = "absent"
COMMITMENT_MUTATED = "mutated"

NONCE_FRESH = "fresh"
NONCE_CONSUMED = "consumed"
NONCE_ABSENT = "absent"


class RecipeError(ValueError):
    """Raised when a registered recipe cannot build a well-formed case."""


@dataclass(frozen=True, slots=True)
class LineSpec:
    sku: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int | None = None
    recurring: bool = False

    def resolved_line_total(self) -> int:
        if self.line_total_minor is not None:
            return self.line_total_minor
        return self.unit_price_minor * self.quantity


@dataclass(frozen=True, slots=True)
class ItemSpec:
    sku: str
    merchant_id: str
    unit_price_minor: int
    recurring: bool = False


@dataclass(frozen=True, slots=True)
class Scenario:
    """Every knob a registered recipe may turn, resolved to typed inputs later."""

    variation_index: int
    mandate_id: str
    nonce: str
    subject_ref: str
    issuer_id: str
    issued_at: datetime
    expires_at: datetime
    evaluated_at: datetime
    server_time_available: bool
    replay_seed: int
    transaction_id: str
    snapshot_id: str
    merchant_id: str
    snapshot_merchant_id: str
    mandate_currency: str
    cart_currency: str
    order_currency: str
    catalog_currency: str
    lines: tuple[LineSpec, ...]
    items: tuple[ItemSpec, ...]
    catalog_present: bool
    declared_order_total_minor: int | None
    declared_aggregate_quantity: int | None
    cart_recurring: bool | None
    order_recurring: bool | None
    declared_transaction_hash_mutated: bool
    max_total_minor: int | None
    max_quantity: int | None
    recurring_allowed: bool
    merchant_allowlist: tuple[str, ...] | None
    sku_allowlist: tuple[str, ...] | None
    commitments_present: bool
    transaction_commitment: str
    catalog_commitment: str
    nonce_mode: str
    consumed_nonces: tuple[str, ...]
    commitment_variant_delta: int


def recipe_key(family_id: str, case_class: str, index: int) -> str:
    return f"{GENERATOR_VERSION}|{family_id}|{case_class}|{index:03d}"


def _digest(family_id: str, case_class: str, index: int) -> bytes:
    return sha256(recipe_key(family_id, case_class, index).encode("utf-8")).digest()


def generator_seed(family_id: str, case_class: str, index: int) -> int:
    """Explicit recorded integer seed; never system entropy, never ``hash()``."""

    return int.from_bytes(_digest(family_id, case_class, index)[:8], "big")


def _mandate_id(digest: bytes) -> str:
    hex32 = digest[8:24].hex()
    return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"


def _slug(family_id: str, case_class: str, index: int) -> str:
    return f"{family_id.lower()}-{case_class.lower()}-{index:03d}"


def _currency(index: int) -> str:
    return CURRENCIES[index % len(CURRENCIES)]


def _other_currency(base: str, index: int) -> str:
    alternatives = tuple(code for code in CURRENCIES if code != base)
    return alternatives[index % len(alternatives)]


def resolved_declared_total(scenario: Scenario) -> int:
    if scenario.declared_order_total_minor is not None:
        return scenario.declared_order_total_minor
    return sum(line.resolved_line_total() for line in scenario.lines)


def resolved_aggregate_quantity(scenario: Scenario) -> int:
    if scenario.declared_aggregate_quantity is not None:
        return scenario.declared_aggregate_quantity
    return sum(line.quantity for line in scenario.lines)


def catalog_derived_total(scenario: Scenario) -> int:
    prices = {item.sku: item.unit_price_minor for item in scenario.items}
    return sum(prices[line.sku] * line.quantity for line in scenario.lines)


def default_scenario(family_id: str, case_class: str, index: int) -> Scenario:
    """A fully evidenced scenario in which every Tier A/B invariant holds.

    Prices, quantities, line counts, merchants, SKUs, ceilings, timestamps, and
    nonces all vary with the recipe index, so no two cases are byte-identical
    apart from their identifier.
    """

    digest = _digest(family_id, case_class, index)
    slug = _slug(family_id, case_class, index)
    line_count = 1 + (index % 3)
    merchant_id = f"merchant-{family_id.lower()}-{(index % 6) + 1}"
    currency = _currency(index)
    lines = tuple(
        LineSpec(
            sku=f"sku-{slug}-{position}",
            unit_price_minor=9_900 + ((index * 7 + position * 311) % 40) * 125,
            quantity=1 + ((index + position) % 3),
        )
        for position in range(line_count)
    )
    items = tuple(
        ItemSpec(
            sku=line.sku,
            merchant_id=merchant_id,
            unit_price_minor=line.unit_price_minor,
            recurring=False,
        )
        for line in lines
    )
    if index % 5 == 3:
        items = items + tuple(
            ItemSpec(
                sku=f"sku-{slug}-unused-{extra}",
                merchant_id=merchant_id,
                unit_price_minor=4_500 + extra * 725 + (index % 11) * 60,
                recurring=False,
            )
            for extra in range(2)
        )
    issued_at = EPOCH + timedelta(days=index, hours=index % 7)
    expires_at = issued_at + timedelta(days=7 + (index % 5))
    server_time = issued_at + timedelta(
        days=1 + (index % 3), hours=3, minutes=(index * 7) % 60
    )
    merchant_allowlist: tuple[str, ...] | None = None
    if index % 3 != 2:
        merchant_allowlist = tuple(
            sorted({merchant_id, f"merchant-allow-{family_id.lower()}-{index % 4}"})
        )
    sku_allowlist: tuple[str, ...] | None = None
    if index % 4 != 3:
        sku_allowlist = tuple(
            sorted({line.sku for line in lines} | {f"sku-allow-{slug}"})
        )
    return Scenario(
        variation_index=index,
        mandate_id=_mandate_id(digest),
        nonce=f"nonce-{slug}-{digest[:4].hex()}",
        subject_ref=f"subject-{slug}",
        issuer_id=f"issuer-{family_id.lower()}-{index % 4}",
        issued_at=issued_at,
        expires_at=expires_at,
        evaluated_at=server_time,
        server_time_available=True,
        replay_seed=int.from_bytes(digest[24:28], "big"),
        transaction_id=f"txn-{slug}",
        snapshot_id=f"catalog-{slug}",
        merchant_id=merchant_id,
        snapshot_merchant_id=merchant_id,
        mandate_currency=currency,
        cart_currency=currency,
        order_currency=currency,
        catalog_currency=currency,
        lines=lines,
        items=items,
        catalog_present=True,
        declared_order_total_minor=None,
        declared_aggregate_quantity=None,
        cart_recurring=None,
        order_recurring=None,
        declared_transaction_hash_mutated=False,
        max_total_minor=None,
        max_quantity=None,
        recurring_allowed=False,
        merchant_allowlist=merchant_allowlist,
        sku_allowlist=sku_allowlist,
        commitments_present=True,
        transaction_commitment=COMMITMENT_ACTUAL,
        catalog_commitment=COMMITMENT_ACTUAL,
        nonce_mode=NONCE_FRESH,
        consumed_nonces=tuple(
            f"nonce-other-{slug}-{position}" for position in range(1 + index % 3)
        ),
        commitment_variant_delta=1 + (index % 7),
    )


def _build_transaction_payload(scenario: Scenario) -> TransactionPayload:
    lines = tuple(
        TransactionLine(
            sku=line.sku,
            effective_unit_price_minor=line.unit_price_minor,
            quantity=line.quantity,
            line_total_minor=line.resolved_line_total(),
            recurring=line.recurring,
        )
        for line in scenario.lines
    )
    line_recurring = any(line.recurring for line in lines)
    return TransactionPayload(
        transaction_id=scenario.transaction_id,
        merchant_id=scenario.merchant_id,
        cart_currency=scenario.cart_currency,
        order_currency=scenario.order_currency,
        declared_order_total_minor=resolved_declared_total(scenario),
        declared_aggregate_quantity=resolved_aggregate_quantity(scenario),
        cart_recurring=(
            line_recurring if scenario.cart_recurring is None else scenario.cart_recurring
        ),
        order_recurring=(
            line_recurring
            if scenario.order_recurring is None
            else scenario.order_recurring
        ),
        lines=lines,
    )


def _build_catalog(scenario: Scenario) -> CatalogSnapshot | None:
    if not scenario.catalog_present:
        return None
    return CatalogSnapshot(
        snapshot_id=scenario.snapshot_id,
        merchant_id=scenario.snapshot_merchant_id,
        currency=scenario.catalog_currency,
        items=tuple(
            CatalogItem(
                sku=item.sku,
                merchant_id=item.merchant_id,
                effective_unit_price_minor=item.unit_price_minor,
                recurring=item.recurring,
            )
            for item in scenario.items
        ),
    )


def _mutated_transaction_hash(payload: TransactionPayload, delta: int) -> str:
    """Digest of a neighbouring transaction body, used as a stale commitment."""

    variant = TransactionPayload(
        transaction_id=payload.transaction_id,
        merchant_id=payload.merchant_id,
        cart_currency=payload.cart_currency,
        order_currency=payload.order_currency,
        declared_order_total_minor=payload.declared_order_total_minor + delta,
        declared_aggregate_quantity=payload.declared_aggregate_quantity,
        cart_recurring=payload.cart_recurring,
        order_recurring=payload.order_recurring,
        lines=payload.lines,
    )
    return sha256_canonical(variant)


def _mutated_catalog_hash(catalog: CatalogSnapshot, delta: int) -> str:
    """Digest of a neighbouring catalog snapshot, used as a stale commitment."""

    if catalog.items:
        head = catalog.items[0]
        variant_items = (
            CatalogItem(
                sku=head.sku,
                merchant_id=head.merchant_id,
                effective_unit_price_minor=head.effective_unit_price_minor + delta,
                recurring=head.recurring,
            ),
        ) + catalog.items[1:]
    else:
        variant_items = ()
    variant = CatalogSnapshot(
        snapshot_id=(
            catalog.snapshot_id if catalog.items else f"{catalog.snapshot_id}-prior"
        ),
        merchant_id=catalog.merchant_id,
        currency=catalog.currency,
        items=variant_items,
    )
    return catalog_snapshot_sha256(variant)


def build_inputs(scenario: Scenario) -> EvaluationInputs:
    """Assemble the typed, fully explicit evaluation inputs for one scenario."""

    if scenario.mandate_currency != scenario.mandate_currency.upper():
        raise RecipeError("currency codes must be uppercase")
    if not scenario.catalog_present and scenario.catalog_commitment != COMMITMENT_ABSENT:
        raise RecipeError("a catalog commitment requires a catalog snapshot")

    payload = _build_transaction_payload(scenario)
    catalog = _build_catalog(scenario)
    actual_transaction_hash = transaction_body_sha256(payload)
    declared_transaction_hash = (
        _mutated_transaction_hash(payload, scenario.commitment_variant_delta)
        if scenario.declared_transaction_hash_mutated
        else actual_transaction_hash
    )
    transaction = Transaction(
        payload=payload, declared_transaction_hash=declared_transaction_hash
    )

    declared_total = payload.declared_order_total_minor
    aggregate_quantity = payload.declared_aggregate_quantity
    max_total_minor = scenario.max_total_minor
    if max_total_minor is None:
        max_total_minor = (
            declared_total + 5_000 + (scenario.variation_index % 9) * 250
        )
    max_quantity = scenario.max_quantity
    if max_quantity is None:
        max_quantity = aggregate_quantity + 1 + (scenario.variation_index % 4)

    mandate = Mandate(
        payload=MandatePayload(
            mandate_id=scenario.mandate_id,
            nonce=scenario.nonce,
            issued_at=scenario.issued_at,
            expires_at=scenario.expires_at,
            subject_ref=scenario.subject_ref,
            currency=scenario.mandate_currency,
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=max_total_minor,
                    max_quantity=max_quantity,
                    recurring_allowed=scenario.recurring_allowed,
                    merchant_allowlist=scenario.merchant_allowlist,
                    sku_allowlist=scenario.sku_allowlist,
                ),
                semantic=(),
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY", issuer_id=scenario.issuer_id
        ),
    )

    if scenario.nonce_mode == NONCE_ABSENT:
        nonce_state: NonceLedgerState | None = None
    elif scenario.nonce_mode == NONCE_FRESH:
        nonce_state = NonceLedgerState(frozenset(scenario.consumed_nonces))
    elif scenario.nonce_mode == NONCE_CONSUMED:
        nonce_state = NonceLedgerState(
            frozenset(scenario.consumed_nonces) | {scenario.nonce}
        )
    else:
        raise RecipeError(f"unregistered nonce mode {scenario.nonce_mode!r}")

    committed_hashes: CommittedHashes | None = None
    if scenario.commitments_present:
        committed_hashes = CommittedHashes(
            transaction_sha256=_commitment_digest(
                scenario.transaction_commitment,
                actual_transaction_hash,
                lambda: _mutated_transaction_hash(
                    payload, scenario.commitment_variant_delta
                ),
            ),
            catalog_snapshot_sha256=_commitment_digest(
                scenario.catalog_commitment,
                None if catalog is None else catalog_snapshot_sha256(catalog),
                lambda: _mutated_catalog_hash(catalog, scenario.commitment_variant_delta),
            ),
        )

    return EvaluationInputs(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=scenario.evaluated_at if scenario.server_time_available else None,
        nonce_state=nonce_state,
        psp_committed_hashes=committed_hashes,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
    )


def _commitment_digest(
    mode: str, actual: str | None, mutated: Callable[[], str]
) -> str | None:
    if mode == COMMITMENT_ABSENT:
        return None
    if mode == COMMITMENT_ACTUAL:
        return actual
    if mode == COMMITMENT_MUTATED:
        return mutated()
    raise RecipeError(f"unregistered commitment mode {mode!r}")


@dataclass(frozen=True, slots=True)
class Recipe:
    """A registered recipe outcome: the scenario plus its audit parameters."""

    recipe_id: str
    scenario: Scenario
    parameters: dict[str, Any]


def _recipe(
    family_id: str, case_class: str, name: str, scenario: Scenario, **parameters: Any
) -> Recipe:
    tier_class = {"V": "violation", "P": "benign", "NE": "unavailable"}[case_class]
    return Recipe(
        recipe_id=f"{family_id}.{tier_class}.{name}",
        scenario=scenario,
        parameters=parameters,
    )


# --------------------------------------------------------------------------
# Tier A recipes
# --------------------------------------------------------------------------


def _catalog_absent(scenario: Scenario) -> Scenario:
    return replace(
        scenario, catalog_present=False, catalog_commitment=COMMITMENT_ABSENT
    )


def _catalog_commitment_absent(scenario: Scenario) -> Scenario:
    return replace(scenario, catalog_commitment=COMMITMENT_ABSENT)


def _a1_violation(base: Scenario, index: int) -> Recipe:
    """Offsetting per-SKU price mutations that preserve declared arithmetic.

    Each mutated pair shares a quantity, so ``+delta`` on one declared unit
    price and ``-delta`` on the other leaves every declared line total valid
    (B1) and leaves the declared order total equal to the catalog-derived total
    (A7), while A1 still observes two exact per-SKU price mismatches.
    """

    slug = _slug("A1", "V", index)
    pair_count = 1 + (index % 2)
    extra_line = 1 if index % 3 == 2 else 0
    lines: list[LineSpec] = []
    items: list[ItemSpec] = []
    merchant_id = base.merchant_id
    delta = 0
    for pair in range(pair_count):
        quantity = 1 + ((index + pair) % 3)
        low_price = 10_000 + pair * 1_500 + (index % 10) * 100
        high_price = low_price + 3_000
        delta = 100 + ((index + pair) % 9) * 50
        left = f"sku-{slug}-p{pair}a"
        right = f"sku-{slug}-p{pair}b"
        lines.append(LineSpec(sku=left, unit_price_minor=low_price + delta, quantity=quantity))
        lines.append(
            LineSpec(sku=right, unit_price_minor=high_price - delta, quantity=quantity)
        )
        items.append(ItemSpec(sku=left, merchant_id=merchant_id, unit_price_minor=low_price))
        items.append(
            ItemSpec(sku=right, merchant_id=merchant_id, unit_price_minor=high_price)
        )
    for extra in range(extra_line):
        sku = f"sku-{slug}-clean-{extra}"
        price = 8_800 + (index % 12) * 150
        lines.append(LineSpec(sku=sku, unit_price_minor=price, quantity=1 + (index % 2)))
        items.append(ItemSpec(sku=sku, merchant_id=merchant_id, unit_price_minor=price))
    catalog_total = sum(
        item.unit_price_minor * line.quantity
        for line, item in zip(lines, items, strict=True)
    )
    scenario = replace(
        base,
        lines=tuple(lines),
        items=tuple(items),
        declared_order_total_minor=catalog_total,
        sku_allowlist=tuple(sorted({line.sku for line in lines})),
    )
    if resolved_declared_total(scenario) != catalog_derived_total(scenario):
        raise RecipeError("A1 violation must preserve the catalog-derived total")
    return _recipe(
        "A1",
        "V",
        "offsetting_price_pair",
        scenario,
        pair_count=pair_count,
        extra_clean_lines=extra_line,
        last_pair_delta_minor=delta,
    )


def _a1_unavailable(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "A1", "NE", "catalog_snapshot_absent", _catalog_absent(base), variant="absent"
        )
    catalog_currency = _other_currency(base.order_currency, index)
    return _recipe(
        "A1",
        "NE",
        "catalog_currency_unavailable",
        replace(base, catalog_currency=catalog_currency),
        variant="foreign_currency_catalog",
        catalog_currency=catalog_currency,
    )


def _a2_violation(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        other_merchant = f"merchant-foreign-{index % 4}"
        head = base.items[0]
        items = (
            ItemSpec(
                sku=head.sku,
                merchant_id=other_merchant,
                unit_price_minor=head.unit_price_minor,
                recurring=head.recurring,
            ),
        ) + base.items[1:]
        return _recipe(
            "A2",
            "V",
            "sku_not_owned_by_declared_merchant",
            replace(base, items=items),
            variant="ownership_mismatch",
            foreign_merchant_id=other_merchant,
        )
    dropped = base.lines[0].sku
    items = tuple(item for item in base.items if item.sku != dropped)
    return _recipe(
        "A2",
        "V",
        "sku_absent_from_catalog",
        replace(base, items=items),
        variant="missing_sku",
        dropped_sku=dropped,
    )


def _a3_violation(base: Scenario, index: int) -> Recipe:
    snapshot_merchant = f"merchant-snapshot-{index % 5}"
    if index % 2 == 0:
        return _recipe(
            "A3",
            "V",
            "snapshot_merchant_mismatch_isolated",
            replace(base, snapshot_merchant_id=snapshot_merchant),
            variant="isolated",
            snapshot_merchant_id=snapshot_merchant,
        )
    items = tuple(
        ItemSpec(
            sku=item.sku,
            merchant_id=snapshot_merchant,
            unit_price_minor=item.unit_price_minor,
            recurring=item.recurring,
        )
        for item in base.items
    )
    return _recipe(
        "A3",
        "V",
        "snapshot_merchant_mismatch_with_ownership",
        replace(base, snapshot_merchant_id=snapshot_merchant, items=items),
        variant="composes_with_a2",
        snapshot_merchant_id=snapshot_merchant,
    )


def _a6_violation(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "A6",
            "V",
            "transaction_commitment_mismatch",
            replace(base, transaction_commitment=COMMITMENT_MUTATED),
            variant="transaction",
            commitment_variant_delta=base.commitment_variant_delta,
        )
    return _recipe(
        "A6",
        "V",
        "catalog_commitment_mismatch",
        replace(base, catalog_commitment=COMMITMENT_MUTATED),
        variant="catalog",
        commitment_variant_delta=base.commitment_variant_delta,
    )


def _a6_unavailable(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "A6",
            "NE",
            "psp_commitments_absent",
            replace(
                base,
                commitments_present=False,
                transaction_commitment=COMMITMENT_ABSENT,
                catalog_commitment=COMMITMENT_ABSENT,
            ),
            variant="no_commitment_record",
        )
    return _recipe(
        "A6",
        "NE",
        "catalog_commitment_absent",
        _catalog_commitment_absent(base),
        variant="catalog_commitment_missing",
    )


def _a7_violation(base: Scenario, index: int) -> Recipe:
    """Catalog-derived total above the mandate ceiling.

    A7 also requires the catalog-derived total to equal the declared charge.
    Breaking that equality is impossible without also breaking A1 or B1, so the
    registered A7 violation is the ceiling limb. It composes with B6 by
    construction, which the protocol permits and requires to be reported.
    """

    declared_total = resolved_declared_total(base)
    shortfall = 1 + (index % 5) * 37
    ceiling = declared_total - shortfall
    if ceiling < 0:
        raise RecipeError("A7 violation ceiling must stay non-negative")
    return _recipe(
        "A7",
        "V",
        "catalog_total_above_mandate_ceiling",
        replace(base, max_total_minor=ceiling),
        catalog_total_minor=declared_total,
        mandate_max_total_minor=ceiling,
        shortfall_minor=shortfall,
    )


def _a7_unavailable(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "A7",
            "NE",
            "transaction_commitment_absent",
            replace(base, transaction_commitment=COMMITMENT_ABSENT),
            variant="transaction_commitment_missing",
        )
    return _recipe(
        "A7", "NE", "catalog_snapshot_absent", _catalog_absent(base), variant="absent"
    )


def _a8_violation(base: Scenario, index: int) -> Recipe:
    head = base.items[0]
    items = (
        ItemSpec(
            sku=head.sku,
            merchant_id=head.merchant_id,
            unit_price_minor=head.unit_price_minor,
            recurring=True,
        ),
    ) + base.items[1:]
    return _recipe(
        "A8",
        "V",
        "catalog_recurring_sku_forbidden_by_mandate",
        replace(
            base,
            items=items,
            recurring_allowed=False,
            cart_recurring=False,
            order_recurring=False,
        ),
        recurring_sku=head.sku,
    )


def _a8_benign(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "A8",
            "P",
            "catalog_non_recurring_recurrence_forbidden",
            base,
            variant="non_recurring_catalog",
        )
    head = base.items[0]
    items = (
        ItemSpec(
            sku=head.sku,
            merchant_id=head.merchant_id,
            unit_price_minor=head.unit_price_minor,
            recurring=True,
        ),
    ) + base.items[1:]
    return _recipe(
        "A8",
        "P",
        "catalog_recurring_sku_permitted_by_mandate",
        replace(
            base,
            items=items,
            recurring_allowed=True,
            cart_recurring=False,
            order_recurring=False,
        ),
        variant="recurring_catalog_allowed",
        recurring_sku=head.sku,
    )


# --------------------------------------------------------------------------
# Tier B recipes
# --------------------------------------------------------------------------


def _b1_violation(base: Scenario, index: int) -> Recipe:
    """Break one declared line total while keeping the catalog-derived total."""

    position = index % len(base.lines)
    target = base.lines[position]
    correct_total = target.unit_price_minor * target.quantity
    delta = (100 + (index % 9) * 25) * (1 if index % 2 == 0 else -1)
    mutated_total = correct_total + delta
    if mutated_total < 0:
        raise RecipeError("B1 violation line total must stay non-negative")
    lines = (
        base.lines[:position]
        + (replace(target, line_total_minor=mutated_total),)
        + base.lines[position + 1 :]
    )
    scenario = replace(base, lines=lines)
    scenario = replace(
        scenario, declared_order_total_minor=catalog_derived_total(scenario)
    )
    return _recipe(
        "B1",
        "V",
        "line_total_arithmetic_broken",
        scenario,
        mutated_line_position=position,
        line_total_delta_minor=delta,
    )


def _b2_violation(base: Scenario, index: int) -> Recipe:
    line_quantity = sum(line.quantity for line in base.lines)
    deltas = (1, 2, -1)
    delta = deltas[index % len(deltas)]
    declared = line_quantity + delta
    if declared < 0:
        raise RecipeError("B2 declared aggregate quantity must stay non-negative")
    ceiling = max(line_quantity, declared) + 1 + (index % 4)
    return _recipe(
        "B2",
        "V",
        "aggregate_quantity_mismatch",
        replace(
            base, declared_aggregate_quantity=declared, max_quantity=ceiling
        ),
        line_quantity=line_quantity,
        declared_aggregate_quantity=declared,
        quantity_delta=delta,
    )


def _b3_violation(base: Scenario, index: int) -> Recipe:
    other = _other_currency(base.mandate_currency, index)
    if index % 2 == 0:
        return _recipe(
            "B3",
            "V",
            "cart_currency_mismatch",
            replace(base, cart_currency=other),
            variant="cart",
            mismatched_currency=other,
        )
    return _recipe(
        "B3",
        "V",
        "order_currency_mismatch",
        replace(base, order_currency=other),
        variant="order",
        mismatched_currency=other,
    )


def _b4_violation(base: Scenario, index: int) -> Recipe:
    scenario = replace(base, recurring_allowed=True)
    variant = index % 3
    if variant == 0:
        return _recipe(
            "B4",
            "V",
            "cart_recurrence_inconsistent",
            replace(scenario, cart_recurring=True, order_recurring=False),
            variant="cart_only",
        )
    if variant == 1:
        return _recipe(
            "B4",
            "V",
            "order_recurrence_inconsistent",
            replace(scenario, cart_recurring=False, order_recurring=True),
            variant="order_only",
        )
    lines = (replace(scenario.lines[0], recurring=True),) + scenario.lines[1:]
    return _recipe(
        "B4",
        "V",
        "line_recurrence_inconsistent",
        replace(scenario, lines=lines, cart_recurring=False, order_recurring=False),
        variant="line_only",
    )


def _b4_benign(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "B4",
            "P",
            "recurrence_consistently_absent",
            replace(base, cart_recurring=False, order_recurring=False),
            variant="all_false",
        )
    lines = tuple(replace(line, recurring=True) for line in base.lines)
    return _recipe(
        "B4",
        "P",
        "recurrence_consistently_declared",
        replace(
            base,
            lines=lines,
            cart_recurring=True,
            order_recurring=True,
            recurring_allowed=True,
        ),
        variant="all_true",
    )


def _b6_violation(base: Scenario, index: int) -> Recipe:
    """Declared order total above the mandate ceiling.

    Because the declared total equals the catalog-derived total in the baseline,
    this necessarily composes with A7's ceiling limb. The protocol permits that
    composition and forbids hiding it.
    """

    declared_total = resolved_declared_total(base)
    shortfall = 1 + (index % 7) * 29
    ceiling = declared_total - shortfall
    if ceiling < 0:
        raise RecipeError("B6 violation ceiling must stay non-negative")
    return _recipe(
        "B6",
        "V",
        "declared_total_above_mandate_ceiling",
        replace(base, max_total_minor=ceiling),
        declared_order_total_minor=declared_total,
        mandate_max_total_minor=ceiling,
        shortfall_minor=shortfall,
    )


def _b7_violation(base: Scenario, index: int) -> Recipe:
    lines = tuple(
        replace(line, quantity=2 + ((index + position) % 2))
        for position, line in enumerate(base.lines)
    )
    line_quantity = sum(line.quantity for line in lines)
    ceiling = max(1, line_quantity - 1 - (index % 2))
    if ceiling >= line_quantity:
        raise RecipeError("B7 violation must exceed the mandate quantity ceiling")
    return _recipe(
        "B7",
        "V",
        "aggregate_quantity_above_mandate_ceiling",
        replace(base, lines=lines, max_quantity=ceiling),
        declared_aggregate_quantity=line_quantity,
        mandate_max_quantity=ceiling,
    )


def _b8_violation(base: Scenario, index: int) -> Recipe:
    lines = tuple(replace(line, recurring=True) for line in base.lines)
    return _recipe(
        "B8",
        "V",
        "declared_recurrence_forbidden",
        replace(
            base,
            lines=lines,
            cart_recurring=True,
            order_recurring=True,
            recurring_allowed=False,
        ),
        declared_recurrence=True,
        recurring_allowed=False,
    )


def _b8_benign(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        return _recipe(
            "B8",
            "P",
            "no_declared_recurrence",
            replace(base, recurring_allowed=False),
            variant="recurrence_absent",
        )
    lines = tuple(replace(line, recurring=True) for line in base.lines)
    head = base.items[0]
    items = (
        ItemSpec(
            sku=head.sku,
            merchant_id=head.merchant_id,
            unit_price_minor=head.unit_price_minor,
            recurring=True,
        ),
    ) + base.items[1:]
    return _recipe(
        "B8",
        "P",
        "declared_recurrence_permitted",
        replace(
            base,
            lines=lines,
            items=items,
            cart_recurring=True,
            order_recurring=True,
            recurring_allowed=True,
        ),
        variant="recurrence_allowed",
    )


def _b9_violation(base: Scenario, index: int) -> Recipe:
    allowlist = tuple(
        sorted(
            {
                f"merchant-allowed-{index % 5}-a",
                f"merchant-allowed-{index % 5}-b",
            }
        )
    )
    if base.merchant_id in allowlist:
        raise RecipeError("B9 allowlist must exclude the declared merchant")
    return _recipe(
        "B9",
        "V",
        "declared_merchant_not_allowlisted",
        replace(base, merchant_allowlist=allowlist),
        declared_merchant=base.merchant_id,
        allowlist_size=len(allowlist),
    )


def _b9_benign(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        allowlist = tuple(
            sorted({base.merchant_id, f"merchant-allowed-{index % 5}"})
        )
        return _recipe(
            "B9",
            "P",
            "declared_merchant_allowlisted",
            replace(base, merchant_allowlist=allowlist),
            variant="allowlist_present",
        )
    return _recipe(
        "B9",
        "P",
        "merchant_allowlist_absent",
        replace(base, merchant_allowlist=None),
        variant="allowlist_absent",
    )


def _b10_violation(base: Scenario, index: int) -> Recipe:
    excluded = base.lines[0].sku
    allowlist = tuple(
        sorted(
            {line.sku for line in base.lines[1:]}
            | {f"sku-allowed-{_slug('B10', 'V', index)}"}
        )
    )
    if excluded in allowlist:
        raise RecipeError("B10 allowlist must exclude at least one declared SKU")
    return _recipe(
        "B10",
        "V",
        "declared_sku_not_allowlisted",
        replace(base, sku_allowlist=allowlist),
        excluded_sku=excluded,
        allowlist_size=len(allowlist),
    )


def _b10_benign(base: Scenario, index: int) -> Recipe:
    if index % 2 == 0:
        allowlist = tuple(
            sorted(
                {line.sku for line in base.lines}
                | {f"sku-allowed-{_slug('B10', 'P', index)}"}
            )
        )
        return _recipe(
            "B10",
            "P",
            "declared_skus_allowlisted",
            replace(base, sku_allowlist=allowlist),
            variant="allowlist_present",
        )
    return _recipe(
        "B10",
        "P",
        "sku_allowlist_absent",
        replace(base, sku_allowlist=None),
        variant="allowlist_absent",
    )


def _clean_benign(family_id: str) -> Callable[[Scenario, int], Recipe]:
    def recipe(base: Scenario, index: int) -> Recipe:
        return _recipe(family_id, "P", "fully_evidenced_baseline", base)

    return recipe


def _catalog_evidence_unavailable(family_id: str) -> Callable[[Scenario, int], Recipe]:
    def recipe(base: Scenario, index: int) -> Recipe:
        if index % 2 == 0:
            return _recipe(
                family_id,
                "NE",
                "catalog_snapshot_absent",
                _catalog_absent(base),
                variant="absent",
            )
        return _recipe(
            family_id,
            "NE",
            "catalog_commitment_absent",
            _catalog_commitment_absent(base),
            variant="commitment_missing",
        )

    return recipe


def _a4_violation(base: Scenario, index: int) -> Recipe:
    return _recipe(
        "A4",
        "V",
        "consumed_mandate_nonce",
        replace(base, nonce_mode=NONCE_CONSUMED),
        replayed_nonce=base.nonce,
    )


def _a4_unavailable(base: Scenario, index: int) -> Recipe:
    return _recipe("A4", "NE", "nonce_ledger_absent", replace(base, nonce_mode=NONCE_ABSENT))


def _a5_violation(base: Scenario, index: int) -> Recipe:
    lateness = timedelta(hours=1 + index % 5, minutes=(index * 11) % 60)
    server_time = base.expires_at + lateness
    return _recipe(
        "A5",
        "V",
        "mandate_expired_at_server_time",
        replace(base, evaluated_at=server_time),
        lateness_seconds=int(lateness.total_seconds()),
    )


def _a5_unavailable(base: Scenario, index: int) -> Recipe:
    return _recipe(
        "A5", "NE", "server_time_absent", replace(base, server_time_available=False)
    )


def _b5_violation(base: Scenario, index: int) -> Recipe:
    return _recipe(
        "B5",
        "V",
        "declared_transaction_hash_mismatch",
        replace(base, declared_transaction_hash_mutated=True),
        commitment_variant_delta=base.commitment_variant_delta,
    )


RECIPES: dict[tuple[str, str], Callable[[Scenario, int], Recipe]] = {
    ("A1", "V"): _a1_violation,
    ("A1", "P"): _clean_benign("A1"),
    ("A1", "NE"): _a1_unavailable,
    ("A2", "V"): _a2_violation,
    ("A2", "P"): _clean_benign("A2"),
    ("A2", "NE"): _catalog_evidence_unavailable("A2"),
    ("A3", "V"): _a3_violation,
    ("A3", "P"): _clean_benign("A3"),
    ("A3", "NE"): _catalog_evidence_unavailable("A3"),
    ("A4", "V"): _a4_violation,
    ("A4", "P"): _clean_benign("A4"),
    ("A4", "NE"): _a4_unavailable,
    ("A5", "V"): _a5_violation,
    ("A5", "P"): _clean_benign("A5"),
    ("A5", "NE"): _a5_unavailable,
    ("A6", "V"): _a6_violation,
    ("A6", "P"): _clean_benign("A6"),
    ("A6", "NE"): _a6_unavailable,
    ("A7", "V"): _a7_violation,
    ("A7", "P"): _clean_benign("A7"),
    ("A7", "NE"): _a7_unavailable,
    ("A8", "V"): _a8_violation,
    ("A8", "P"): _a8_benign,
    ("A8", "NE"): _catalog_evidence_unavailable("A8"),
    ("B1", "V"): _b1_violation,
    ("B1", "P"): _clean_benign("B1"),
    ("B2", "V"): _b2_violation,
    ("B2", "P"): _clean_benign("B2"),
    ("B3", "V"): _b3_violation,
    ("B3", "P"): _clean_benign("B3"),
    ("B4", "V"): _b4_violation,
    ("B4", "P"): _b4_benign,
    ("B5", "V"): _b5_violation,
    ("B5", "P"): _clean_benign("B5"),
    ("B6", "V"): _b6_violation,
    ("B6", "P"): _clean_benign("B6"),
    ("B7", "V"): _b7_violation,
    ("B7", "P"): _clean_benign("B7"),
    ("B8", "V"): _b8_violation,
    ("B8", "P"): _b8_benign,
    ("B9", "V"): _b9_violation,
    ("B9", "P"): _b9_benign,
    ("B10", "V"): _b10_violation,
    ("B10", "P"): _b10_benign,
}


def build_recipe(family_id: str, case_class: str, index: int) -> Recipe:
    """Resolve the registered recipe for one inventory slot."""

    if case_class == "NE" and family_id not in TIER_A_FAMILIES:
        raise RecipeError("only Tier A families register evidence-unavailable cases")
    try:
        recipe = RECIPES[(family_id, case_class)]
    except KeyError as error:
        raise RecipeError(
            f"no registered recipe for {family_id} class {case_class}"
        ) from error
    return recipe(default_scenario(family_id, case_class, index), index)
