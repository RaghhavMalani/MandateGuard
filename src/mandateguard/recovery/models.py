"""Immutable value objects for bounded REVIEW recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re

from mandateguard.core.hashing import sha256_canonical
from mandateguard.models.decision import DecisionAction
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.evidence import SemanticEvidence, SemanticEvidenceEntry


MAX_ACQUISITION_ROUNDS = 2
MAX_NEW_EVIDENCE_ITEMS = 4

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier")


def _bounded_text(value: object, name: str, maximum: int = 1000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty and at most {maximum} characters")


def _aware(value: object, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware")


def _digest(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


class EvidenceKind(str, Enum):
    PURPOSE = "PURPOSE_EVIDENCE"
    RECURRENCE = "RECURRENCE_TERMS"
    EXCLUSION = "EXCLUSION_EVIDENCE"


class GapAnalysisStatus(str, Enum):
    RECOVERABLE = "RECOVERABLE_GAP"
    NONE = "NO_RECOVERABLE_GAP"


class AcquisitionItemStatus(str, Enum):
    ACQUIRED = "ACQUIRED"
    NO_RECORD = "SOURCE_RETURNED_NO_RECORD"
    WRONG_BINDING = "EVIDENCE_BINDING_REJECTED"
    DUPLICATE = "DUPLICATE_EVIDENCE_REJECTED"
    TAMPERED = "TAMPERED_EVIDENCE_REJECTED"


class RecoveryEventType(str, Enum):
    REVIEW_CREATED = "REVIEW_CREATED"
    EVIDENCE_GAP_IDENTIFIED = "EVIDENCE_GAP_IDENTIFIED"
    EVIDENCE_ACQUISITION_STARTED = "EVIDENCE_ACQUISITION_STARTED"
    EVIDENCE_ACQUIRED = "EVIDENCE_ACQUIRED"
    AUTHORIZATION_REEVALUATED = "AUTHORIZATION_REEVALUATED"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"


@dataclass(frozen=True, slots=True)
class TrustedEvidenceSource:
    """Server-configured source metadata; never constructed from buyer input."""

    source_id: str
    evidence_id: str
    display_name: str
    merchant_id: str
    sku: str
    evidence_kinds: tuple[EvidenceKind, ...]
    expected_entry_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.source_id, "source_id")
        _identifier(self.evidence_id, "evidence_id")
        _bounded_text(self.display_name, "display_name", 160)
        _identifier(self.merchant_id, "merchant_id")
        _identifier(self.sku, "sku")
        if (
            not isinstance(self.evidence_kinds, tuple)
            or not self.evidence_kinds
            or not all(isinstance(item, EvidenceKind) for item in self.evidence_kinds)
        ):
            raise ValueError("evidence_kinds must be a non-empty EvidenceKind tuple")
        if len(self.evidence_kinds) != len(set(self.evidence_kinds)):
            raise ValueError("evidence_kinds must be unique")
        _digest(self.expected_entry_sha256, "expected_entry_sha256")


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    constraint_id: str
    constraint_family: str
    reason: str
    missing_evidence_kind: EvidenceKind
    merchant_id: str
    sku: str
    candidate_evidence_ids: tuple[str, ...]
    diagnostic_source: str
    created_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.constraint_id, "constraint_id")
        _identifier(self.constraint_family, "constraint_family")
        _bounded_text(self.reason, "reason")
        if not isinstance(self.missing_evidence_kind, EvidenceKind):
            raise TypeError("missing_evidence_kind must be EvidenceKind")
        _identifier(self.merchant_id, "merchant_id")
        _identifier(self.sku, "sku")
        if not isinstance(self.candidate_evidence_ids, tuple) or not all(
            isinstance(item, str) and _IDENTIFIER_RE.fullmatch(item)
            for item in self.candidate_evidence_ids
        ):
            raise ValueError("candidate_evidence_ids must be a bounded tuple")
        if len(self.candidate_evidence_ids) != len(set(self.candidate_evidence_ids)):
            raise ValueError("candidate_evidence_ids must be unique")
        _identifier(self.diagnostic_source, "diagnostic_source")
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class EvidenceGapAnalysis:
    status: GapAnalysisStatus
    gaps: tuple[EvidenceGap, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, GapAnalysisStatus):
            raise TypeError("status must be GapAnalysisStatus")
        if not isinstance(self.gaps, tuple) or not all(
            isinstance(gap, EvidenceGap) for gap in self.gaps
        ):
            raise TypeError("gaps must be an EvidenceGap tuple")
        if self.status is GapAnalysisStatus.RECOVERABLE and not self.gaps:
            raise ValueError("RECOVERABLE_GAP requires at least one gap")
        if self.status is GapAnalysisStatus.NONE and self.gaps:
            raise ValueError("NO_RECOVERABLE_GAP cannot contain gaps")


def evidence_set_sha256(
    *, merchant_id: str, entries: tuple[SemanticEvidenceEntry, ...]
) -> str:
    """Commit empty and non-empty evidence sets using one canonical shape."""

    _identifier(merchant_id, "merchant_id")
    if not isinstance(entries, tuple) or not all(
        isinstance(entry, SemanticEvidenceEntry) for entry in entries
    ):
        raise TypeError("entries must be a SemanticEvidenceEntry tuple")
    ordered = tuple(sorted(entries, key=lambda entry: entry.evidence_id))
    return sha256_canonical({"merchant_id": merchant_id, "entries": ordered})


@dataclass(frozen=True, slots=True)
class RecoveryAuditEvent:
    sequence: int
    event: RecoveryEventType
    recorded_at: datetime
    review_id: str
    round_number: int
    evidence_set_sha256: str
    authorization_result_sha256: str | None
    evidence_ids: tuple[str, ...]
    outcome_codes: tuple[str, ...]
    decision: DecisionAction
    previous_event_sha256: str | None
    event_sha256: str

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be positive")
        if not isinstance(self.event, RecoveryEventType):
            raise TypeError("event must be RecoveryEventType")
        _aware(self.recorded_at, "recorded_at")
        _identifier(self.review_id, "review_id")
        if isinstance(self.round_number, bool) or not isinstance(self.round_number, int) or self.round_number < 0:
            raise ValueError("round_number must be non-negative")
        _digest(self.evidence_set_sha256, "evidence_set_sha256")
        _digest(
            self.authorization_result_sha256,
            "authorization_result_sha256",
            nullable=True,
        )
        for values, name in (
            (self.evidence_ids, "evidence_ids"),
            (self.outcome_codes, "outcome_codes"),
        ):
            if not isinstance(values, tuple) or not all(
                isinstance(item, str) and _IDENTIFIER_RE.fullmatch(item)
                for item in values
            ):
                raise ValueError(f"{name} must be a bounded identifier tuple")
        if not isinstance(self.decision, DecisionAction):
            raise TypeError("decision must be DecisionAction")
        _digest(self.previous_event_sha256, "previous_event_sha256", nullable=True)
        _digest(self.event_sha256, "event_sha256")
        if self.event_sha256 != sha256_canonical(self.body_data()):
            raise ValueError("event_sha256 does not commit the event body")

    def body_data(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "recorded_at": self.recorded_at,
            "review_id": self.review_id,
            "round_number": self.round_number,
            "evidence_set_sha256": self.evidence_set_sha256,
            "authorization_result_sha256": self.authorization_result_sha256,
            "evidence_ids": self.evidence_ids,
            "outcome_codes": self.outcome_codes,
            "decision": self.decision,
            "previous_event_sha256": self.previous_event_sha256,
        }

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        event: RecoveryEventType,
        recorded_at: datetime,
        review_id: str,
        round_number: int,
        evidence_set_sha256: str,
        authorization_result_sha256: str | None,
        evidence_ids: tuple[str, ...],
        outcome_codes: tuple[str, ...],
        decision: DecisionAction,
        previous_event_sha256: str | None,
    ) -> RecoveryAuditEvent:
        body = {
            "sequence": sequence,
            "event": event,
            "recorded_at": recorded_at,
            "review_id": review_id,
            "round_number": round_number,
            "evidence_set_sha256": evidence_set_sha256,
            "authorization_result_sha256": authorization_result_sha256,
            "evidence_ids": evidence_ids,
            "outcome_codes": outcome_codes,
            "decision": decision,
            "previous_event_sha256": previous_event_sha256,
        }
        return cls(event_sha256=sha256_canonical(body), **body)


@dataclass(frozen=True, slots=True)
class ReviewRecoveryState:
    review_id: str
    scenario: ReplayScenario
    initial_authorization: object
    current_authorization: object
    current_evidence: SemanticEvidence | None
    gap_analysis: EvidenceGapAnalysis
    rounds_used: int
    new_evidence_items: int
    evidence_provider_calls: int
    initial_evidence_sha256: str
    current_evidence_sha256: str
    audit_events: tuple[RecoveryAuditEvent, ...]

    def __post_init__(self) -> None:
        _identifier(self.review_id, "review_id")
        if not isinstance(self.scenario, ReplayScenario):
            raise TypeError("scenario must be ReplayScenario")
        for value, name in (
            (self.initial_authorization, "initial_authorization"),
            (self.current_authorization, "current_authorization"),
        ):
            if getattr(value, "final_action", None) not in set(DecisionAction):
                raise TypeError(f"{name} must expose a DecisionAction final_action")
        if getattr(self.initial_authorization, "final_action") is not DecisionAction.REVIEW:
            raise ValueError("initial_authorization must be REVIEW")
        if self.current_evidence is not None and not isinstance(
            self.current_evidence, SemanticEvidence
        ):
            raise TypeError("current_evidence must be SemanticEvidence or None")
        if not isinstance(self.gap_analysis, EvidenceGapAnalysis):
            raise TypeError("gap_analysis must be EvidenceGapAnalysis")
        for value, name, maximum in (
            (self.rounds_used, "rounds_used", MAX_ACQUISITION_ROUNDS),
            (self.new_evidence_items, "new_evidence_items", MAX_NEW_EVIDENCE_ITEMS),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError(f"{name} is outside its fixed bound")
        if (
            isinstance(self.evidence_provider_calls, bool)
            or not isinstance(self.evidence_provider_calls, int)
            or self.evidence_provider_calls < 0
        ):
            raise ValueError("evidence_provider_calls must be non-negative")
        _digest(self.initial_evidence_sha256, "initial_evidence_sha256")
        _digest(self.current_evidence_sha256, "current_evidence_sha256")
        if not isinstance(self.audit_events, tuple) or not all(
            isinstance(item, RecoveryAuditEvent) for item in self.audit_events
        ):
            raise TypeError("audit_events must be a RecoveryAuditEvent tuple")
        previous = None
        for index, event in enumerate(self.audit_events, start=1):
            if event.sequence != index or event.previous_event_sha256 != previous:
                raise ValueError("recovery audit event chain is invalid")
            previous = event.event_sha256

    @property
    def final_action(self) -> DecisionAction:
        return getattr(self.current_authorization, "final_action")

    @property
    def resolved(self) -> bool:
        return self.final_action is not DecisionAction.REVIEW

    @property
    def budget_exhausted(self) -> bool:
        return (
            self.rounds_used >= MAX_ACQUISITION_ROUNDS
            or self.new_evidence_items >= MAX_NEW_EVIDENCE_ITEMS
        )
