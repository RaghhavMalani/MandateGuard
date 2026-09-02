"""Deterministic, evidence-complete, time-safe REVIEW recovery."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    sha256_canonical,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.catalog import CatalogSnapshot
from mandateguard.models.decision import DecisionAction, decide_deterministically
from mandateguard.models.mandate import (
    Mandate,
    SemanticConstraint,
    SemanticConstraintFamily,
)
from mandateguard.policy.tier_a import evaluate_tier_a
from mandateguard.policy.tier_b import evaluate_tier_b
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
    TrustedEvidenceSourceRegistry,
)
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    semantic_evidence_sha256,
)
from mandateguard.semantic.models import AuthorizationResult, ConstraintStatus
from mandateguard.semantic.orchestration import (
    authorize_transaction,
    finalize_authorization,
)
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier


DIAGNOSTIC_VERSION = "STRUCTURED_CONSTRAINT_FAMILY_PLANNER_V2"


def _evidence_kind(constraint: SemanticConstraint) -> EvidenceKind | None:
    family = constraint.constraint_family
    if family is SemanticConstraintFamily.PURPOSE:
        return EvidenceKind.PURPOSE
    if family is SemanticConstraintFamily.RECURRENCE:
        return EvidenceKind.RECURRENCE
    if family is SemanticConstraintFamily.EXCLUSION:
        return EvidenceKind.EXCLUSION
    return None


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
    skus: tuple[str, ...],
    current_entries: tuple[SemanticEvidenceEntry, ...],
    registry: TrustedEvidenceSourceRegistry,
    created_at: datetime,
    current_entries_complete: bool = False,
) -> EvidenceGapAnalysis:
    """Map structured REVIEW families across every line to complete manifests."""

    if getattr(authorization, "final_action", None) is not DecisionAction.REVIEW:
        return EvidenceGapAnalysis(status=GapAnalysisStatus.NONE, gaps=())
    current_ids = frozenset(entry.evidence_id for entry in current_entries)
    gaps: list[EvidenceGap] = []
    for constraint in _review_constraints(authorization, mandate):
        kind = _evidence_kind(constraint)
        if kind is None:
            continue
        for sku in tuple(dict.fromkeys(skus)):
            candidates = registry.candidates(
                merchant_id=merchant_id,
                sku=sku,
                evidence_kind=kind,
                at_time=created_at,
            )
            if current_entries_complete and candidates and all(
                set(source.manifest.record_ids).issubset(current_ids)
                for source in candidates
            ):
                continue
            gaps.append(
                EvidenceGap(
                    constraint_id=constraint.constraint_id,
                    constraint_family=constraint.constraint_family.value,
                    reason=_gap_reason(kind),
                    missing_evidence_kind=kind,
                    merchant_id=merchant_id,
                    sku=sku,
                    candidate_evidence_ids=tuple(
                        source.source_id for source in candidates
                    ),
                    diagnostic_source=DIAGNOSTIC_VERSION,
                    registry_sha256=registry.registry_sha256,
                    created_at=created_at,
                )
            )
    if not gaps:
        status = GapAnalysisStatus.NONE
    elif any(not gap.candidate_evidence_ids for gap in gaps):
        status = GapAnalysisStatus.INCOMPLETE_COVERAGE
    else:
        status = GapAnalysisStatus.RECOVERABLE
    return EvidenceGapAnalysis(status=status, gaps=tuple(gaps))


def _constraint_statuses(authorization: object, mandate: Mandate) -> tuple[str, ...]:
    semantic = getattr(authorization, "semantic_decision", None)
    if semantic is None:
        return tuple(
            f"{constraint.constraint_id}:NOT_EVALUATED"
            for constraint in mandate.payload.constraints.semantic
        )
    return tuple(
        f"{item.constraint_id}:{item.status.value}"
        for item in semantic.constraint_results
    )


def _semantic_hashes(authorization: object) -> tuple[str | None, str | None]:
    semantic = getattr(authorization, "semantic_decision", None)
    if semantic is None:
        return None, None
    return semantic.semantic_input_sha256, semantic.semantic_output_sha256


def _append_event(
    events: tuple[RecoveryAuditEvent, ...],
    *,
    event: RecoveryEventType,
    recorded_at: datetime,
    state: ReviewRecoveryState | None,
    review_id: str,
    initial_evaluated_at: datetime,
    registry_sha256: str,
    evidence_hash: str,
    authorization: object,
    round_number: int = 0,
    recovery_started_at: datetime | None = None,
    recovery_authorized_at: datetime | None = None,
    gaps: tuple[EvidenceGap, ...] = (),
    source_ids: tuple[str, ...] = (),
    source_scopes: tuple[str, ...] = (),
    manifest_versions: tuple[str, ...] = (),
    manifest_sha256s: tuple[str, ...] = (),
    expected_ids: tuple[str, ...] = (),
    expected_hashes: tuple[str, ...] = (),
    actual_ids: tuple[str, ...] = (),
    actual_hashes: tuple[str, ...] = (),
    acquisition_complete: bool | None = None,
    outcome_codes: tuple[str, ...] = (),
) -> tuple[RecoveryAuditEvent, ...]:
    mandate = state.scenario.mandate if state is not None else None
    if mandate is None:
        raise ValueError("state is required to append a recovery audit event")
    semantic_input, semantic_output = _semantic_hashes(authorization)
    added = RecoveryAuditEvent.create(
        sequence=len(events) + 1,
        event=event,
        recorded_at=recorded_at,
        review_id=review_id,
        round_number=round_number,
        initial_evaluated_at=initial_evaluated_at,
        recovery_started_at=recovery_started_at,
        recovery_authorized_at=recovery_authorized_at,
        evidence_set_sha256=evidence_hash,
        authorization_result_sha256=sha256_canonical(authorization),
        constraint_statuses=_constraint_statuses(authorization, mandate),
        gap_kinds=tuple(
            f"{gap.constraint_id}:{gap.sku}:{gap.missing_evidence_kind.value}"
            for gap in gaps
        ),
        diagnostic_version=(gaps[0].diagnostic_source if gaps else None),
        registry_sha256=registry_sha256,
        source_ids=source_ids,
        source_scopes=source_scopes,
        manifest_versions=manifest_versions,
        manifest_sha256s=manifest_sha256s,
        expected_evidence_ids=expected_ids,
        expected_evidence_hashes=expected_hashes,
        actual_evidence_ids=actual_ids,
        actual_evidence_hashes=actual_hashes,
        acquisition_complete=acquisition_complete,
        semantic_input_sha256=semantic_input,
        semantic_output_sha256=semantic_output,
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

    if not isinstance(scenario, ReplayScenario):
        raise TypeError("scenario must be ReplayScenario")
    if getattr(authorization, "final_action", None) is not DecisionAction.REVIEW:
        raise ValueError("recovery can be created only for REVIEW")
    entries = semantic_evidence.bundle.entries if semantic_evidence is not None else ()
    merchant_id = scenario.transaction.payload.merchant_id
    skus = tuple(line.sku for line in scenario.transaction.payload.lines)
    evidence_hash = evidence_set_sha256(merchant_id=merchant_id, entries=entries)
    review_id = "review:" + sha256_canonical(
        {
            "authorization": authorization,
            "evidence_set_sha256": evidence_hash,
            "merchant_id": merchant_id,
            "skus": skus,
        }
    )[:24]
    gap_analysis = detect_evidence_gaps(
        authorization=authorization,
        mandate=scenario.mandate,
        merchant_id=merchant_id,
        skus=skus,
        current_entries=entries,
        registry=registry,
        created_at=created_at,
    )
    provisional = ReviewRecoveryState(
        review_id=review_id,
        scenario=scenario,
        initial_authorization=authorization,
        current_authorization=authorization,
        current_evidence=semantic_evidence,
        gap_analysis=gap_analysis,
        rounds_used=0,
        round_in_flight=None,
        new_evidence_items=0,
        evidence_provider_calls=0,
        initial_evidence_sha256=evidence_hash,
        current_evidence_sha256=evidence_hash,
        initial_evidence_entries=entries,
        initial_evaluated_at=scenario.evaluated_at,
        recovery_started_at=None,
        recovery_authorized_at=None,
        audit_events=(),
    )
    events = _append_event(
        (),
        event=RecoveryEventType.INITIAL_REVIEW,
        recorded_at=created_at,
        state=provisional,
        review_id=review_id,
        initial_evaluated_at=scenario.evaluated_at,
        registry_sha256=registry.registry_sha256,
        evidence_hash=evidence_hash,
        authorization=authorization,
        actual_ids=tuple(entry.evidence_id for entry in entries),
        actual_hashes=tuple(sha256_canonical(entry) for entry in entries),
    )
    gap_source_ids = tuple(
        dict.fromkeys(
            source_id
            for gap in gap_analysis.gaps
            for source_id in gap.candidate_evidence_ids
        )
    )
    gap_sources = tuple(
        source
        for source_id in gap_source_ids
        if (source := registry.source(source_id)) is not None
    )
    events = _append_event(
        events,
        event=RecoveryEventType.GAP_IDENTIFIED,
        recorded_at=created_at,
        state=provisional,
        review_id=review_id,
        initial_evaluated_at=scenario.evaluated_at,
        registry_sha256=registry.registry_sha256,
        evidence_hash=evidence_hash,
        authorization=authorization,
        gaps=gap_analysis.gaps,
        source_ids=tuple(source.source_id for source in gap_sources),
        source_scopes=tuple(
            source.manifest.scope_type.value for source in gap_sources
        ),
        manifest_versions=tuple(
            source.manifest.manifest_version for source in gap_sources
        ),
        manifest_sha256s=tuple(
            source.manifest.manifest_sha256 for source in gap_sources
        ),
        outcome_codes=(gap_analysis.status.value,),
    )
    return replace(provisional, audit_events=events)


def _selected_sources(state: ReviewRecoveryState) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            source_id
            for gap in state.gap_analysis.gaps
            for source_id in gap.candidate_evidence_ids
        )
    )


def reserve_recovery_round(
    *,
    state: ReviewRecoveryState,
    registry: TrustedEvidenceSourceRegistry,
    recovery_started_at: datetime,
) -> ReviewRecoveryState:
    """Consume and record a round before any provider or authorization work."""

    if not isinstance(state, ReviewRecoveryState):
        raise TypeError("state must be ReviewRecoveryState")
    if state.final_action is not DecisionAction.REVIEW:
        raise ValueError("review has already been resolved")
    if state.round_in_flight is not None:
        raise RuntimeError("an acquisition round is already reserved")
    if state.rounds_used >= MAX_ACQUISITION_ROUNDS:
        raise RuntimeError("acquisition round budget exhausted")
    if state.new_evidence_items >= MAX_NEW_EVIDENCE_ITEMS:
        raise RuntimeError("new evidence item budget exhausted")
    if state.gap_analysis.status is not GapAnalysisStatus.RECOVERABLE:
        raise RuntimeError(state.gap_analysis.status.value)
    source_ids = _selected_sources(state)
    sources = tuple(
        source
        for source_id in source_ids
        if (source := registry.source(source_id)) is not None
    )
    round_number = state.rounds_used + 1
    reserved = replace(
        state,
        rounds_used=round_number,
        round_in_flight=round_number,
        recovery_started_at=recovery_started_at,
    )
    events = _append_event(
        state.audit_events,
        event=RecoveryEventType.ROUND_RESERVED,
        recorded_at=recovery_started_at,
        state=reserved,
        review_id=state.review_id,
        initial_evaluated_at=state.initial_evaluated_at,
        recovery_started_at=recovery_started_at,
        registry_sha256=registry.registry_sha256,
        evidence_hash=state.current_evidence_sha256,
        authorization=state.current_authorization,
        round_number=round_number,
        gaps=state.gap_analysis.gaps,
        source_ids=source_ids,
        outcome_codes=("ROUND_RESERVED",),
    )
    events = _append_event(
        events,
        event=RecoveryEventType.SOURCE_SELECTED,
        recorded_at=recovery_started_at,
        state=reserved,
        review_id=state.review_id,
        initial_evaluated_at=state.initial_evaluated_at,
        recovery_started_at=recovery_started_at,
        registry_sha256=registry.registry_sha256,
        evidence_hash=state.current_evidence_sha256,
        authorization=state.current_authorization,
        round_number=round_number,
        gaps=state.gap_analysis.gaps,
        source_ids=tuple(source.source_id for source in sources),
        source_scopes=tuple(source.manifest.scope_type.value for source in sources),
        manifest_versions=tuple(
            source.manifest.manifest_version for source in sources
        ),
        manifest_sha256s=tuple(
            source.manifest.manifest_sha256 for source in sources
        ),
        expected_ids=tuple(
            record_id for source in sources for record_id in source.manifest.record_ids
        ),
        expected_hashes=tuple(
            record_hash
            for source in sources
            for record_hash in source.manifest.record_hashes
        ),
    )
    return replace(reserved, audit_events=events)


def _fresh_scenario(
    state: ReviewRecoveryState,
    *,
    recovery_time: datetime,
    catalog_snapshot: CatalogSnapshot | None,
    nonce_state: NonceLedgerState | None,
) -> ReplayScenario:
    original = state.scenario
    catalog = catalog_snapshot if catalog_snapshot is not None else original.catalog_snapshot
    nonce = nonce_state if nonce_state is not None else original.nonce_state
    return ReplayScenario(
        mandate=original.mandate,
        transaction=original.transaction,
        catalog_snapshot=catalog,
        server_time=recovery_time,
        nonce_state=nonce,
        psp_committed_hashes=CommittedHashes(
            transaction_sha256=transaction_body_sha256(original.transaction),
            catalog_snapshot_sha256=(
                catalog_snapshot_sha256(catalog) if catalog is not None else None
            ),
        ),
        replay_seed=original.replay_seed,
        evaluated_at=recovery_time,
    )


def _deterministic_preflight(scenario: ReplayScenario) -> AuthorizationResult | None:
    tier_a_results = evaluate_tier_a(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.psp_committed_hashes,
    )
    findings = evaluate_tier_b(
        mandate=scenario.mandate, transaction=scenario.transaction
    )
    deterministic = decide_deterministically(
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        transaction_sha256=transaction_body_sha256(scenario.transaction),
        catalog_snapshot_sha256=(
            catalog_snapshot_sha256(scenario.catalog_snapshot)
            if scenario.catalog_snapshot is not None
            else None
        ),
        tier_a_results=tier_a_results,
        tier_b_findings=findings,
    )
    if deterministic.action is DecisionAction.ALLOW:
        return None
    return finalize_authorization(
        deterministic_decision=deterministic,
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        semantic_evidence=None,
        semantic_verifier=None,
    )


def _batch_actuals(batch: AcquisitionBatch) -> tuple[tuple[str, ...], tuple[str, ...]]:
    hashes: dict[str, str] = {}
    for item in batch.items:
        for evidence_id, entry_hash in zip(
            item.received_ids, item.received_hashes, strict=True
        ):
            hashes[evidence_id] = entry_hash
    ids = tuple(sorted(hashes))
    return ids, tuple(hashes[evidence_id] for evidence_id in ids)


def _finish_without_acquisition(
    state: ReviewRecoveryState,
    *,
    scenario: ReplayScenario,
    authorization: object,
    registry: TrustedEvidenceSourceRegistry,
    recovery_time: datetime,
    outcome_code: str,
) -> ReviewRecoveryState:
    events = _append_event(
        state.audit_events,
        event=RecoveryEventType.REAUTHORIZATION,
        recorded_at=recovery_time,
        state=state,
        review_id=state.review_id,
        initial_evaluated_at=state.initial_evaluated_at,
        recovery_started_at=state.recovery_started_at,
        recovery_authorized_at=recovery_time,
        registry_sha256=registry.registry_sha256,
        evidence_hash=state.current_evidence_sha256,
        authorization=authorization,
        round_number=state.rounds_used,
        outcome_codes=(outcome_code, authorization.final_action.value),
    )
    if authorization.final_action is not DecisionAction.REVIEW:
        events = _append_event(
            events,
            event=RecoveryEventType.REVIEW_RESOLVED,
            recorded_at=recovery_time,
            state=state,
            review_id=state.review_id,
            initial_evaluated_at=state.initial_evaluated_at,
            recovery_started_at=state.recovery_started_at,
            recovery_authorized_at=recovery_time,
            registry_sha256=registry.registry_sha256,
            evidence_hash=state.current_evidence_sha256,
            authorization=authorization,
            round_number=state.rounds_used,
            outcome_codes=(authorization.final_action.value,),
        )
    return replace(
        state,
        scenario=scenario,
        current_authorization=authorization,
        round_in_flight=None,
        recovery_authorized_at=recovery_time,
        audit_events=events,
    )


def complete_recovery_round(
    *,
    state: ReviewRecoveryState,
    registry: TrustedEvidenceSourceRegistry,
    semantic_verifier: SemanticVerifier,
    recovery_time: datetime,
    catalog_snapshot: CatalogSnapshot | None = None,
    nonce_state: NonceLedgerState | None = None,
) -> ReviewRecoveryState:
    """Complete an already-reserved round; every failure remains REVIEW."""

    if state.round_in_flight != state.rounds_used or state.round_in_flight is None:
        raise RuntimeError("recovery round must be reserved before acquisition")
    if not isinstance(semantic_verifier, SemanticVerifier):
        raise TypeError("semantic_verifier must be SemanticVerifier")
    scenario = _fresh_scenario(
        state,
        recovery_time=recovery_time,
        catalog_snapshot=catalog_snapshot,
        nonce_state=nonce_state,
    )
    preflight = _deterministic_preflight(scenario)
    if preflight is not None:
        return _finish_without_acquisition(
            state,
            scenario=scenario,
            authorization=preflight,
            registry=registry,
            recovery_time=recovery_time,
            outcome_code="DETERMINISTIC_PREFLIGHT_STOP",
        )

    source_event = state.audit_events[-1]
    source_ids = source_event.source_ids
    existing_entries = (
        state.current_evidence.bundle.entries
        if state.current_evidence is not None
        else ()
    )
    item_limit = MAX_NEW_EVIDENCE_ITEMS - state.new_evidence_items
    try:
        batch = registry.acquire(
            source_ids=source_ids,
            merchant_id=scenario.transaction.payload.merchant_id,
            skus=tuple(line.sku for line in scenario.transaction.payload.lines),
            existing_entries=existing_entries,
            item_limit=item_limit,
            acquired_at=recovery_time,
        )
    except Exception:
        events = _append_event(
            state.audit_events,
            event=RecoveryEventType.RECOVERY_FAILED,
            recorded_at=recovery_time,
            state=state,
            review_id=state.review_id,
            initial_evaluated_at=state.initial_evaluated_at,
            recovery_started_at=state.recovery_started_at,
            registry_sha256=registry.registry_sha256,
            evidence_hash=state.current_evidence_sha256,
            authorization=state.current_authorization,
            round_number=state.rounds_used,
            outcome_codes=(AcquisitionItemStatus.SOURCE_UNAVAILABLE.value,),
        )
        return replace(state, scenario=scenario, round_in_flight=None, audit_events=events)

    actual_ids, actual_hashes = _batch_actuals(batch)
    expected_hashes_by_id = {
        evidence_id: entry_hash
        for item in batch.items
        for evidence_id, entry_hash in zip(
            item.expected_ids, item.expected_hashes, strict=True
        )
    }
    expected_ids = tuple(sorted(expected_hashes_by_id))
    acquisition_evidence_hash = state.current_evidence_sha256
    if batch.complete:
        acquisition_evidence_hash = evidence_set_sha256(
            merchant_id=scenario.transaction.payload.merchant_id,
            entries=batch.acquired_entries,
        )
    events = _append_event(
        state.audit_events,
        event=RecoveryEventType.ACQUISITION_RESULT,
        recorded_at=recovery_time,
        state=state,
        review_id=state.review_id,
        initial_evaluated_at=state.initial_evaluated_at,
        recovery_started_at=state.recovery_started_at,
        registry_sha256=registry.registry_sha256,
        evidence_hash=acquisition_evidence_hash,
        authorization=state.current_authorization,
        round_number=state.rounds_used,
        source_ids=tuple(item.source_id for item in batch.items),
        source_scopes=tuple(
            item.source_scope.value
            for item in batch.items
            if item.source_scope is not None
        ),
        manifest_versions=tuple(
            source.manifest.manifest_version
            for item in batch.items
            if (source := registry.source(item.source_id)) is not None
        ),
        manifest_sha256s=tuple(
            item.manifest_sha256
            for item in batch.items
            if item.manifest_sha256 is not None
        ),
        expected_ids=expected_ids,
        expected_hashes=tuple(
            expected_hashes_by_id[evidence_id] for evidence_id in expected_ids
        ),
        actual_ids=actual_ids,
        actual_hashes=actual_hashes,
        acquisition_complete=batch.complete,
        outcome_codes=tuple(item.status.value for item in batch.items)
        + batch.conflict_codes,
    )
    provider_calls = state.evidence_provider_calls + batch.provider_calls
    if not batch.complete:
        return replace(
            state,
            scenario=scenario,
            round_in_flight=None,
            evidence_provider_calls=provider_calls,
            audit_events=events,
        )

    acquired_entries = batch.acquired_entries
    acquired_by_id = {entry.evidence_id: entry for entry in acquired_entries}
    initial_uncovered = tuple(
        entry.evidence_id
        for entry in state.initial_evidence_entries
        if entry.evidence_id not in acquired_by_id
        or sha256_canonical(entry) != sha256_canonical(acquired_by_id[entry.evidence_id])
    )
    if initial_uncovered:
        events = _append_event(
            events,
            event=RecoveryEventType.RECOVERY_FAILED,
            recorded_at=recovery_time,
            state=state,
            review_id=state.review_id,
            initial_evaluated_at=state.initial_evaluated_at,
            recovery_started_at=state.recovery_started_at,
            registry_sha256=registry.registry_sha256,
            evidence_hash=state.current_evidence_sha256,
            authorization=state.current_authorization,
            round_number=state.rounds_used,
            actual_ids=initial_uncovered,
            outcome_codes=(AcquisitionItemStatus.INITIAL_EVIDENCE_UNMANIFESTED.value,),
        )
        return replace(
            state,
            scenario=scenario,
            round_in_flight=None,
            evidence_provider_calls=provider_calls,
            audit_events=events,
        )

    bundle = SemanticEvidenceBundle(
        merchant_id=scenario.transaction.payload.merchant_id,
        entries=acquired_entries,
    )
    new_evidence = SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )
    new_hash = evidence_set_sha256(
        merchant_id=bundle.merchant_id, entries=bundle.entries
    )
    previous_ids = {entry.evidence_id for entry in existing_entries}
    new_item_count = len(
        {entry.evidence_id for entry in acquired_entries} - previous_ids
    )

    try:
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
    except Exception:
        events = _append_event(
            events,
            event=RecoveryEventType.RECOVERY_FAILED,
            recorded_at=recovery_time,
            state=state,
            review_id=state.review_id,
            initial_evaluated_at=state.initial_evaluated_at,
            recovery_started_at=state.recovery_started_at,
            registry_sha256=registry.registry_sha256,
            evidence_hash=new_hash,
            authorization=state.current_authorization,
            round_number=state.rounds_used,
            actual_ids=tuple(entry.evidence_id for entry in acquired_entries),
            actual_hashes=tuple(sha256_canonical(entry) for entry in acquired_entries),
            acquisition_complete=True,
            outcome_codes=(AcquisitionItemStatus.AUTHORIZATION_FAILED.value,),
        )
        return replace(
            state,
            scenario=scenario,
            current_evidence=new_evidence,
            current_evidence_sha256=new_hash,
            round_in_flight=None,
            new_evidence_items=state.new_evidence_items + new_item_count,
            evidence_provider_calls=provider_calls,
            audit_events=events,
        )

    events = _append_event(
        events,
        event=RecoveryEventType.REAUTHORIZATION,
        recorded_at=recovery_time,
        state=state,
        review_id=state.review_id,
        initial_evaluated_at=state.initial_evaluated_at,
        recovery_started_at=state.recovery_started_at,
        recovery_authorized_at=recovery_time,
        registry_sha256=registry.registry_sha256,
        evidence_hash=new_hash,
        authorization=authorization,
        round_number=state.rounds_used,
        actual_ids=tuple(entry.evidence_id for entry in bundle.entries),
        actual_hashes=tuple(sha256_canonical(entry) for entry in bundle.entries),
        acquisition_complete=True,
        outcome_codes=(authorization.final_action.value,),
    )
    if authorization.final_action is not DecisionAction.REVIEW:
        events = _append_event(
            events,
            event=RecoveryEventType.REVIEW_RESOLVED,
            recorded_at=recovery_time,
            state=state,
            review_id=state.review_id,
            initial_evaluated_at=state.initial_evaluated_at,
            recovery_started_at=state.recovery_started_at,
            recovery_authorized_at=recovery_time,
            registry_sha256=registry.registry_sha256,
            evidence_hash=new_hash,
            authorization=authorization,
            round_number=state.rounds_used,
            actual_ids=tuple(entry.evidence_id for entry in bundle.entries),
            actual_hashes=tuple(sha256_canonical(entry) for entry in bundle.entries),
            acquisition_complete=True,
            outcome_codes=(authorization.final_action.value,),
        )
    gap_analysis = detect_evidence_gaps(
        authorization=authorization,
        mandate=scenario.mandate,
        merchant_id=bundle.merchant_id,
        skus=tuple(line.sku for line in scenario.transaction.payload.lines),
        current_entries=bundle.entries,
        registry=registry,
        created_at=recovery_time,
        current_entries_complete=True,
    )
    return replace(
        state,
        scenario=scenario,
        current_authorization=authorization,
        current_evidence=new_evidence,
        gap_analysis=gap_analysis,
        round_in_flight=None,
        new_evidence_items=state.new_evidence_items + new_item_count,
        evidence_provider_calls=provider_calls,
        current_evidence_sha256=new_hash,
        recovery_authorized_at=recovery_time,
        audit_events=events,
    )


def recover_review_once(
    *,
    state: ReviewRecoveryState,
    registry: TrustedEvidenceSourceRegistry,
    semantic_verifier: SemanticVerifier,
    recorded_at: datetime,
    catalog_snapshot: CatalogSnapshot | None = None,
    nonce_state: NonceLedgerState | None = None,
) -> ReviewRecoveryState:
    """Reserve one round, then acquire and reauthorize with the supplied current time."""

    reserved = reserve_recovery_round(
        state=state,
        registry=registry,
        recovery_started_at=recorded_at,
    )
    return complete_recovery_round(
        state=reserved,
        registry=registry,
        semantic_verifier=semantic_verifier,
        recovery_time=recorded_at,
        catalog_snapshot=catalog_snapshot,
        nonce_state=nonce_state,
    )
