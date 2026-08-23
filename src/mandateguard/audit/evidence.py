"""Canonical digests for decision-time evidence retained outside the journal."""

from __future__ import annotations

from hashlib import sha256

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.core.nonce_ledger import NonceLedgerState


def nonce_state_sha256(nonce_state: NonceLedgerState) -> str:
    """Hash an explicit, order-independent nonce-state representation.

    This digest binds a decision event to a separately retained nonce-state
    snapshot. The journal stores only the digest and therefore cannot reconstruct
    the consumed nonce set without that external snapshot.
    """

    if not isinstance(nonce_state, NonceLedgerState):
        raise TypeError("nonce_state must be a NonceLedgerState")
    representation = {
        "consumed_nonces": sorted(nonce_state.consumed_nonces),
    }
    return sha256(canonical_json_bytes(representation)).hexdigest()
