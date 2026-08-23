"""Canonical, deterministic representation of a Tier A/B decision event."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import re
from typing import Any

from mandateguard.core.canonical import canonical_json_bytes
from mandateguard.models.decision import DecisionAction
from mandateguard.models.finding import (
    Finding,
    TIER_B_FAMILIES,
    TaxonomyFamily,
    TierACheckResult,
    TierACheckStatus,
)


EVENT_SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIER_A_ORDER = tuple(TaxonomyFamily(f"A{index}") for index in range(1, 9))
_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "sequence",
        "replay_seed",
        "evaluated_at",
        "mandate_payload_sha256",
        "transaction_body_sha256",
        "catalog_snapshot_sha256",
        "tier_a_results",
        "tier_b_findings",
        "action",
        "previous_event_sha256",
        "event_sha256",
    }
)
_FINDING_FIELDS = frozenset({"family", "message", "details"})
_TIER_A_RESULT_FIELDS = frozenset({"family", "status", "finding", "reason"})


class DecisionEventValidationError(ValueError):
    """Raised when decoded data is not a valid canonical decision event."""


def _require_exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], name: str
) -> None:
    if not all(isinstance(key, str) for key in value):
        raise DecisionEventValidationError(f"{name} keys must be strings")
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise DecisionEventValidationError(
            f"{name} fields do not match schema; missing={missing}, extra={extra}"
        )


def _require_sha256(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        suffix = " or null" if nullable else ""
        raise DecisionEventValidationError(
            f"{name} must be a lowercase SHA-256 hex digest{suffix}"
        )


def _finding_data(finding: Finding) -> dict[str, Any]:
    return {
        "family": finding.family.value,
        "message": finding.message,
        "details": dict(finding.details),
    }


def _tier_a_result_data(result: TierACheckResult) -> dict[str, Any]:
    return {
        "family": result.family.value,
        "status": result.status.value,
        "finding": _finding_data(result.finding) if result.finding is not None else None,
        "reason": result.reason,
    }


def _body_data(
    *,
    schema_version: str,
    sequence: int,
    replay_seed: int,
    evaluated_at: datetime,
    mandate_payload_sha256: str,
    transaction_body_sha256: str,
    catalog_snapshot_sha256: str | None,
    tier_a_results: tuple[TierACheckResult, ...],
    tier_b_findings: tuple[Finding, ...],
    action: DecisionAction,
    previous_event_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "sequence": sequence,
        "replay_seed": replay_seed,
        "evaluated_at": evaluated_at,
        "mandate_payload_sha256": mandate_payload_sha256,
        "transaction_body_sha256": transaction_body_sha256,
        "catalog_snapshot_sha256": catalog_snapshot_sha256,
        "tier_a_results": [_tier_a_result_data(result) for result in tier_a_results],
        "tier_b_findings": [_finding_data(finding) for finding in tier_b_findings],
        "action": action.value,
        "previous_event_sha256": previous_event_sha256,
    }


def _expected_action(
    tier_a_results: tuple[TierACheckResult, ...],
    tier_b_findings: tuple[Finding, ...],
) -> DecisionAction:
    if tier_b_findings or any(
        result.status is TierACheckStatus.FAIL for result in tier_a_results
    ):
        return DecisionAction.BLOCK
    if any(
        result.status is TierACheckStatus.NOT_EVALUABLE for result in tier_a_results
    ):
        return DecisionAction.REVIEW
    return DecisionAction.ALLOW


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    """One hash-addressed Tier A/B decision with no runtime-generated values."""

    schema_version: str
    sequence: int
    replay_seed: int
    evaluated_at: datetime
    mandate_payload_sha256: str
    transaction_body_sha256: str
    catalog_snapshot_sha256: str | None
    tier_a_results: tuple[TierACheckResult, ...]
    tier_b_findings: tuple[Finding, ...]
    action: DecisionAction
    previous_event_sha256: str | None
    event_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise DecisionEventValidationError(
                f"schema_version must be {EVENT_SCHEMA_VERSION}"
            )
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise DecisionEventValidationError("sequence must be a positive integer")
        if isinstance(self.replay_seed, bool) or not isinstance(self.replay_seed, int):
            raise DecisionEventValidationError("replay_seed must be an integer")
        if (
            not isinstance(self.evaluated_at, datetime)
            or self.evaluated_at.tzinfo is None
            or self.evaluated_at.utcoffset() is None
        ):
            raise DecisionEventValidationError(
                "evaluated_at must be a timezone-aware datetime"
            )
        _require_sha256(self.mandate_payload_sha256, "mandate_payload_sha256")
        _require_sha256(self.transaction_body_sha256, "transaction_body_sha256")
        _require_sha256(
            self.catalog_snapshot_sha256,
            "catalog_snapshot_sha256",
            nullable=True,
        )
        _require_sha256(
            self.previous_event_sha256,
            "previous_event_sha256",
            nullable=True,
        )
        _require_sha256(self.event_sha256, "event_sha256")
        if not isinstance(self.tier_a_results, tuple) or not all(
            isinstance(result, TierACheckResult) for result in self.tier_a_results
        ):
            raise DecisionEventValidationError(
                "tier_a_results must be a tuple of TierACheckResult values"
            )
        if tuple(result.family for result in self.tier_a_results) != _TIER_A_ORDER:
            raise DecisionEventValidationError(
                "tier_a_results must contain A1-A8 exactly once in canonical order"
            )
        if not isinstance(self.tier_b_findings, tuple) or not all(
            isinstance(finding, Finding) for finding in self.tier_b_findings
        ):
            raise DecisionEventValidationError(
                "tier_b_findings must be a tuple of Finding values"
            )
        if any(finding.family not in TIER_B_FAMILIES for finding in self.tier_b_findings):
            raise DecisionEventValidationError(
                "tier_b_findings may contain only B1-B10 findings"
            )
        if not isinstance(self.action, DecisionAction):
            raise DecisionEventValidationError("action must be a DecisionAction")
        if self.action is not _expected_action(
            self.tier_a_results, self.tier_b_findings
        ):
            raise DecisionEventValidationError(
                "action does not match deterministic Tier A/B precedence"
            )

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        replay_seed: int,
        evaluated_at: datetime,
        mandate_payload_sha256: str,
        transaction_body_sha256: str,
        catalog_snapshot_sha256: str | None,
        tier_a_results: tuple[TierACheckResult, ...],
        tier_b_findings: tuple[Finding, ...],
        action: DecisionAction,
        previous_event_sha256: str | None,
        schema_version: str = EVENT_SCHEMA_VERSION,
    ) -> DecisionEvent:
        body = _body_data(
            schema_version=schema_version,
            sequence=sequence,
            replay_seed=replay_seed,
            evaluated_at=evaluated_at,
            mandate_payload_sha256=mandate_payload_sha256,
            transaction_body_sha256=transaction_body_sha256,
            catalog_snapshot_sha256=catalog_snapshot_sha256,
            tier_a_results=tier_a_results,
            tier_b_findings=tier_b_findings,
            action=action,
            previous_event_sha256=previous_event_sha256,
        )
        event_sha256 = sha256(canonical_json_bytes(body)).hexdigest()
        return cls(
            schema_version=schema_version,
            sequence=sequence,
            replay_seed=replay_seed,
            evaluated_at=evaluated_at,
            mandate_payload_sha256=mandate_payload_sha256,
            transaction_body_sha256=transaction_body_sha256,
            catalog_snapshot_sha256=catalog_snapshot_sha256,
            tier_a_results=tier_a_results,
            tier_b_findings=tier_b_findings,
            action=action,
            previous_event_sha256=previous_event_sha256,
            event_sha256=event_sha256,
        )

    def body_data(self) -> dict[str, Any]:
        return _body_data(
            schema_version=self.schema_version,
            sequence=self.sequence,
            replay_seed=self.replay_seed,
            evaluated_at=self.evaluated_at,
            mandate_payload_sha256=self.mandate_payload_sha256,
            transaction_body_sha256=self.transaction_body_sha256,
            catalog_snapshot_sha256=self.catalog_snapshot_sha256,
            tier_a_results=self.tier_a_results,
            tier_b_findings=self.tier_b_findings,
            action=self.action,
            previous_event_sha256=self.previous_event_sha256,
        )

    def record_data(self) -> dict[str, Any]:
        record = self.body_data()
        record["event_sha256"] = self.event_sha256
        return record

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DecisionEvent:
        """Decode a JSON object strictly; unknown or absent fields are rejected."""

        if not isinstance(value, Mapping):
            raise DecisionEventValidationError("decision event must be an object")
        _require_exact_fields(value, _EVENT_FIELDS, "decision event")
        try:
            evaluated_at = _parse_timestamp(value["evaluated_at"])
            tier_a_raw = value["tier_a_results"]
            tier_b_raw = value["tier_b_findings"]
            if not isinstance(tier_a_raw, list):
                raise DecisionEventValidationError("tier_a_results must be an array")
            if not isinstance(tier_b_raw, list):
                raise DecisionEventValidationError("tier_b_findings must be an array")
            tier_a_results = tuple(_parse_tier_a_result(item) for item in tier_a_raw)
            tier_b_findings = tuple(_parse_finding(item) for item in tier_b_raw)
            action = DecisionAction(value["action"])
            return cls(
                schema_version=value["schema_version"],
                sequence=value["sequence"],
                replay_seed=value["replay_seed"],
                evaluated_at=evaluated_at,
                mandate_payload_sha256=value["mandate_payload_sha256"],
                transaction_body_sha256=value["transaction_body_sha256"],
                catalog_snapshot_sha256=value["catalog_snapshot_sha256"],
                tier_a_results=tier_a_results,
                tier_b_findings=tier_b_findings,
                action=action,
                previous_event_sha256=value["previous_event_sha256"],
                event_sha256=value["event_sha256"],
            )
        except DecisionEventValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise DecisionEventValidationError("invalid decision event value") from error


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise DecisionEventValidationError("evaluated_at must be a timestamp string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DecisionEventValidationError("evaluated_at is not an ISO timestamp") from error


def _parse_finding(value: object) -> Finding:
    if not isinstance(value, Mapping):
        raise DecisionEventValidationError("finding must be an object")
    _require_exact_fields(value, _FINDING_FIELDS, "finding")
    details = value["details"]
    if not isinstance(details, Mapping):
        raise DecisionEventValidationError("finding details must be an object")
    try:
        return Finding.create(
            family=TaxonomyFamily(value["family"]),
            message=value["message"],
            details=details,
        )
    except (TypeError, ValueError) as error:
        raise DecisionEventValidationError("invalid finding value") from error


def _parse_tier_a_result(value: object) -> TierACheckResult:
    if not isinstance(value, Mapping):
        raise DecisionEventValidationError("Tier A result must be an object")
    _require_exact_fields(value, _TIER_A_RESULT_FIELDS, "Tier A result")
    finding_raw = value["finding"]
    finding = None if finding_raw is None else _parse_finding(finding_raw)
    try:
        return TierACheckResult(
            family=TaxonomyFamily(value["family"]),
            status=TierACheckStatus(value["status"]),
            finding=finding,
            reason=value["reason"],
        )
    except (TypeError, ValueError) as error:
        raise DecisionEventValidationError("invalid Tier A result value") from error


def canonical_event_body_bytes(event: DecisionEvent) -> bytes:
    if not isinstance(event, DecisionEvent):
        raise TypeError("event must be a DecisionEvent")
    return canonical_json_bytes(event.body_data())


def canonical_event_bytes(event: DecisionEvent) -> bytes:
    if not isinstance(event, DecisionEvent):
        raise TypeError("event must be a DecisionEvent")
    return canonical_json_bytes(event.record_data())
