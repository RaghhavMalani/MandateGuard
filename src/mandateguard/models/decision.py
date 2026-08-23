"""Deterministic ALLOW/REVIEW/BLOCK decision composition for Tier A/B."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Iterable

from mandateguard.models.finding import (
    Finding,
    TIER_A_FAMILIES,
    TIER_B_FAMILIES,
    TaxonomyFamily,
    TierACheckResult,
    TierACheckStatus,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIER_A_ORDER = (
    TaxonomyFamily.A1,
    TaxonomyFamily.A2,
    TaxonomyFamily.A3,
    TaxonomyFamily.A4,
    TaxonomyFamily.A5,
    TaxonomyFamily.A6,
    TaxonomyFamily.A7,
    TaxonomyFamily.A8,
)


class DecisionAction(str, Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class DeterministicDecision:
    action: DecisionAction
    replay_seed: int
    evaluated_at: datetime
    transaction_sha256: str
    catalog_snapshot_sha256: str | None
    tier_a_results: tuple[TierACheckResult, ...]
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
        if not isinstance(self.transaction_sha256, str) or not _SHA256_RE.fullmatch(
            self.transaction_sha256
        ):
            raise ValueError("transaction_sha256 must be a lowercase SHA-256 hex digest")
        if self.catalog_snapshot_sha256 is not None and (
            not isinstance(self.catalog_snapshot_sha256, str)
            or not _SHA256_RE.fullmatch(self.catalog_snapshot_sha256)
        ):
            raise ValueError(
                "catalog_snapshot_sha256 must be null or a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.findings, tuple) or not all(
            isinstance(finding, Finding) for finding in self.findings
        ):
            raise ValueError("findings must be a tuple of Finding values")
        if not isinstance(self.tier_a_results, tuple) or not all(
            isinstance(result, TierACheckResult) for result in self.tier_a_results
        ):
            raise ValueError("tier_a_results must be a tuple of TierACheckResult values")
        tier_a_families = tuple(result.family for result in self.tier_a_results)
        if tier_a_families != _TIER_A_ORDER:
            raise ValueError("tier_a_results must contain A1-A8 exactly once in canonical order")
        expected = _decision_action(self.tier_a_results, self.findings)
        if self.action is not expected:
            raise ValueError("action does not match deterministic enforcement precedence")


def _decision_action(
    tier_a_results: tuple[TierACheckResult, ...], findings: tuple[Finding, ...]
) -> DecisionAction:
    if findings:
        return DecisionAction.BLOCK
    if any(
        result.status is TierACheckStatus.NOT_EVALUABLE for result in tier_a_results
    ):
        return DecisionAction.REVIEW
    return DecisionAction.ALLOW


def decide_deterministically(
    *,
    replay_seed: int,
    evaluated_at: datetime,
    transaction_sha256: str,
    catalog_snapshot_sha256: str | None,
    tier_a_results: Iterable[TierACheckResult],
    tier_b_findings: Iterable[Finding],
) -> DeterministicDecision:
    """Compose Tier A/B findings without generating time, IDs, or randomness."""

    tier_a = tuple(tier_a_results)
    tier_b = tuple(tier_b_findings)
    if any(result.family not in TIER_A_FAMILIES for result in tier_a):
        raise ValueError("tier_a_results contains a non-Tier-A family")
    if any(finding.family not in TIER_B_FAMILIES for finding in tier_b):
        raise ValueError("tier_b_findings contains a non-Tier-B family")
    tier_a_findings = tuple(
        result.finding
        for result in tier_a
        if result.status is TierACheckStatus.FAIL and result.finding is not None
    )
    findings = tier_a_findings + tier_b
    action = _decision_action(tier_a, findings)
    return DeterministicDecision(
        action=action,
        replay_seed=replay_seed,
        evaluated_at=evaluated_at,
        transaction_sha256=transaction_sha256,
        catalog_snapshot_sha256=catalog_snapshot_sha256,
        tier_a_results=tier_a,
        findings=findings,
    )
