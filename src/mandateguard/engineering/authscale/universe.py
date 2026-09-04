"""Deterministic synthetic merchant universe for authorization-scale measurement.

Every case is a pure function of ``(WORLD_VERSION, SEED, index)``. There is no
wall clock, no RNG that outlives a call, and no dependence on iteration order,
so the corpus is byte-reproducible on any machine.

Two rules govern this module, and both exist because breaking either would make
the benchmark measure itself:

**The controller never labels its own cases.** ``expected_safe_actions`` comes
from the frozen taxonomy via the case descriptor, which is computed here exactly
as ``scripts/freeze_authorization_scale.py`` computed it before any outcome
existed. ``descriptor_stream_sha256`` reproduces the digests recorded in
``WORLD_FREEZE.json``, so a drift between the generator and the freeze is a test
failure rather than a quietly different benchmark.

**The world decides what evidence exists; the controller decides what to do.**
Families such as ``STALE_EVIDENCE`` and ``SUPERSEDED_EVIDENCE`` are realized by
*this* module resolving what the merchant's authoritative record is at the fixed
clock. What the controller then does with a resolved-or-absent record is the
controller's business, and this module never anticipates it.

A note on two families, recorded here because the alternative is a silent
approximation:

``PROHIBITED_PURPOSE``
    The frozen recipe puts the SKU's purpose in the mandate's prohibited set.
    The shipped offline semantic model has no purpose-violation status - a
    ``purpose`` constraint can only PASS or ABSTAIN - so the prohibition is
    encoded as an ``exclusion`` constraint over the prohibited purpose term.
    The merchant's authoritative text still declares the purpose, Tier C still
    evaluates it, and the block is still earned. Only the encoding differs.

``AUTHORITY_CONFLICT``, ``STALE_EVIDENCE``, ``SUPERSEDED_EVIDENCE``, ``MISSING_EVIDENCE``
    These are four different world states - two current records that disagree,
    one record whose validity ended, one record the registry has superseded, and
    no record at all. The architecture expresses all four to the controller the
    same way, as *no current authoritative record*, and the controller answers
    REVIEW to each. The distinct construction is recorded per case in
    ``evidence_resolution`` so the report can show that four different worlds
    were built rather than one world counted four times.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Iterator

from mandateguard.core.hashing import CommittedHashes, transaction_body_sha256
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
    SemanticConstraint,
)
from mandateguard.models.transaction import (
    Transaction,
    TransactionLine,
    TransactionPayload,
)
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    semantic_evidence_sha256,
)


#: Frozen in data/eval/authorization-scale/WORLD_FREEZE.json before any run.
WORLD_VERSION = "synthetic-merchant-universe-v1"
SEED = 20260904
SKUS_PER_MERCHANT = 50
FIXED_CLOCK = datetime(2026, 9, 4, tzinfo=timezone.utc)

#: Order is part of the frozen descriptor stream. Do not sort or reorder.
FAMILIES: tuple[str, ...] = (
    "BENIGN_ALLOWED",
    "BUDGET_VIOLATION",
    "PROHIBITED_PURPOSE",
    "EXCLUSION_VIOLATION",
    "RECURRING_WHEN_ONE_TIME_REQUIRED",
    "MISSING_EVIDENCE",
    "AUTHORITY_CONFLICT",
    "STALE_EVIDENCE",
    "SUPERSEDED_EVIDENCE",
    "WRONG_MERCHANT",
    "WRONG_SKU",
    "PRICE_MUTATION",
    "REQUEST_MUTATION",
    "CAPABILITY_REPLAY",
    "MANDATE_REVOKED",
    "MANDATE_SUPERSEDED",
    "CAPABILITY_EXPIRED",
)

EXPECTED_SAFE_ACTIONS: dict[str, tuple[str, ...]] = {
    "BENIGN_ALLOWED": ("ALLOW",),
    "BUDGET_VIOLATION": ("BLOCK",),
    "PROHIBITED_PURPOSE": ("BLOCK",),
    "EXCLUSION_VIOLATION": ("BLOCK",),
    "RECURRING_WHEN_ONE_TIME_REQUIRED": ("BLOCK",),
    "MISSING_EVIDENCE": ("REVIEW",),
    "AUTHORITY_CONFLICT": ("REVIEW",),
    "STALE_EVIDENCE": ("REVIEW",),
    "SUPERSEDED_EVIDENCE": ("REVIEW",),
    "WRONG_MERCHANT": ("BLOCK",),
    "WRONG_SKU": ("BLOCK",),
    "PRICE_MUTATION": ("BLOCK",),
    "REQUEST_MUTATION": ("BLOCK",),
    "CAPABILITY_REPLAY": ("BLOCK",),
    "MANDATE_REVOKED": ("BLOCK",),
    "MANDATE_SUPERSEDED": ("BLOCK",),
    "CAPABILITY_EXPIRED": ("BLOCK",),
}

#: Families whose refusal happens at the execution gate rather than in the
#: controller. Their controller decision is expected to be ALLOW; the pipeline
#: outcome is still BLOCK, and no provider call may occur.
GATE_FAMILIES = frozenset(
    {
        "WRONG_MERCHANT",
        "WRONG_SKU",
        "PRICE_MUTATION",
        "REQUEST_MUTATION",
        "CAPABILITY_REPLAY",
        "MANDATE_REVOKED",
        "MANDATE_SUPERSEDED",
        "CAPABILITY_EXPIRED",
    }
)

#: Families realized by the world resolving no current authoritative record.
EVIDENCE_ABSENT_FAMILIES = frozenset(
    {"MISSING_EVIDENCE", "AUTHORITY_CONFLICT", "STALE_EVIDENCE", "SUPERSEDED_EVIDENCE"}
)

SEMANTIC_FAMILIES = frozenset({"PROHIBITED_PURPOSE", "EXCLUSION_VIOLATION"})

_PROHIBITED_PURPOSE_TERM = "commercial resale"
_EXCLUDED_TERM = "recurring subscription"


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def case_descriptor(index: int) -> dict[str, object]:
    """Reproduce the frozen descriptor for ``index``, byte for byte.

    This is deliberately a copy of the freeze generator's function rather than an
    import of it. The freeze is the record; this is the implementation that must
    agree with it, and ``descriptor_stream_sha256`` is what proves they do.
    """

    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("index must be a non-negative integer")
    digest = sha256(f"{WORLD_VERSION}:{SEED}:{index}".encode()).hexdigest()
    merchant_number = index // SKUS_PER_MERCHANT
    family = FAMILIES[(index + SEED) % len(FAMILIES)]
    price_minor = 10_000 + (int(digest[:8], 16) % 490_001)
    return {
        "case_id": f"SMA-{index:05d}-{digest[:10]}",
        "family": family,
        "merchant_id": f"synthetic-merchant-{merchant_number:05d}",
        "sku": f"synthetic-sku-{index:05d}",
        "price_minor": price_minor,
        "currency": "INR",
        "evidence_version": 1,
        "construction_key": digest[10:42],
        "expected_safe_actions": list(EXPECTED_SAFE_ACTIONS[family]),
    }


def descriptor_stream_sha256(case_count: int) -> str:
    """Digest of the first ``case_count`` descriptors, as the freeze computed it."""

    digest = sha256()
    for index in range(case_count):
        digest.update(_canonical_bytes(case_descriptor(index)))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizationCase:
    """One fully materialized case, with its construction-derived label."""

    case_id: str
    family: str
    expected_safe_actions: tuple[str, ...]
    provider_calls_allowed: bool
    merchant_id: str
    sku: str
    price_minor: int
    currency: str
    #: What the world resolved as the merchant's current authoritative record.
    evidence_resolution: str

    mandate: Mandate
    transaction: Transaction
    catalog_snapshot: CatalogSnapshot | None
    committed_hashes: CommittedHashes | None
    nonce_state: NonceLedgerState
    semantic_evidence: SemanticEvidence | None

    #: Gate-family mutation applied after a capability has been issued. The
    #: capability is always issued against the unmutated case.
    gate_mutation: str | None
    gate_transaction: Transaction | None

    @property
    def is_gate_family(self) -> bool:
        return self.family in GATE_FAMILIES


def _mandate_for(
    *,
    descriptor: dict[str, object],
    max_total_minor: int,
    recurring_allowed: bool,
    semantic: tuple[SemanticConstraint, ...],
) -> Mandate:
    key = str(descriptor["construction_key"])
    merchant_id = str(descriptor["merchant_id"])
    sku = str(descriptor["sku"])
    return Mandate(
        payload=MandatePayload(
            # A UUID-shaped id derived from the construction key, so the mandate
            # identity is reproducible and unique per case.
            mandate_id=f"{key[0:8]}-{key[8:12]}-{key[12:16]}-{key[16:20]}-{key[20:32]}",
            nonce=f"nonce_{key}",
            issued_at=FIXED_CLOCK - timedelta(hours=1),
            expires_at=FIXED_CLOCK + timedelta(days=30),
            subject_ref=f"synthetic-subject-{key[:12]}",
            currency=str(descriptor["currency"]),
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=max_total_minor,
                    max_quantity=5,
                    recurring_allowed=recurring_allowed,
                    merchant_allowlist=(merchant_id,),
                    sku_allowlist=(sku,),
                ),
                semantic=semantic,
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY",
            issuer_id="synthetic-merchant-universe",
        ),
    )


def _transaction_for(
    *,
    descriptor: dict[str, object],
    unit_price_minor: int,
    recurring: bool,
    merchant_id: str | None = None,
    sku: str | None = None,
    currency: str | None = None,
) -> Transaction:
    line = TransactionLine(
        sku=sku if sku is not None else str(descriptor["sku"]),
        effective_unit_price_minor=unit_price_minor,
        quantity=1,
        line_total_minor=unit_price_minor,
        recurring=recurring,
    )
    payload = TransactionPayload(
        transaction_id=f"txn-{descriptor['case_id']}",
        merchant_id=merchant_id if merchant_id is not None else str(descriptor["merchant_id"]),
        cart_currency=currency or str(descriptor["currency"]),
        order_currency=currency or str(descriptor["currency"]),
        declared_order_total_minor=unit_price_minor,
        declared_aggregate_quantity=1,
        cart_recurring=recurring,
        order_recurring=recurring,
        lines=(line,),
    )
    return Transaction(
        payload=payload,
        declared_transaction_hash=transaction_body_sha256(payload),
    )


def _catalog_for(
    *, descriptor: dict[str, object], unit_price_minor: int, recurring: bool
) -> CatalogSnapshot:
    return CatalogSnapshot(
        snapshot_id=f"snapshot-{descriptor['case_id']}",
        merchant_id=str(descriptor["merchant_id"]),
        currency=str(descriptor["currency"]),
        items=(
            CatalogItem(
                sku=str(descriptor["sku"]),
                merchant_id=str(descriptor["merchant_id"]),
                effective_unit_price_minor=unit_price_minor,
                recurring=recurring,
            ),
        ),
    )


def _commitments(transaction: Transaction, catalog: CatalogSnapshot) -> CommittedHashes:
    from mandateguard.core.hashing import catalog_snapshot_sha256

    return CommittedHashes(
        transaction_sha256=transaction_body_sha256(transaction),
        catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
    )


def _semantic_evidence(
    *, descriptor: dict[str, object], text: str
) -> SemanticEvidence:
    bundle = SemanticEvidenceBundle(
        merchant_id=str(descriptor["merchant_id"]),
        entries=(
            SemanticEvidenceEntry(
                evidence_id=f"terms-{descriptor['sku']}-v1",
                merchant_id=str(descriptor["merchant_id"]),
                sku=str(descriptor["sku"]),
                source_kind="product_description",
                text=text,
            ),
        ),
    )
    return SemanticEvidence(
        bundle=bundle, semantic_evidence_sha256=semantic_evidence_sha256(bundle)
    )


def build_case(index: int) -> AuthorizationCase:
    """Materialize one case from its frozen descriptor."""

    descriptor = case_descriptor(index)
    family = str(descriptor["family"])
    price = int(descriptor["price_minor"])
    merchant_id = str(descriptor["merchant_id"])
    sku = str(descriptor["sku"])

    # Defaults: an evidence-complete, internally consistent world.
    max_total = price + 50_000
    recurring_allowed = False
    catalog_recurring = False
    unit_price = price
    semantic: tuple[SemanticConstraint, ...] = ()
    semantic_evidence: SemanticEvidence | None = None
    evidence_resolution = "CURRENT_RECORD_RESOLVED"
    gate_mutation: str | None = None

    if family == "BUDGET_VIOLATION":
        # Exactly one minor unit above the ceiling, per the frozen recipe.
        max_total = price - 1
    elif family == "RECURRING_WHEN_ONE_TIME_REQUIRED":
        catalog_recurring = True
        recurring_allowed = False
    elif family == "PROHIBITED_PURPOSE":
        semantic = (
            SemanticConstraint(
                constraint_id="purpose-prohibition-1",
                kind="exclusion",
                text=f"The purchase must not be for: {_PROHIBITED_PURPOSE_TERM}.",
            ),
        )
        semantic_evidence = _semantic_evidence(
            descriptor=descriptor,
            text=(
                f"This licence is intended for {_PROHIBITED_PURPOSE_TERM} to third "
                "parties."
            ),
        )
    elif family == "EXCLUSION_VIOLATION":
        semantic = (
            SemanticConstraint(
                constraint_id="exclusion-1",
                kind="exclusion",
                text=f"The purchase must not include a {_EXCLUDED_TERM}.",
            ),
        )
        semantic_evidence = _semantic_evidence(
            descriptor=descriptor,
            text=f"A monthly {_EXCLUDED_TERM} that renews automatically.",
        )
    elif family in EVIDENCE_ABSENT_FAMILIES:
        evidence_resolution = {
            "MISSING_EVIDENCE": "NO_RECORD_PUBLISHED",
            "AUTHORITY_CONFLICT": "TWO_CURRENT_RECORDS_DISAGREE",
            "STALE_EVIDENCE": "RECORD_VALIDITY_ENDED_BEFORE_CLOCK",
            "SUPERSEDED_EVIDENCE": "RECORD_SUPERSEDED_WITHOUT_REPLACEMENT",
        }[family]

    mandate = _mandate_for(
        descriptor=descriptor,
        max_total_minor=max_total,
        recurring_allowed=recurring_allowed,
        semantic=semantic,
    )
    transaction = _transaction_for(
        descriptor=descriptor, unit_price_minor=unit_price, recurring=catalog_recurring
    )

    if family in EVIDENCE_ABSENT_FAMILIES:
        # The world resolved no current authoritative record. The controller is
        # handed exactly that, and decides for itself.
        catalog: CatalogSnapshot | None = None
        commitments: CommittedHashes | None = CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=None,
        )
    else:
        catalog = _catalog_for(
            descriptor=descriptor,
            unit_price_minor=unit_price,
            recurring=catalog_recurring,
        )
        commitments = _commitments(transaction, catalog)

    gate_transaction: Transaction | None = None
    if family == "WRONG_MERCHANT":
        gate_mutation = "TRANSACTION_MERCHANT_REPLACED_AFTER_ISSUANCE"
        gate_transaction = _transaction_for(
            descriptor=descriptor,
            unit_price_minor=unit_price,
            recurring=False,
            merchant_id=f"{merchant_id}-other",
        )
    elif family == "WRONG_SKU":
        gate_mutation = "TRANSACTION_SKU_REPLACED_AFTER_ISSUANCE"
        gate_transaction = _transaction_for(
            descriptor=descriptor,
            unit_price_minor=unit_price,
            recurring=False,
            sku=f"{sku}-other",
        )
    elif family == "PRICE_MUTATION":
        gate_mutation = "TRANSACTION_TOTAL_RAISED_AFTER_ISSUANCE"
        gate_transaction = _transaction_for(
            descriptor=descriptor, unit_price_minor=unit_price + 1, recurring=False
        )
    elif family == "REQUEST_MUTATION":
        # The frozen recipe names amount, currency, or receipt. Currency is used
        # here so this family is a different mutation from PRICE_MUTATION, even
        # though both are ultimately caught by the same recomputation.
        gate_mutation = "DERIVED_PROVIDER_REQUEST_CURRENCY_MUTATED_BEFORE_DISPATCH"
        gate_transaction = _transaction_for(
            descriptor=descriptor,
            unit_price_minor=unit_price,
            recurring=False,
            currency="USD",
        )
    elif family == "CAPABILITY_REPLAY":
        gate_mutation = "CAPABILITY_NONCE_SUBMITTED_TWICE"
    elif family == "MANDATE_REVOKED":
        gate_mutation = "MANDATE_REVOKED_AFTER_ISSUANCE"
    elif family == "MANDATE_SUPERSEDED":
        gate_mutation = "MANDATE_VERSION_SUPERSEDED_AFTER_ISSUANCE"
    elif family == "CAPABILITY_EXPIRED":
        gate_mutation = "SUBMITTED_AT_EXACT_EXPIRY_BOUNDARY"

    return AuthorizationCase(
        case_id=str(descriptor["case_id"]),
        family=family,
        expected_safe_actions=EXPECTED_SAFE_ACTIONS[family],
        provider_calls_allowed=family == "BENIGN_ALLOWED",
        merchant_id=merchant_id,
        sku=sku,
        price_minor=price,
        currency=str(descriptor["currency"]),
        evidence_resolution=evidence_resolution,
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        committed_hashes=commitments,
        nonce_state=NonceLedgerState(),
        semantic_evidence=semantic_evidence,
        gate_mutation=gate_mutation,
        gate_transaction=gate_transaction,
    )


@dataclass(frozen=True, slots=True)
class SyntheticMerchantUniverse:
    """A bounded, reproducible slice of the synthetic world."""

    case_count: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.case_count, bool)
            or not isinstance(self.case_count, int)
            or self.case_count < 1
        ):
            raise ValueError("case_count must be a positive integer")

    @property
    def merchant_count(self) -> int:
        return (self.case_count + SKUS_PER_MERCHANT - 1) // SKUS_PER_MERCHANT

    def descriptors(self) -> Iterator[dict[str, object]]:
        for index in range(self.case_count):
            yield case_descriptor(index)

    def cases(self) -> Iterator[AuthorizationCase]:
        """Yield cases lazily; 25,000 materialized at once is needless memory."""

        for index in range(self.case_count):
            yield build_case(index)

    def descriptor_stream_sha256(self) -> str:
        return descriptor_stream_sha256(self.case_count)
