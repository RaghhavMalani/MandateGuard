"""Deterministic gap planning and one-round-at-a-time REVIEW recovery."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from mandateguard.core.hashing import sha256_canonical
from mandateguard.models.decision import DecisionAction
from mandateguard.models.mandate import Mandate, SemanticConstraint
from mandateguard.recovery.models import (
    MAX_ACQUISITION_ROUNDS,
    MAX_NEW_EVIDENCE_ITEMS,
    AcquisitionItemStatus,
    EvidenceGap,
    EvidenceGapAnalysis,
    EvidenceKind,
    GapAnalysisStatus,
    RecoveryAuditEvent,
    RecoveryEventType,
    ReviewRecoveryState,
    evidence_set_sha256,
)
from mandateguard.recovery.registry import (
    AcquisitionBatch,
    AcquiredEvidenceItem,
    TrustedEvidenceSourceRegistry,
)
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceAcquisitionError,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    semantic_evidence_sha256,
)
from mandateguard.semantic.models import ConstraintStatus
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier


def _evidence_kind(constraint: SemanticConstraint) -> EvidenceKind | None:
    if constraint.kind == "purpose":
        return EvidenceKind.PURPOSE
    if constraint.kind != "exclusion":
        return None
    text = constraint.text.casefold()
    if any(token in text for token in ("subscription", "recurr", "renew")):
        return EvidenceKind.RECURRENCE
    return EvidenceKind.EXCLUSION


def _gap_reason(kind: EvidenceKind) -> str:
    if kind is EvidenceKind.PURPOSE:
        return "Product purpose could not be established from the current trusted evidence."
    if kind is EvidenceKind.RECURRENCE:
        return "Recurring terms could not be verified."
    return "The excluded characteristic could not be verified from trusted evidence."


def _review_constraints(
    authorization: object, mandate: Mandate
) -> tuple[SemanticConstraint, ...]:
    semantic = getattr(authorization, "semantic_decision", None)
    if semantic is None:
        deterministic = getattr(authorization, "deterministic_decision", None)
        if getattr(deterministic, "action", None) is not DecisionAction.ALLOW:
            return ()
        if not getattr(authorization, "semantic_constraints_present", False):
            return ()
        return mandate.payload.constraints.semantic
    abstained = {
        item.constraint_id
        for item in semantic.constraint_results
        if item.status is ConstraintStatus.ABSTAIN
    }
    return tuple(
        constraint
        for constraint in mandate.payload.constraints.semantic
        if constraint.constraint_id in abstained
    )


def detect_evidence_gaps(
    *,
    authorization: object,
    mandate: Mandate,
    merchant_id: str,
    sku: str,
    current_entries: tuple[SemanticEvidenceEntry, ...],
    registry: TrustedEvidenceSourceRegistry,
    created_at: datetime,
) -> EvidenceGapAnalysis:
    """Map REVIEW statuses to registered sources without trusting model prose."""

    if getattr(authorization, "final_action", None) is not DecisionAction.REVIEW:
        return EvidenceGapAnalysis(status=GapAnalysisStatus.NONE, gaps=())
    current_ids = frozenset(entry.evidence_id for entry in current_entries)
    gaps: list[EvidenceGap] = []
    for constraint in _review_constraints(authorization, mandate):
        kind = _evidence_kind(constraint)
        if kind is None:
            continue
        candidates = registry.candidates(
            merchant_id=merchant_id,
            sku=sku,
            evidence_kind=kind,
            excluded_evidence_ids=current_ids,
        )
        if not candidates:
            continue
        gaps.append(
            EvidenceGap(
                constraint_id=constraint.constraint_id,
                constraint_family=constraint.kind,
                reason=_gap_reason(kind),
                missing_evidence_kind=kind,
                merchant_id=merchant_id,
                sku=sku,
                candidate_evidence_ids=tuple(
                    source.source_id for source in candidates
                ),
                diagnostic_source="DETERMINISTIC_CONSTRAINT_FAMILY_PLANNER_V1",
                created_at=created_at,
            )
        )
    return EvidenceGapAnalysis(
        status=(GapAnalysisStatus.RECOVERABLE if gaps else GapAnalysisStatus.NONE),
        gaps=tuple(gaps),
    )


def _append_event(
    events: tuple[RecoveryAuditEvent, ...],
    *,
    event: RecoveryEventType,
    recorded_at: datetime,
    review_id: str,
    round_number: int,
    evidence_hash: str,
    authorization: object,
    evidence_ids: tuple[str, ...] = (),
    outcome_codes: tuple[str, ...] = (),
) -> tuple[RecoveryAuditEvent, ...]:
    authorization_hash = sha256_canonical(authorization)
    added = RecoveryAuditEvent.create(
        sequence=len(events) + 1,
        event=event,
        recorded_at=recorded_at,
        review_id=review_id,
        round_number=round_number,
        evidence_set_sha256=evidence_hash,
        authorization_result_sha256=authorization_hash,
        evidence_ids=evidence_ids,
        outcome_codes=outcome_codes,
        decision=getattr(authorization, "final_action"),
        previous_event_sha256=events[-1].event_sha256 if events else None,
    )
    return events + (added,)


def create_review_recovery(
    *,
    scenario: object,
    authorization: object,
    semantic_evidence: SemanticEvidence | None,
    registry: TrustedEvidenceSourceRegistry,
    created_at: datetime,
) -> ReviewRecoveryState:
    """Create immutable recovery state around an existing REVIEW result."""

    from mandateguard.replay.scenario import ReplayScenario

    if not isinstance(scenario, ReplayScenario):
        raise TypeError("scenario must be ReplayScenario")
    if getattr(authorization, "final_action", None) is not DecisionAction.REVIEW:
        raise ValueError("recovery can be created only for REVIEW")
    entries = semantic_evidence.bundle.entries if semantic_evidence is not None else ()
    merchant_id = scenario.transaction.payload.merchant_id
    sku = scenario.transaction.payload.lines[0].sku
    evidence_hash = evidence_set_sha256(merchant_id=merchant_id, entries=entries)
    review_id = "review:" + sha256_canonical(
        {
            "authorization": authorization,
            "evidence_set_sha256": evidence_hash,
            "merchant_id": merchant_id,
            "sku": sku,
        }
    )[:24]
    gap_analysis = detect_evidence_gaps(
        authorization=authorization,
        mandate=scenario.mandate,
        merchant_id=merchant_id,
        sku=sku,
        current_entries=entries,
        registry=registry,
        created_at=created_at,
    )
    events: tuple[RecoveryAuditEvent, ...] = ()
    events = _append_event(
        events,
        event=RecoveryEventType.REVIEW_CREATED,
        recorded_at=created_at,
        review_id=review_id,
        round_number=0,
        evidence_hash=evidence_hash,
        authorization=authorization,
        evidence_ids=tuple(entry.evidence_id for entry in entries),
    )
    events = _append_event(
        events,
        event=RecoveryEventType.EVIDENCE_GAP_IDENTIFIED,
        recorded_at=created_at,
        review_id=review_id,
        round_number=0,
        evidence_hash=evidence_hash,
        authorization=authorization,
        evidence_ids=tuple(
            dict.fromkeys(
                candidate
                for gap in gap_analysis.gaps
                for candidate in gap.candidate_evidence_ids
            )
        ),
        outcome_codes=(gap_analysis.status.value,),
    )
    return ReviewRecoveryState(
        review_id=review_id,
        scenario=scenario,
        initial_authorization=authorization,
        current_authorization=authorization,
        current_evidence=semantic_evidence,
        gap_analysis=gap_analysis,
        rounds_used=0,
        new_evidence_items=0,
        evidence_provider_calls=0,
        initial_evidence_sha256=evidence_hash,
        current_evidence_sha256=evidence_hash,
        audit_events=events,
    )


def _safe_acquire(
    *,
    registry: TrustedEvidenceSourceRegistry,
    source_ids: tuple[str, ...],
    merchant_id: str,
    sku: str,
    existing_entries: tuple[SemanticEvidenceEntry, ...],
    item_limit: int,
) -> AcquisitionBatch:
    try:
        return registry.acquire(
            source_ids=source_ids,
            merchant_id=merchant_id,
            sku=sku,
            existing_entries=existing_entries,
            item_limit=item_limit,
        )
    except SemanticEvidenceAcquisitionError:
        return AcquisitionBatch(
            items=tuple(
                AcquiredEvidenceItem(
                    source_id=source_id,
                    status=AcquisitionItemStatus.NO_RECORD,
                    entry=None,
                )
                for source_id in source_ids[:item_limit]
            ),
            provider_calls=1,
        )


def recover_review_once(
    *,
    state: ReviewRecoveryState,
    registry: TrustedEvidenceSourceRegistry,
    semantic_verifier: SemanticVerifier,
    recorded_at: datetime,
) -> ReviewRecoveryState:
    """Acquire at most one bounded round, then run full authorization from scratch."""

    if not isinstance(state, ReviewRecoveryState):
        raise TypeError("state must be ReviewRecoveryState")
    if state.final_action is not DecisionAction.REVIEW:
        raise ValueError("review has already been resolved")
    if state.rounds_used >= MAX_ACQUISITION_ROUNDS:
        raise RuntimeError("acquisition round budget exhausted")
    if state.new_evidence_items >= MAX_NEW_EVIDENCE_ITEMS:
        raise RuntimeError("new evidence item budget exhausted")
    if state.gap_analysis.status is GapAnalysisStatus.NONE:
        raise RuntimeError("NO_RECOVERABLE_GAP")
    if not isinstance(semantic_verifier, SemanticVerifier):
        raise TypeError("semantic_verifier must be SemanticVerifier")

    source_ids = tuple(
        dict.fromkeys(
            candidate
            for gap in state.gap_analysis.gaps
            for candidate in gap.candidate_evidence_ids
        )
    )
    item_limit = MAX_NEW_EVIDENCE_ITEMS - state.new_evidence_items
    round_number = state.rounds_used + 1
    existing_entries = (
        state.current_evidence.bundle.entries
        if state.current_evidence is not None
        else ()
    )
    events = _append_event(
        state.audit_events,
        event=RecoveryEventType.EVIDENCE_ACQUISITION_STARTED,
        recorded_at=recorded_at,
        review_id=state.review_id,
        round_number=round_number,
        evidence_hash=state.current_evidence_sha256,
        authorization=state.current_authorization,
        evidence_ids=source_ids[:item_limit],
    )
    merchant_id = state.scenario.transaction.payload.merchant_id
    sku = state.scenario.transaction.payload.lines[0].sku
    batch = _safe_acquire(
        registry=registry,
        source_ids=source_ids,
        merchant_id=merchant_id,
        sku=sku,
        existing_entries=existing_entries,
        item_limit=item_limit,
    )
    acquired_entries = batch.acquired_entries
    events = _append_event(
        events,
        event=RecoveryEventType.EVIDENCE_ACQUIRED,
        recorded_at=recorded_at,
        review_id=state.review_id,
        round_number=round_number,
        evidence_hash=state.current_evidence_sha256,
        authorization=state.current_authorization,
        evidence_ids=tuple(entry.evidence_id for entry in acquired_entries),
        outcome_codes=tuple(item.status.value for item in batch.items),
    )
    if not acquired_entries:
        return replace(
            state,
            rounds_used=round_number,
            evidence_provider_calls=state.evidence_provider_calls + batch.provider_calls,
            audit_events=events,
        )

    combined_by_id = {entry.evidence_id: entry for entry in existing_entries}
    combined_by_id.update(
        {entry.evidence_id: entry for entry in acquired_entries}
    )
    combined = tuple(combined_by_id.values())
    if len(combined) - len(existing_entries) > item_limit:
        raise RuntimeError("new evidence item budget would be exceeded")
    bundle = SemanticEvidenceBundle(merchant_id=merchant_id, entries=combined)
    new_evidence = SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )
    new_hash = evidence_set_sha256(merchant_id=merchant_id, entries=bundle.entries)

    scenario = state.scenario
    authorization = authorize_transaction(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.psp_committed_hashes,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        semantic_evidence=new_evidence,
        semantic_verifier=semantic_verifier,
        semantic_mode=SemanticMode.LIVE,
    )
    events = _append_event(
        events,
        event=RecoveryEventType.AUTHORIZATION_REEVALUATED,
        recorded_at=recorded_at,
        review_id=state.review_id,
        round_number=round_number,
        evidence_hash=new_hash,
        authorization=authorization,
        evidence_ids=tuple(entry.evidence_id for entry in bundle.entries),
    )
    if authorization.final_action is not DecisionAction.REVIEW:
        events = _append_event(
            events,
            event=RecoveryEventType.REVIEW_RESOLVED,
            recorded_at=recorded_at,
            review_id=state.review_id,
            round_number=round_number,
            evidence_hash=new_hash,
            authorization=authorization,
            evidence_ids=tuple(entry.evidence_id for entry in acquired_entries),
            outcome_codes=(authorization.final_action.value,),
        )
    gap_analysis = detect_evidence_gaps(
        authorization=authorization,
        mandate=scenario.mandate,
        merchant_id=merchant_id,
        sku=sku,
        current_entries=bundle.entries,
        registry=registry,
        created_at=recorded_at,
    )
    return ReviewRecoveryState(
        review_id=state.review_id,
        scenario=scenario,
        initial_authorization=state.initial_authorization,
        current_authorization=authorization,
        current_evidence=new_evidence,
        gap_analysis=gap_analysis,
        rounds_used=round_number,
        new_evidence_items=state.new_evidence_items + len(acquired_entries),
        evidence_provider_calls=state.evidence_provider_calls + batch.provider_calls,
        initial_evidence_sha256=state.initial_evidence_sha256,
        current_evidence_sha256=new_hash,
        audit_events=events,
    )
