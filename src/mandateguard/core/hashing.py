"""SHA-256 content hashing over canonical structures."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.mandate import Mandate, MandatePayload
from mandateguard.models.transaction import Transaction, TransactionPayload


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_canonical(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def transaction_body_sha256(transaction: Transaction | TransactionPayload) -> str:
    """Hash the canonical transaction body, excluding declared_transaction_hash."""

    payload = transaction.payload if isinstance(transaction, Transaction) else transaction
    if not isinstance(payload, TransactionPayload):
        raise TypeError("transaction must be Transaction or TransactionPayload")
    return sha256_canonical(payload)


def catalog_snapshot_sha256(catalog: CatalogSnapshot) -> str:
    if not isinstance(catalog, CatalogSnapshot):
        raise TypeError("catalog must be CatalogSnapshot")
    return sha256_canonical(catalog)


def mandate_payload_sha256(mandate: Mandate | MandatePayload) -> str:
    payload = mandate.payload if isinstance(mandate, Mandate) else mandate
    if not isinstance(payload, MandatePayload):
        raise TypeError("mandate must be Mandate or MandatePayload")
    return sha256_canonical(payload)


@dataclass(frozen=True, slots=True)
class CommittedHashes:
    """PSP-side commitments captured before deterministic evaluation."""

    transaction_sha256: str | None
    catalog_snapshot_sha256: str | None

    def __post_init__(self) -> None:
        for digest, name in (
            (self.transaction_sha256, "transaction_sha256"),
            (self.catalog_snapshot_sha256, "catalog_snapshot_sha256"),
        ):
            if digest is not None and (
                not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
            ):
                raise ValueError(f"{name} must be null or a lowercase SHA-256 hex digest")
