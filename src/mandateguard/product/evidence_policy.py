"""One server-owned trust-sensitive evidence policy for product and evaluation.

The Commerce Lab, the Resolve evaluator, and the tests all read this module.
Nothing derived from presentation may influence it: `preset_id` selects an
intent, never an evidence policy. Any caller that overrides a field here is
recorded as an override so parity between the product and the evaluation is
verifiable instead of assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mandateguard.intelligence.retrieval import DEFAULT_ALPHA, DEFAULT_TOP_K
from mandateguard.intelligence.retrieval.hybrid import RetrievalMode
from mandateguard.recovery import MAX_ACQUISITION_ROUNDS, MAX_NEW_EVIDENCE_ITEMS


EVIDENCE_POLICY_ID = "MANDATEGUARD_PRODUCT_EVIDENCE_POLICY_V1"

#: Fields whose value can change which trusted evidence reaches authorization.
#: Product and evaluation runs must agree on every one of them.
TRUST_SENSITIVE_FIELDS: tuple[str, ...] = (
    "policy_id",
    "top_k",
    "alpha",
    "retrieval_mode",
    "max_acquisition_rounds",
    "max_new_evidence_items",
    "evidence_policy_overridden",
    "registry_sha256",
    "semantic_mode",
)


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Immutable retrieval and acquisition budget shared by every scenario."""

    policy_id: str
    top_k: int
    alpha: float
    retrieval_mode: RetrievalMode
    max_acquisition_rounds: int
    max_new_evidence_items: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ValueError("policy_id must be a non-empty string")
        if (
            isinstance(self.top_k, bool)
            or not isinstance(self.top_k, int)
            or self.top_k < 1
        ):
            raise ValueError("top_k must be a positive integer")
        if (
            isinstance(self.alpha, bool)
            or not isinstance(self.alpha, (int, float))
            or not 0.0 <= float(self.alpha) <= 1.0
        ):
            raise ValueError("alpha must be within [0, 1]")
        if not isinstance(self.retrieval_mode, RetrievalMode):
            raise TypeError("retrieval_mode must be RetrievalMode")
        for value, name in (
            (self.max_acquisition_rounds, "max_acquisition_rounds"),
            (self.max_new_evidence_items, "max_new_evidence_items"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def describe(
        self, *, top_k: int, registry_sha256: str, semantic_mode: str
    ) -> dict[str, Any]:
        """Return the trust-sensitive configuration a single run actually used."""

        return {
            "policy_id": self.policy_id,
            "top_k": top_k,
            "alpha": float(self.alpha),
            "retrieval_mode": self.retrieval_mode.value,
            "max_acquisition_rounds": self.max_acquisition_rounds,
            "max_new_evidence_items": self.max_new_evidence_items,
            "evidence_policy_overridden": top_k != self.top_k,
            "registry_sha256": registry_sha256,
            "semantic_mode": semantic_mode,
        }


PRODUCT_EVIDENCE_POLICY = EvidencePolicy(
    policy_id=EVIDENCE_POLICY_ID,
    top_k=DEFAULT_TOP_K,
    alpha=DEFAULT_ALPHA,
    retrieval_mode=RetrievalMode.HYBRID,
    max_acquisition_rounds=MAX_ACQUISITION_ROUNDS,
    max_new_evidence_items=MAX_NEW_EVIDENCE_ITEMS,
)
