"""Constrained deterministic policy findings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, TypeAlias


FindingDetail: TypeAlias = str | int | bool | None


class TaxonomyFamily(str, Enum):
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"
    A7 = "A7"
    A8 = "A8"
    B1 = "B1"
    B2 = "B2"
    B3 = "B3"
    B4 = "B4"
    B5 = "B5"
    B6 = "B6"
    B7 = "B7"
    B8 = "B8"
    B9 = "B9"
    B10 = "B10"
    C_DEV_RECURRENCE = "C-DEV-RECURRENCE"
    C_DEV_EXCLUSION = "C-DEV-EXCLUSION"
    C_DEV_PURPOSE = "C-DEV-PURPOSE"
    C_HOLD_BUNDLE = "C-HOLD-BUNDLE"
    C_HOLD_COMPATIBILITY = "C-HOLD-COMPATIBILITY"
    C_HOLD_FULFILLMENT = "C-HOLD-FULFILLMENT"


REGISTERED_FAMILIES = frozenset(TaxonomyFamily)
TIER_A_FAMILIES = frozenset(family for family in TaxonomyFamily if family.value.startswith("A"))
TIER_B_FAMILIES = frozenset(family for family in TaxonomyFamily if family.value.startswith("B"))


class TierACheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True, slots=True)
class Finding:
    family: TaxonomyFamily
    message: str
    details: tuple[tuple[str, FindingDetail], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.family, TaxonomyFamily):
            raise ValueError("family must be a registered TaxonomyFamily")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("message must be a non-empty string")
        if not isinstance(self.details, tuple):
            raise ValueError("details must be a tuple")
        keys: list[str] = []
        for item in self.details:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("each finding detail must be a key/value tuple")
            key, value = item
            if not isinstance(key, str) or not key:
                raise ValueError("finding detail keys must be non-empty strings")
            if isinstance(value, float) or not isinstance(value, (str, int, bool, type(None))):
                raise ValueError("finding detail values must be JSON scalar values without floats")
            keys.append(key)
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("finding details must have unique keys in sorted order")

    @classmethod
    def create(
        cls,
        family: TaxonomyFamily,
        message: str,
        details: Mapping[str, FindingDetail] | None = None,
    ) -> Finding:
        ordered = tuple(sorted((details or {}).items()))
        return cls(family=family, message=message, details=ordered)


@dataclass(frozen=True, slots=True)
class TierACheckResult:
    """One explicit A-family outcome, including evidence-unavailable REVIEW state."""

    family: TaxonomyFamily
    status: TierACheckStatus
    finding: Finding | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.family, TaxonomyFamily) or self.family not in TIER_A_FAMILIES:
            raise ValueError("Tier A check result family must be A1-A8")
        if not isinstance(self.status, TierACheckStatus):
            raise ValueError("status must be a TierACheckStatus")
        if self.status is TierACheckStatus.FAIL:
            if not isinstance(self.finding, Finding) or self.finding.family is not self.family:
                raise ValueError("FAIL requires a same-family Finding")
            if self.reason is not None:
                raise ValueError("FAIL records its reason in the Finding")
        elif self.status is TierACheckStatus.NOT_EVALUABLE:
            if self.finding is not None:
                raise ValueError("NOT_EVALUABLE must not be represented as a violation Finding")
            if not isinstance(self.reason, str) or not self.reason:
                raise ValueError("NOT_EVALUABLE requires an evidence-unavailability reason")
        elif self.finding is not None or self.reason is not None:
            raise ValueError("PASS must not carry a finding or reason")
