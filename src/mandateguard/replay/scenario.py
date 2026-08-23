"""Complete explicit input model for deterministic Tier A/B replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mandateguard.core.hashing import CommittedHashes
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.mandate import Mandate
from mandateguard.models.transaction import Transaction


@dataclass(frozen=True, slots=True)
class ReplayScenario:
    """Every policy input and audit value needed to reproduce one decision."""

    mandate: Mandate
    transaction: Transaction
    catalog_snapshot: CatalogSnapshot | None
    server_time: datetime | None
    nonce_state: NonceLedgerState | None
    psp_committed_hashes: CommittedHashes | None
    replay_seed: int
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.mandate, Mandate):
            raise TypeError("mandate must be Mandate")
        if not isinstance(self.transaction, Transaction):
            raise TypeError("transaction must be Transaction")
        if self.catalog_snapshot is not None and not isinstance(
            self.catalog_snapshot, CatalogSnapshot
        ):
            raise TypeError("catalog_snapshot must be CatalogSnapshot or None")
        for value, name in (
            (self.server_time, "server_time"),
            (self.evaluated_at, "evaluated_at"),
        ):
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{name} must be a timezone-aware datetime or None")
        if not isinstance(self.evaluated_at, datetime):
            raise ValueError("evaluated_at must be a timezone-aware datetime")
        if self.nonce_state is not None and not isinstance(
            self.nonce_state, NonceLedgerState
        ):
            raise TypeError("nonce_state must be NonceLedgerState or None")
        if self.psp_committed_hashes is not None and not isinstance(
            self.psp_committed_hashes, CommittedHashes
        ):
            raise TypeError("psp_committed_hashes must be CommittedHashes or None")
        if isinstance(self.replay_seed, bool) or not isinstance(self.replay_seed, int):
            raise ValueError("replay_seed must be an integer")
