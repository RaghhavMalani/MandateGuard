"""Immutable value objects for evidence-complete REVIEW recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from types import MappingProxyType
from typing import Mapping

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


def _aware(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        suffix = " or None" if nullable else ""
        raise ValueError(f"{name} must be timezone-aware{suffix}")


def _digest(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _identifier_tuple(value: object, name: str) -> None:
    if not isinstance(value, tuple) or not all(
        isinstance(item, str) and _IDENTIFIER_RE.fullmatch(item) for item in value
    ):
        raise ValueError(f"{name} must be a bounded identifier tuple")


class EvidenceKind(str, Enum):
    PURPOSE = "PURPOSE_EVIDENCE"
    RECURRENCE = "RECURRENCE_TERMS"
    EXCLUSION = "EXCLUSION_EVIDENCE"


class EvidenceScope(str, Enum):
    MERCHANT_GLOBAL = "MERCHANT_GLOBAL"
    SKU_SPECIFIC = "SKU_SPECIFIC"


#: Reserved claim value meaning "this authoritative record asserts nothing here".
#: It satisfies the claim-metadata requirement below and never conflicts with an
#: assertion, because declared non-assertion is not a competing truth claim.
CLAIM_VALUE_UNESTABLISHED = "UNESTABLISHED"

#: Evidence kinds whose records assert a conflict-capable fact, and the claim
#: namespace each such record must annotate. `RECURRENCE` records assert the
#: billing model; `EXCLUSION` records assert presence or absence of a prohibited
#: characteristic. Two simultaneously applicable records of these kinds can
#: contradict each other, so the server must be able to compare them
#: deterministically. `PURPOSE` is deliberately absent: purpose evidence only
#: adds support for a declared use and never asserts the negation that would
#: make another record's support false, so a missing purpose claim cannot hide a
#: contradiction. Purpose records may still annotate `purpose.*` claims, and
#: those claims are compared for conflict when present.
REQUIRED_CLAIM_NAMESPACES: Mapping[EvidenceKind, str] = MappingProxyType(
    {
        EvidenceKind.RECURRENCE: "billing",
        EvidenceKind.EXCLUSION: "content",
    }
)

CONFLICT_SIMULTANEOUS_AUTHORITY = "SIMULTANEOUS_AUTHORITY_CONFLICT"
CONFLICT_DUPLICATE_ID_HASH = "DUPLICATE_ID_HASH_CONFLICT"
CONFLICT_CLAIM_METADATA_INCOMPLETE = "CLAIM_METADATA_INCOMPLETE"
AUTHORITY_CONFLICT_UNRESOLVABLE_CODES = frozenset(
    {
        CONFLICT_SIMULTANEOUS_AUTHORITY,
        CONFLICT_DUPLICATE_ID_HASH,
        CONFLICT_CLAIM_METADATA_INCOMPLETE,
    }
)


class GapAnalysisStatus(str, Enum):
    RECOVERABLE = "RECOVERABLE_GAP"
    NONE = "NO_RECOVERABLE_GAP"
    INCOMPLETE_COVERAGE = "INCOMPLETE_SOURCE_COVERAGE"


class AcquisitionItemStatus(str, Enum):
    ACQUIRED = "SOURCE_COMPLETE"
    NO_RECORD = "SOURCE_RETURNED_NO_RECORD"
    WRONG_BINDING = "EVIDENCE_BINDING_REJECTED"
    DUPLICATE = "DUPLICATE_EVIDENCE_REJECTED"
    TAMPERED = "MANIFEST_HASH_MISMATCH"
    SOURCE_INCOMPLETE = "SOURCE_INCOMPLETE"
    BUDGET_INSUFFICIENT = "EVIDENCE_BUDGET_INSUFFICIENT"
    SOURCE_NOT_EFFECTIVE = "SOURCE_NOT_EFFECTIVE"
    SOURCE_EXPIRED = "SOURCE_EXPIRED"
    SOURCE_SUPERSEDED = "SOURCE_SUPERSEDED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    CONFLICT = "REVIEW_ON_CONFLICT"
    INITIAL_EVIDENCE_UNMANIFESTED = "INITIAL_EVIDENCE_UNMANIFESTED"
    AUTHORIZATION_FAILED = "REAUTHORIZATION_FAILED"


class RecoveryEventType(str, Enum):
    INITIAL_REVIEW = "INITIAL_REVIEW"
    GAP_IDENTIFIED = "GAP_IDENTIFIED"
    ROUND_RESERVED = "ROUND_RESERVED"
    SOURCE_SELECTED = "SOURCE_SELECTED"
    ACQUISITION_RESULT = "ACQUISITION_RESULT"
    REAUTHORIZATION = "REAUTHORIZATION"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    EXECUTION_LINKED = "EXECUTION_LINKED"

    # Source-compatible aliases for callers from the first Resolve prototype.
    REVIEW_CREATED = "INITIAL_REVIEW"
    EVIDENCE_GAP_IDENTIFIED = "GAP_IDENTIFIED"
    EVIDENCE_ACQUISITION_STARTED = "SOURCE_SELECTED"
    EVIDENCE_ACQUIRED = "ACQUISITION_RESULT"
    AUTHORIZATION_REEVALUATED = "REAUTHORIZATION"


@dataclass(frozen=True, slots=True)
class TrustedEvidenceClaim:
    """One server-normalized fact used only for deterministic conflict checks."""

    claim_id: str
    claim_value: str

    def __post_init__(self) -> None:
        _identifier(self.claim_id, "claim_id")
        _identifier(self.claim_value, "claim_value")


@dataclass(frozen=True, slots=True)
class TrustedEvidenceRecord:
    """Manifest metadata for one exact evidence record."""

    evidence_id: str
    expected_entry_sha256: str
    effective_at: datetime
    expires_at: datetime | None = None
    supersedes_evidence_id: str | None = None
    claims: tuple[TrustedEvidenceClaim, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.evidence_id, "evidence_id")
        _digest(self.expected_entry_sha256, "expected_entry_sha256")
        _aware(self.effective_at, "effective_at")
        _aware(self.expires_at, "expires_at", nullable=True)
        if self.expires_at is not None and self.expires_at <= self.effective_at:
            raise ValueError("record expires_at must be after effective_at")
        if self.supersedes_evidence_id is not None:
            _identifier(self.supersedes_evidence_id, "supersedes_evidence_id")
            if self.supersedes_evidence_id == self.evidence_id:
                raise ValueError("a record cannot supersede itself")
        if not isinstance(self.claims, tuple) or not all(
            isinstance(claim, TrustedEvidenceClaim) for claim in self.claims
        ):
            raise TypeError("claims must be a TrustedEvidenceClaim tuple")
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("record claim IDs must be unique")

    def active_at(self, at_time: datetime) -> bool:
        _aware(at_time, "at_time")
        return self.effective_at <= at_time and (
            self.expires_at is None or at_time < self.expires_at
        )

    def declares_claim_namespace(self, namespace: str) -> bool:
        """Report whether this record carries normalized metadata for a namespace."""

        prefix = f"{namespace}."
        return any(claim.claim_id.startswith(prefix) for claim in self.claims)


@dataclass(frozen=True, slots=True)
class TrustedEvidenceManifest:
    """Complete authoritative record declaration for one source and scope."""

    manifest_id: str
    source_id: str
    merchant_id: str
    scope_type: EvidenceScope
    sku: str | None
    evidence_kinds: tuple[EvidenceKind, ...]
    manifest_version: str
    effective_at: datetime
    expires_at: datetime | None
    records: tuple[TrustedEvidenceRecord, ...]
    supersedes_manifest_id: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.manifest_id, "manifest_id"),
            (self.source_id, "source_id"),
            (self.merchant_id, "merchant_id"),
            (self.manifest_version, "manifest_version"),
        ):
            _identifier(value, name)
        if not isinstance(self.scope_type, EvidenceScope):
            raise TypeError("scope_type must be EvidenceScope")
        if self.scope_type is EvidenceScope.SKU_SPECIFIC:
            _identifier(self.sku, "sku")
        elif self.sku is not None:
            raise ValueError("MERCHANT_GLOBAL scope must declare sku=None")
        if (
            not isinstance(self.evidence_kinds, tuple)
            or not self.evidence_kinds
            or not all(isinstance(kind, EvidenceKind) for kind in self.evidence_kinds)
        ):
            raise ValueError("evidence_kinds must be a non-empty EvidenceKind tuple")
        if len(self.evidence_kinds) != len(set(self.evidence_kinds)):
            raise ValueError("evidence_kinds must be unique")
        _aware(self.effective_at, "effective_at")
        _aware(self.expires_at, "expires_at", nullable=True)
        if self.expires_at is not None and self.expires_at <= self.effective_at:
            raise ValueError("manifest expires_at must be after effective_at")
        if not isinstance(self.records, tuple) or not self.records or not all(
            isinstance(record, TrustedEvidenceRecord) for record in self.records
        ):
            raise TypeError("records must be a non-empty TrustedEvidenceRecord tuple")
        record_ids = self.record_ids
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("manifest record IDs must be unique")
        if self.supersedes_manifest_id is not None:
            _identifier(self.supersedes_manifest_id, "supersedes_manifest_id")
            if self.supersedes_manifest_id == self.manifest_id:
                raise ValueError("a manifest cannot supersede itself")

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.evidence_id for record in self.records)

    @property
    def record_hashes(self) -> tuple[str, ...]:
        return tuple(record.expected_entry_sha256 for record in self.records)

    @property
    def manifest_sha256(self) -> str:
        return sha256_canonical(self)

    def active_at(self, at_time: datetime) -> bool:
        _aware(at_time, "at_time")
        return self.effective_at <= at_time and (
            self.expires_at is None or at_time < self.expires_at
        )


@dataclass(frozen=True, slots=True)
class TrustedEvidenceSource:
    """Server-configured provider label plus its immutable complete manifest."""

    source_id: str
    display_name: str
    manifest: TrustedEvidenceManifest

    def __post_init__(self) -> None:
        _identifier(self.source_id, "source_id")
        _bounded_text(self.display_name, "display_name", 160)
        if not isinstance(self.manifest, TrustedEvidenceManifest):
            raise TypeError("manifest must be TrustedEvidenceManifest")
        if self.source_id != self.manifest.source_id:
            raise ValueError("source_id must match manifest.source_id")

    @property
    def merchant_id(self) -> str:
        return self.manifest.merchant_id

    @property
    def sku(self) -> str | None:
        return self.manifest.sku

    @property
    def evidence_kinds(self) -> tuple[EvidenceKind, ...]:
        return self.manifest.evidence_kinds


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
    registry_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.constraint_id, "constraint_id")
        _identifier(self.constraint_family, "constraint_family")
        _bounded_text(self.reason, "reason")
        if not isinstance(self.missing_evidence_kind, EvidenceKind):
            raise TypeError("missing_evidence_kind must be EvidenceKind")
        _identifier(self.merchant_id, "merchant_id")
        _identifier(self.sku, "sku")
        _identifier_tuple(self.candidate_evidence_ids, "candidate_evidence_ids")
        if len(self.candidate_evidence_ids) != len(set(self.candidate_evidence_ids)):
            raise ValueError("candidate_evidence_ids must be unique")
        _identifier(self.diagnostic_source, "diagnostic_source")
        _digest(self.registry_sha256, "registry_sha256")
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
        if self.status is GapAnalysisStatus.RECOVERABLE and (
            not self.gaps or any(not gap.candidate_evidence_ids for gap in self.gaps)
        ):
            raise ValueError("RECOVERABLE_GAP requires source coverage for every gap")
        if self.status is GapAnalysisStatus.NONE and self.gaps:
            raise ValueError("NO_RECOVERABLE_GAP cannot contain gaps")
        if self.status is GapAnalysisStatus.INCOMPLETE_COVERAGE and (
            not self.gaps or all(gap.candidate_evidence_ids for gap in self.gaps)
        ):
            raise ValueError("INCOMPLETE_SOURCE_COVERAGE requires an uncovered gap")


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
    """Hash-linked recovery provenance sufficient for offline reconstruction."""

    sequence: int
    event: RecoveryEventType
    recorded_at: datetime
    review_id: str
    round_number: int
    initial_evaluated_at: datetime
    recovery_started_at: datetime | None
    recovery_authorized_at: datetime | None
    mandate_payload_sha256: str
    transaction_body_sha256: str
    decision_nonce: str | None
    execution_request_sha256: str | None
    execution_receipt_id: str | None
    evidence_set_sha256: str
    authorization_result_sha256: str | None
    constraint_statuses: tuple[str, ...]
    gap_kinds: tuple[str, ...]
    diagnostic_version: str | None
    registry_sha256: str
    source_ids: tuple[str, ...]
    source_scopes: tuple[str, ...]
    manifest_versions: tuple[str, ...]
    manifest_sha256s: tuple[str, ...]
    expected_evidence_ids: tuple[str, ...]
    expected_evidence_hashes: tuple[str, ...]
    actual_evidence_ids: tuple[str, ...]
    actual_evidence_hashes: tuple[str, ...]
    acquisition_complete: bool | None
    semantic_input_sha256: str | None
    semantic_output_sha256: str | None
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
        _aware(self.initial_evaluated_at, "initial_evaluated_at")
        _aware(self.recovery_started_at, "recovery_started_at", nullable=True)
        _aware(self.recovery_authorized_at, "recovery_authorized_at", nullable=True)
        _identifier(self.review_id, "review_id")
        if isinstance(self.round_number, bool) or not isinstance(self.round_number, int) or self.round_number < 0:
            raise ValueError("round_number must be non-negative")
        _digest(self.mandate_payload_sha256, "mandate_payload_sha256")
        _digest(self.transaction_body_sha256, "transaction_body_sha256")
        _digest(
            self.execution_request_sha256, "execution_request_sha256", nullable=True
        )
        for value, name in (
            (self.decision_nonce, "decision_nonce"),
            (self.execution_receipt_id, "execution_receipt_id"),
        ):
            if value is not None:
                _identifier(value, name)
        _digest(self.evidence_set_sha256, "evidence_set_sha256")
        _digest(self.authorization_result_sha256, "authorization_result_sha256", nullable=True)
        _digest(self.registry_sha256, "registry_sha256")
        for values, name in (
            (self.constraint_statuses, "constraint_statuses"),
            (self.gap_kinds, "gap_kinds"),
            (self.source_ids, "source_ids"),
            (self.source_scopes, "source_scopes"),
            (self.manifest_versions, "manifest_versions"),
            (self.expected_evidence_ids, "expected_evidence_ids"),
            (self.actual_evidence_ids, "actual_evidence_ids"),
            (self.outcome_codes, "outcome_codes"),
        ):
            _identifier_tuple(values, name)
        for values, name in (
            (self.manifest_sha256s, "manifest_sha256s"),
            (self.expected_evidence_hashes, "expected_evidence_hashes"),
            (self.actual_evidence_hashes, "actual_evidence_hashes"),
        ):
            if not isinstance(values, tuple):
                raise ValueError(f"{name} must be a digest tuple")
            for value in values:
                _digest(value, name)
        if self.diagnostic_version is not None:
            _identifier(self.diagnostic_version, "diagnostic_version")
        if self.acquisition_complete is not None and not isinstance(
            self.acquisition_complete, bool
        ):
            raise TypeError("acquisition_complete must be bool or None")
        _digest(self.semantic_input_sha256, "semantic_input_sha256", nullable=True)
        _digest(self.semantic_output_sha256, "semantic_output_sha256", nullable=True)
        if not isinstance(self.decision, DecisionAction):
            raise TypeError("decision must be DecisionAction")
        _digest(self.previous_event_sha256, "previous_event_sha256", nullable=True)
        _digest(self.event_sha256, "event_sha256")
        if self.event_sha256 != sha256_canonical(self.body_data()):
            raise ValueError("event_sha256 does not commit the event body")

    def body_data(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "event_sha256"
        }

    @classmethod
    def create(cls, **body: object) -> RecoveryAuditEvent:
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
    round_in_flight: int | None
    new_evidence_items: int
    evidence_provider_calls: int
    initial_evidence_sha256: str
    current_evidence_sha256: str
    initial_evidence_entries: tuple[SemanticEvidenceEntry, ...]
    initial_evaluated_at: datetime
    recovery_started_at: datetime | None
    recovery_authorized_at: datetime | None
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
        if self.round_in_flight is not None and self.round_in_flight != self.rounds_used:
            raise ValueError("round_in_flight must identify the already-reserved round")
        if (
            isinstance(self.evidence_provider_calls, bool)
            or not isinstance(self.evidence_provider_calls, int)
            or self.evidence_provider_calls < 0
        ):
            raise ValueError("evidence_provider_calls must be non-negative")
        _digest(self.initial_evidence_sha256, "initial_evidence_sha256")
        _digest(self.current_evidence_sha256, "current_evidence_sha256")
        if not isinstance(self.initial_evidence_entries, tuple) or not all(
            isinstance(entry, SemanticEvidenceEntry)
            for entry in self.initial_evidence_entries
        ):
            raise TypeError("initial_evidence_entries must be an evidence tuple")
        _aware(self.initial_evaluated_at, "initial_evaluated_at")
        _aware(self.recovery_started_at, "recovery_started_at", nullable=True)
        _aware(self.recovery_authorized_at, "recovery_authorized_at", nullable=True)
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
