"""Deterministic ALLOW/BLOCK decision composition for Tier A/B."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Iterable

from mandateguard.models.finding import Finding, TIER_A_FAMILIES, TIER_B_FAMILIES


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DecisionAction(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class DeterministicDecision:
    action: DecisionAction
    replay_seed: int
    evaluated_at: datetime
    transaction_sha256: str
    catalog_snapshot_sha256: str
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action, DecisionAction):
            raise ValueError("action must be a DecisionAction")
        if isinstance(self.replay_seed, bool) or not isinstance(self.replay_seed, int):
            raise ValueError("replay_seed must be an integer")
        if (
            not isinstance(self.evaluated_at, datetime)
            or self.evaluated_at.tzinfo is None
            or self.evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be a timezone-aware datetime")
        for digest, name in (
            (self.transaction_sha256, "transaction_sha256"),
            (self.catalog_snapshot_sha256, "catalog_snapshot_sha256"),
        ):
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        if not isinstance(self.findings, tuple) or not all(
            isinstance(finding, Finding) for finding in self.findings
        ):
            raise ValueError("findings must be a tuple of Finding values")
        expected = DecisionAction.BLOCK if self.findings else DecisionAction.ALLOW
        if self.action is not expected:
            raise ValueError("action must be BLOCK exactly when deterministic findings exist")


def decide_deterministically(
    *,
    replay_seed: int,
    evaluated_at: datetime,
    transaction_sha256: str,
    catalog_snapshot_sha256: str,
    tier_a_findings: Iterable[Finding],
    tier_b_findings: Iterable[Finding],
) -> DeterministicDecision:
    """Compose Tier A/B findings without generating time, IDs, or randomness."""

    tier_a = tuple(tier_a_findings)
    tier_b = tuple(tier_b_findings)
    if any(finding.family not in TIER_A_FAMILIES for finding in tier_a):
        raise ValueError("tier_a_findings contains a non-Tier-A family")
    if any(finding.family not in TIER_B_FAMILIES for finding in tier_b):
        raise ValueError("tier_b_findings contains a non-Tier-B family")
    findings = tier_a + tier_b
    action = DecisionAction.BLOCK if findings else DecisionAction.ALLOW
    return DeterministicDecision(
        action=action,
        replay_seed=replay_seed,
        evaluated_at=evaluated_at,
        transaction_sha256=transaction_sha256,
        catalog_snapshot_sha256=catalog_snapshot_sha256,
        findings=findings,
    )
