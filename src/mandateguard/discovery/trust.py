"""The discovery/authorization trust boundary, stated as executable rules.

ML understands the commerce universe. MandateGuard's deterministic gate controls
money.

Everything reachable from ``mandateguard.discovery`` is *advisory*. It retrieves,
ranks, classifies, detects anomalies, names evidence gaps, and explains. It may
never:

* issue an execution capability,
* override a deterministic ``BLOCK``,
* convert missing trusted evidence into ``ALLOW``,
* override a revocation, or
* override exact request binding.

The rules are here rather than in prose so tests can assert them and so a future
change that tries to widen the boundary has to edit a file whose only purpose is
to refuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


#: What a discovery-layer signal is permitted to influence.
ADVISORY_CAPABILITIES: Final[tuple[str, ...]] = (
    "RETRIEVE",
    "RANK",
    "CLASSIFY",
    "DETECT_ANOMALY",
    "SUGGEST_EVIDENCE_GAP",
    "EXPLAIN",
)

#: What no discovery-layer signal may ever do, at any confidence.
FORBIDDEN_CAPABILITIES: Final[tuple[str, ...]] = (
    "ISSUE_EXECUTION_CAPABILITY",
    "OVERRIDE_DETERMINISTIC_BLOCK",
    "SATISFY_MISSING_TRUSTED_EVIDENCE",
    "OVERRIDE_REVOCATION",
    "OVERRIDE_REQUEST_BINDING",
    "AUTHORIZE_PAYMENT",
)

BOUNDARY_STATEMENT: Final[str] = (
    "ML understands the commerce universe. "
    "MandateGuard's deterministic gate controls money."
)

#: The four states an arbitrary discovery listing can reach without trusted
#: merchant evidence. ``REVIEW_REQUIRED`` is the terminal state, and it is a
#: product feature: it is what "we will not guess about your money" looks like.
DISCOVERY_ONLY_STAGES: Final[tuple[str, ...]] = (
    "DISCOVERED",
    "MATCHED",
    "EVIDENCE_INCOMPLETE",
    "REVIEW_REQUIRED",
)


class TrustBoundaryViolation(RuntimeError):
    """A discovery-layer signal was used for something it may never decide."""


@dataclass(frozen=True, slots=True)
class AdvisorySignal:
    """A discovery-layer output, permanently tagged as advisory.

    Wrapping is not decoration. ``authorize`` raises rather than returning a
    falsy value, so a caller that tries to route a model score into an
    authorization decision fails loudly at the call site instead of quietly
    reading ``0``.
    """

    signal_id: str
    value: object
    produced_by: str

    def __post_init__(self) -> None:
        if not isinstance(self.signal_id, str) or not self.signal_id:
            raise ValueError("signal_id must be a non-empty string")
        if not isinstance(self.produced_by, str) or not self.produced_by:
            raise ValueError("produced_by must be a non-empty string")

    @property
    def authorization_authority(self) -> str:
        return "NONE"

    def authorize(self, *_args: object, **_kwargs: object) -> None:
        raise TrustBoundaryViolation(
            f"{self.signal_id} is advisory ({self.produced_by}); "
            f"{BOUNDARY_STATEMENT}"
        )


def assert_advisory_only(capability: str) -> None:
    """Raise unless ``capability`` is one the discovery layer may exercise."""

    if not isinstance(capability, str) or not capability:
        raise ValueError("capability must be a non-empty string")
    normalized = capability.strip().upper()
    if normalized in FORBIDDEN_CAPABILITIES:
        raise TrustBoundaryViolation(
            f"the discovery layer may not {normalized}; {BOUNDARY_STATEMENT}"
        )
    if normalized not in ADVISORY_CAPABILITIES:
        raise TrustBoundaryViolation(f"unregistered discovery capability: {normalized}")


def boundary_declaration() -> dict[str, object]:
    """Machine-readable boundary, served to the product surface verbatim."""

    return {
        "statement": BOUNDARY_STATEMENT,
        "ml_may": list(ADVISORY_CAPABILITIES),
        "ml_may_not": list(FORBIDDEN_CAPABILITIES),
        "discovery_only_stages": list(DISCOVERY_ONLY_STAGES),
        "authoritative_component": "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
        "discovery_catalog_is_trusted_evidence": False,
    }
