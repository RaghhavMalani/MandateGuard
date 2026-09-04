"""The product's discovery surface, and the seam to the authorization gate.

Discovery answers "what could an agent buy, and what do we actually know about
it". It stops there. Handing a discovered listing to the authorization
controller is a separate, explicit step that only a listing with registered
merchant evidence can take, and even then the controller decides on its own
terms.

Nothing in this module can issue a capability or reach a payment provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from mandateguard.discovery.search import (
    DiscoveryEngine,
    DiscoveryResult,
    TrustedListingFacts,
    preset_intents,
    try_load,
)
from mandateguard.discovery.transactability import DISCOVERY_ONLY_TERMINAL_STATUS
from mandateguard.discovery.trust import DISCOVERY_ONLY_STAGES, boundary_declaration
from mandateguard.intelligence.store import CommerceStoreError, TrustedCommerceStore
from mandateguard.intelligence.models import SelectedProductIdentity


REGISTERED_SOURCE = "mandateguard"
MAX_DISCOVERY_TOP_K = 12
_RECURRENCE_EVIDENCE_RE = re.compile(
    r"\b(one[- ]time|single (?:charge|payment)|does not renew|no recurring|"
    r"renews?|recurring|subscription|monthly)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DiscoverySelection:
    """The outcome of choosing one discovered listing to act on."""

    stage: str
    status: str
    transactable: bool
    next_step: str
    product_identity: SelectedProductIdentity | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "transactable": self.transactable,
            "next_step": self.next_step,
            "product_identity": (
                self.product_identity.to_mapping()
                if self.product_identity is not None
                else None
            ),
            "stages": list(DISCOVERY_ONLY_STAGES),
            "payment_provider_calls": 0,
        }


def build_trusted_lookup(
    store: TrustedCommerceStore,
) -> Any:
    """Map a discovery listing back to what the authorization store knows.

    Only listings imported from the registered merchant catalog can resolve, and
    the resolution is by merchant and SKU through the store's own API. A crawled
    listing that happens to share a title with a registered product resolves to
    nothing, because the identity that matters is the identifier, not the words.
    """

    def lookup(product: Any) -> TrustedListingFacts:
        if getattr(product, "source", None) != REGISTERED_SOURCE:
            return TrustedListingFacts()
        merchant_id, separator, sku = str(product.source_product_id).partition("/")
        if not separator or not merchant_id or not sku or "/" in sku:
            return TrustedListingFacts()
        try:
            registered = store.get_product(merchant_id=merchant_id, sku=sku)
            if registered.merchant_id != merchant_id or registered.sku != sku:
                return TrustedListingFacts()
            entries = store.evidence_for_product(merchant_id=merchant_id, sku=sku)
        except CommerceStoreError:
            return TrustedListingFacts()
        recurrence_evidenced = any(
            _RECURRENCE_EVIDENCE_RE.search(entry.text) for entry in entries
        )
        return TrustedListingFacts(
            evidence_count=len(entries),
            merchant_of_record=merchant_id,
            recurrence_evidenced=recurrence_evidenced,
            category_declared_by_merchant=True,
        )

    return lookup


class DiscoverySurface:
    """Holds the loaded engine, or the reason it is unavailable."""

    __slots__ = ("engine", "unavailable_reason")

    def __init__(
        self,
        *,
        processed_dir: Path,
        models_dir: Path,
        store: TrustedCommerceStore,
        with_embedding: bool = True,
    ) -> None:
        engine, reason = try_load(
            processed_dir=processed_dir,
            models_dir=models_dir,
            with_embedding=with_embedding,
            trusted_evidence_lookup=build_trusted_lookup(store),
        )
        self.engine: DiscoveryEngine | None = engine
        self.unavailable_reason = reason

    @property
    def available(self) -> bool:
        return self.engine is not None

    def public_config(self) -> dict[str, Any]:
        if self.engine is None:
            return {
                "available": False,
                "reason": self.unavailable_reason
                or "The discovery catalog is not built in this deployment.",
                "boundary": boundary_declaration(),
            }
        statistics = self.engine.statistics()
        return {
            "available": True,
            "catalog": {
                "listings": statistics["catalog_listings"],
                "categories": statistics["top_level_categories"],
                "category_paths": statistics["distinct_category_paths"],
                "brands": statistics["distinct_brands"],
                "listings_with_price": statistics["listings_with_price"],
                "index_bytes": statistics["index_bytes"],
                "catalog_bytes": statistics["catalog_bytes"],
                "cold_load_seconds": statistics["cold_load_seconds"],
            },
            "models": {
                "embedding_dimensions": statistics["embedding_dimensions"],
                "embedding_vocabulary": statistics["embedding_vocabulary"],
                "lexical_terms": statistics["lexical_terms"],
                "classifier_classes": statistics["classifier_classes"],
            },
            "provenance": statistics["provenance"],
            "presets": [dict(item) for item in preset_intents()],
            "boundary": boundary_declaration(),
            "max_top_k": MAX_DISCOVERY_TOP_K,
        }

    def search(self, intent: str, *, top_k: int = 6) -> dict[str, Any]:
        if self.engine is None:
            raise RuntimeError(
                self.unavailable_reason
                or "The discovery catalog is not built in this deployment."
            )
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent must be a non-empty string")
        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= MAX_DISCOVERY_TOP_K
        ):
            raise ValueError(f"top_k must be between 1 and {MAX_DISCOVERY_TOP_K}")
        result: DiscoveryResult = self.engine.search(intent, top_k=top_k)
        payload = result.to_mapping()
        payload["selection_by_product"] = {
            candidate.listing.product.catalog_product_id: select(
                candidate.to_mapping(), intent
            ).to_mapping()
            for candidate in result.candidates
        }
        return payload

    def select(self, intent: str, catalog_product_id: str, *, top_k: int = 12) -> dict[str, Any]:
        """Resolve one listing from a fresh search and report what happens next."""

        payload = self.search(intent, top_k=top_k)
        for candidate in payload["candidates"]:
            if candidate["catalog_product_id"] == catalog_product_id:
                return {
                    "candidate": candidate,
                    "selection": select(candidate, intent).to_mapping(),
                }
        raise KeyError("the selected listing is not in this intent's results")

    def resolve_selected_product(
        self, intent: str, catalog_product_id: str, *, top_k: int = 12
    ) -> SelectedProductIdentity:
        """Resolve a browser selection again on the server before authorization."""

        payload = self.select(intent, catalog_product_id, top_k=top_k)
        mapping = payload["selection"].get("product_identity")
        if mapping is None:
            raise ValueError("the selected catalog listing is not transactable")
        return SelectedProductIdentity.from_mapping(mapping)


def select(candidate: Mapping[str, Any], intent: str) -> DiscoverySelection:
    """What can actually be done with this listing, said plainly."""

    if candidate.get("transactable"):
        identity = _registered_identity(candidate)
        if identity is None:
            return _review_selection()
        return DiscoverySelection(
            stage=DISCOVERY_ONLY_STAGES[1],
            status="READY FOR AUTHORIZATION",
            transactable=True,
            next_step=(
                f"{identity.merchant_id} publishes authoritative terms for this "
                "registered product. The authorization controller can now decide it, and may "
                "still answer BLOCK or REVIEW."
            ),
            product_identity=identity,
        )
    return _review_selection()


def _review_selection() -> DiscoverySelection:
    return DiscoverySelection(
        stage=DISCOVERY_ONLY_STAGES[3],
        status=DISCOVERY_ONLY_TERMINAL_STATUS,
        transactable=False,
        next_step=(
            "This listing was discovered and matched, and no merchant has "
            "published authoritative terms for it. MandateGuard will not "
            "manufacture an ALLOW for a product nobody has vouched for, so the "
            "journey ends here with zero payment-provider calls."
        ),
        product_identity=None,
    )


def _registered_identity(
    candidate: Mapping[str, Any],
) -> SelectedProductIdentity | None:
    """Split a registered listing's ``merchant/sku`` source identifier."""

    product_id = str(candidate.get("catalog_product_id", ""))
    if candidate.get("source") != REGISTERED_SOURCE:
        return None
    raw = str(candidate.get("source_product_id", ""))
    merchant, separator, sku = raw.partition("/")
    if not separator or not merchant or not sku or "/" in sku:
        return None
    try:
        return SelectedProductIdentity(
            merchant_id=merchant,
            sku=sku,
            catalog_product_id=product_id,
            source=REGISTERED_SOURCE,
            source_product_id=raw,
        )
    except (TypeError, ValueError):
        return None
