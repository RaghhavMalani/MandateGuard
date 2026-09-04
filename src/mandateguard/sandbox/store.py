"""Turning the generated world into the trusted store the controller reads.

The sandbox catalogue is large, so it is built once per process and shared. It
is immutable after construction, which is what makes sharing safe: a request
cannot mutate the world another request is being judged against.

The important boundary in this module is the one it does *not* cross. A
``TrustedCommerceStore`` is the type the authorization controller treats as
authoritative. Building one from the sandbox generator is legitimate precisely
because the generator is server-side, schema-valid, versioned and bound to exact
merchant/SKU pairs. Nothing here can build one from crawled marketplace text,
and ``sandbox_snapshot_id`` keeps the two snapshots distinguishable in every
audit record that mentions one.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.semantic.evidence import SemanticEvidenceEntry

from mandateguard.sandbox.templates import (
    BILLING_CONFLICT_SENTENCE,
    BILLING_ONE_TIME,
    BILLING_RECURRING,
    BILLING_UNDECLARED_SENTENCE,
    CONTENT_CLEAR,
    CONTENT_PROHIBITED,
    CONTENT_UNDECLARED_SENTENCE,
    PURPOSE_UNDECLARED_SENTENCE,
    WORLD_VERSION,
)
from mandateguard.sandbox.universe import (
    SandboxProduct,
    SandboxUniverse,
    build_universe,
    universe_manifest,
)


#: Snapshot identifier for every store built from the generated world. It names
#: the world version so a trace can never be mistaken for a registered-fixture
#: run or for a differently versioned sandbox.
SANDBOX_SNAPSHOT_ID = f"{WORLD_VERSION}-snapshot"

_lock = Lock()
_cached: tuple[SandboxUniverse, TrustedCommerceStore, dict[str, Any]] | None = None


def build_sandbox_store(universe: SandboxUniverse) -> TrustedCommerceStore:
    """Project a generated world into the controller's trusted store type."""

    return TrustedCommerceStore(
        snapshot_id=SANDBOX_SNAPSHOT_ID,
        products=tuple(product.commerce_product() for product in universe.products),
        evidence_entries=universe.evidence_entries,
    )


def sandbox_world() -> tuple[SandboxUniverse, TrustedCommerceStore, dict[str, Any]]:
    """Return the process-wide sandbox world, generating it on first use.

    Generation costs a few hundred milliseconds, so it is deliberately not done
    at import time: a deployment that never opens the Playground never pays for
    it, and the HTTP server binds its port without waiting.
    """

    global _cached
    with _lock:
        if _cached is None:
            universe = build_universe()
            store = build_sandbox_store(universe)
            manifest = universe_manifest(universe)
            manifest["snapshot_id"] = SANDBOX_SNAPSHOT_ID
            _cached = (universe, store, manifest)
        return _cached


# ---------------------------------------------------------------------------
# Declaration scanning
#
# The readiness signals shown beside a candidate are read out of the merchant's
# published evidence, not out of the generator's family label. That distinction
# is the whole point: a real merchant integration would have only the text, and
# a signal that quietly consulted generator metadata would be measuring the
# fixture rather than the evidence.
# ---------------------------------------------------------------------------

DECLARED = "DECLARED"
NOT_DECLARED = "NOT_DECLARED"
CONFLICTED = "CONFLICTED"


def scan_declarations(entries: tuple[SemanticEvidenceEntry, ...]) -> dict[str, str]:
    """Report what the supplied evidence actually declares, by reading it."""

    text = " ".join(entry.text for entry in entries)
    has_identity = "Merchant of record:" in text and "SKU ownership:" in text
    has_price = "Authoritative price:" in text
    if BILLING_CONFLICT_SENTENCE in text:
        billing = CONFLICTED
    elif BILLING_ONE_TIME in text or BILLING_RECURRING in text:
        billing = DECLARED
    elif BILLING_UNDECLARED_SENTENCE in text:
        billing = NOT_DECLARED
    else:
        billing = NOT_DECLARED
    if CONTENT_CLEAR in text or CONTENT_PROHIBITED in text:
        content = DECLARED
    elif CONTENT_UNDECLARED_SENTENCE in text:
        content = NOT_DECLARED
    else:
        content = NOT_DECLARED
    purpose = (
        NOT_DECLARED
        if PURPOSE_UNDECLARED_SENTENCE in text or "Intended use:" not in text
        else DECLARED
    )
    return {
        "merchant_identity": DECLARED if has_identity else NOT_DECLARED,
        "sku_evidence": DECLARED if has_identity else NOT_DECLARED,
        "authoritative_price": DECLARED if has_price else NOT_DECLARED,
        "billing_model": billing,
        "content_classification": content,
        "intended_use": purpose,
        "evidence_version": "CURRENT" if has_identity else "UNKNOWN",
    }


def readiness_for(
    store: TrustedCommerceStore, product: SandboxProduct
) -> dict[str, str]:
    """Agent-readiness signals for one sandbox listing, read from its evidence."""

    entries = store.evidence_for_product(
        merchant_id=product.merchant_id, sku=product.sku
    )
    return scan_declarations(entries)
