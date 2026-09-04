"""Agent transactability: what an autonomous buyer would still need.

Most of the world's product listings are readable by an agent and *not*
transactable by one. The listing says what the thing is and what it costs; it
does not say who is selling it as a matter of record, that this identifier is
theirs, or whether the charge repeats. A human infers those from context. A
system that is about to move money cannot.

This diagnostic names the gap for one listing, in the order a buyer would hit
it:

    DISCOVERABLE      -> can an agent find it at all?
    PRICE AVAILABLE   -> is there a number to check a budget against?
    CATEGORY UNDERSTOOD -> do we know what kind of thing it is?
    MERCHANT IDENTITY -> who is the seller of record?
    SKU TRUST EVIDENCE -> does the merchant vouch for this identifier?
    RECURRENCE TERMS  -> will this charge once, or forever?

It is diagnostic. A perfect score authorizes nothing: the score describes how
much is known, and the authorization controller decides separately whether what
is known is enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from mandateguard.discovery.schema import DiscoveryProduct
from mandateguard.discovery.trust import AdvisorySignal


DIAGNOSTIC_VERSION = "agent-transactability-v1"

YES = "YES"
NO = "NO"
UNRESOLVED = "UNRESOLVED"

#: The stages a listing without trusted merchant evidence can reach.
DISCOVERY_ONLY_TERMINAL_STATUS = "REVIEW REQUIRED"


@dataclass(frozen=True, slots=True)
class TransactabilityCheck:
    label: str
    status: str
    detail: str

    def to_mapping(self) -> dict[str, Any]:
        return {"label": self.label, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class TransactabilityReport:
    checks: tuple[TransactabilityCheck, ...]
    status: str
    next_action: str
    resolved: int
    total: int

    @property
    def blocking(self) -> tuple[TransactabilityCheck, ...]:
        return tuple(item for item in self.checks if item.status != YES)

    def as_signal(self) -> AdvisorySignal:
        return AdvisorySignal(
            signal_id="agent_transactability",
            value=self.status,
            produced_by=DIAGNOSTIC_VERSION,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "diagnostic_version": DIAGNOSTIC_VERSION,
            "checks": [item.to_mapping() for item in self.checks],
            "status": self.status,
            "next_action": self.next_action,
            "resolved": self.resolved,
            "total": self.total,
            "authority_notice": (
                "Diagnostic only. This surface cannot authorize a payment, and a "
                "complete score is not an authorization."
            ),
            "authorization_authority": "NONE",
        }


def assess_listing(
    product: DiscoveryProduct,
    *,
    category_understood: bool,
    trusted_evidence_count: int = 0,
    merchant_of_record: str | None = None,
    recurrence_evidenced: bool = False,
) -> TransactabilityReport:
    """Diagnose one discovery listing against what a payment would require."""

    checks: list[TransactabilityCheck] = [
        TransactabilityCheck(
            label="DISCOVERABLE",
            status=YES,
            detail=(
                f"Indexed from {product.source} and retrievable by free-text "
                "intent."
            ),
        ),
        TransactabilityCheck(
            label="PRICE AVAILABLE",
            status=YES if product.price_minor is not None else NO,
            detail=(
                f"{product.currency} {product.price_minor / 100:,.2f} is published "
                "by the listing. It is a listing claim, not authoritative merchant "
                "price evidence."
                if product.price_minor is not None
                else "This listing publishes no price, so no budget can be checked."
            ),
        ),
        TransactabilityCheck(
            label="CATEGORY UNDERSTOOD",
            status=YES if category_understood else UNRESOLVED,
            detail=(
                f"Classified as {product.top_category}."
                if category_understood
                else "The listing's own taxonomy places it outside any category "
                "the model was trained on."
            ),
        ),
        TransactabilityCheck(
            label="MERCHANT IDENTITY",
            status=YES if merchant_of_record else UNRESOLVED,
            detail=(
                f"Seller of record: {merchant_of_record}."
                if merchant_of_record
                else (
                    f"Listed on {product.merchant_or_seller or 'an unnamed platform'}. "
                    "A marketplace is not a seller of record, and this dataset "
                    "does not publish one."
                )
            ),
        ),
        TransactabilityCheck(
            label="SKU TRUST EVIDENCE",
            status=YES if trusted_evidence_count > 0 else UNRESOLVED,
            detail=(
                f"{trusted_evidence_count} merchant-controlled evidence items "
                "resolve for this exact merchant and SKU."
                if trusted_evidence_count > 0
                else "No merchant vouches for this identifier. A crawled row is a "
                "claim about a product, not the merchant's own statement."
            ),
        ),
        TransactabilityCheck(
            label="RECURRENCE TERMS",
            status=YES if recurrence_evidenced else UNRESOLVED,
            detail=(
                "Authoritative terms state how often this charges."
                if recurrence_evidenced
                else "Nothing authoritative says whether this charges once or "
                "repeats. It cannot be assumed either way."
            ),
        ),
    ]
    resolved = sum(1 for item in checks if item.status == YES)
    total = len(checks)
    if resolved == total:
        status = "EVIDENCE READY"
        next_action = (
            "Everything a payment needs is known. The authorization controller "
            "decides separately whether it permits this transaction."
        )
    elif any(item.status == NO for item in checks):
        status = DISCOVERY_ONLY_TERMINAL_STATUS
        next_action = (
            "The listing is missing a value a payment requires outright, not "
            "merely an unverified one."
        )
    else:
        status = DISCOVERY_ONLY_TERMINAL_STATUS
        missing = ", ".join(
            item.label.lower() for item in checks if item.status != YES
        )
        next_action = (
            f"The merchant must expose authoritative SKU terms ({missing}) "
            "before an agent can transact this listing."
        )
    return TransactabilityReport(
        checks=tuple(checks),
        status=status,
        next_action=next_action,
        resolved=resolved,
        total=total,
    )


def summarize(reports: Sequence[TransactabilityReport]) -> dict[str, Any]:
    """Aggregate over a result page, for the catalog-wide honest headline."""

    if not reports:
        return {"listings": 0, "evidence_ready": 0, "review_required": 0}
    ready = sum(1 for item in reports if item.status == "EVIDENCE READY")
    return {
        "listings": len(reports),
        "evidence_ready": ready,
        "review_required": len(reports) - ready,
        "mean_resolved_checks": round(
            sum(item.resolved for item in reports) / len(reports), 3
        ),
        "checks_per_listing": reports[0].total,
    }
