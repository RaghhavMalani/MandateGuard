from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import pytest

from mandateguard.core.hashing import sha256_canonical, transaction_body_sha256
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.models.decision import DecisionAction
from mandateguard.models.transaction import Transaction, TransactionLine
from mandateguard.product.service import (
    RECOVERY_AUDIT_UNAVAILABLE,
    CommerceLabService,
    DEMO_PRESETS,
)
from mandateguard.recovery import (
    AcquisitionItemStatus,
    CLAIM_VALUE_UNESTABLISHED,
    CONFLICT_CLAIM_METADATA_INCOMPLETE,
    EvidenceKind,
    EvidenceScope,
    GapAnalysisStatus,
    OBSERVED_COUNTER_NAMES,
    RecoveryAuditStoreError,
    SQLiteRecoveryAuditStore,
    TrustedEvidenceClaim,
    TrustedEvidenceManifest,
    TrustedEvidenceRecord,
    TrustedEvidenceSource,
    TrustedEvidenceSourceRegistry,
    create_review_recovery,
    detect_evidence_gaps,
    recover_review_once,
)
from mandateguard.semantic import SemanticVerifier
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    SemanticEvidenceEntry,
    SemanticEvidenceProviderRegistry,
    semantic_evidence_sha256,
)


T0 = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
PRESETS = {item["id"]: item for item in DEMO_PRESETS}
_UNANNOTATED_DEFAULT_CLAIMS = (
    TrustedEvidenceClaim("billing.model", CLAIM_VALUE_UNESTABLISHED),
    TrustedEvidenceClaim("content.class", CLAIM_VALUE_UNESTABLISHED),
)


def _annotated(*explicit: TrustedEvidenceClaim) -> tuple[TrustedEvidenceClaim, ...]:
    """Complete a record's claim metadata with explicit non-assertions."""

    covered = {claim.claim_id.split(".", 1)[0] for claim in explicit}
    return explicit + tuple(
        claim
        for claim in _UNANNOTATED_DEFAULT_CLAIMS
        if claim.claim_id.split(".", 1)[0] not in covered
    )


@dataclass
class _Provider:
    entries: tuple[SemanticEvidenceEntry, ...]
    calls: int = 0

    def fetch_semantic_evidence(self, *, merchant_id: str) -> SemanticEvidenceBundle:
        self.calls += 1
        return SemanticEvidenceBundle(merchant_id=merchant_id, entries=self.entries)


class _FailingProvider:
    def fetch_semantic_evidence(self, *, merchant_id: str) -> SemanticEvidenceBundle:
        raise RuntimeError("injected provider failure")


class _FailingCache:
    def get(self, request: object) -> object:
        raise RuntimeError("injected cache failure")

    def put(self, request: object, record: object) -> None:
        raise RuntimeError("injected cache failure")


@dataclass
class _ForbiddenSemanticModel:
    model_id: str = "must-not-evaluate-authority-conflict"
    calls: int = 0

    def evaluate(self, request: object) -> object:
        self.calls += 1
        raise AssertionError("semantic model must not resolve an authority conflict")


@dataclass
class _MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def _entry(
    evidence_id: str,
    *,
    merchant_id: str = "merchant-lumen",
    sku: str | None = "aurora-focus-lamp",
    text: str | None = None,
) -> SemanticEvidenceEntry:
    return SemanticEvidenceEntry(
        evidence_id=evidence_id,
        merchant_id=merchant_id,
        sku=sku,
        source_kind="product_terms",
        text=text or f"Authoritative terms for {evidence_id} establish this fact.",
    )


def _source(
    source_id: str,
    entries: tuple[SemanticEvidenceEntry, ...],
    *,
    scope: EvidenceScope = EvidenceScope.SKU_SPECIFIC,
    sku: str | None = "aurora-focus-lamp",
    kinds: tuple[EvidenceKind, ...] = (
        EvidenceKind.PURPOSE,
        EvidenceKind.RECURRENCE,
        EvidenceKind.EXCLUSION,
    ),
    claims: tuple[tuple[str, str], ...] = (),
    supersedes: dict[str, str] | None = None,
    expected_hashes: dict[str, str] | None = None,
    manifest_id: str | None = None,
    supersedes_manifest_id: str | None = None,
) -> TrustedEvidenceSource:
    supersedes = supersedes or {}
    expected_hashes = expected_hashes or {}
    normalized_claims = tuple(
        TrustedEvidenceClaim(claim_id, claim_value)
        for claim_id, claim_value in claims
    )
    records = tuple(
        TrustedEvidenceRecord(
            evidence_id=entry.evidence_id,
            expected_entry_sha256=expected_hashes.get(
                entry.evidence_id, sha256_canonical(entry)
            ),
            effective_at=T0 - timedelta(days=1),
            supersedes_evidence_id=supersedes.get(entry.evidence_id),
            # Records of conflict-capable kinds must be annotated, so records
            # without an explicit claim declare an explicit non-assertion.
            claims=(
                _annotated(*normalized_claims)
                if index == len(entries) - 1 and normalized_claims
                else _UNANNOTATED_DEFAULT_CLAIMS
            ),
        )
        for index, entry in enumerate(entries)
    )
    return TrustedEvidenceSource(
        source_id=source_id,
        display_name=source_id,
        manifest=TrustedEvidenceManifest(
            manifest_id=manifest_id or f"{source_id}:manifest:1",
            source_id=source_id,
            merchant_id=entries[0].merchant_id,
            scope_type=scope,
            sku=sku,
            evidence_kinds=kinds,
            manifest_version="1",
            effective_at=T0 - timedelta(days=1),
            expires_at=None,
            records=records,
            supersedes_manifest_id=supersedes_manifest_id,
        ),
    )


def _registry(
    sources: tuple[TrustedEvidenceSource, ...],
    entries: tuple[SemanticEvidenceEntry, ...],
    *,
    provider: object | None = None,
) -> TrustedEvidenceSourceRegistry:
    merchant_id = sources[0].merchant_id
    return TrustedEvidenceSourceRegistry(
        sources=sources,
        providers=SemanticEvidenceProviderRegistry(
            {merchant_id: provider or _Provider(entries)}
        ),
    )


def _acquire(
    registry: TrustedEvidenceSourceRegistry,
    source_ids: tuple[str, ...],
    *,
    merchant_id: str = "merchant-lumen",
    skus: tuple[str, ...] = ("aurora-focus-lamp",),
    item_limit: int = 4,
):
    return registry.acquire(
        source_ids=source_ids,
        merchant_id=merchant_id,
        skus=skus,
        existing_entries=(),
        item_limit=item_limit,
        acquired_at=T0,
    )


def _initial_review(service: CommerceLabService) -> tuple[dict, object]:
    snapshot = service.run_sync(
        user_intent=PRESETS["recoverable"]["intent"],
        preset_id="recoverable",
    )
    assert snapshot["result"]["decision"] == "REVIEW"
    return snapshot, service.get_run(snapshot["run_id"]).private_context


def test_five_record_manifest_over_budget_never_reauthorizes_or_calls_provider(
    tmp_path: Path,
) -> None:
    clock = _MutableClock(T0)
    service = CommerceLabService(state_dir=tmp_path / "state", clock=clock)
    try:
        _, context = _initial_review(service)
        entries = tuple(
            _entry(
                f"record-{index}",
                text=(
                    "This desk lamp is suitable for study and is sold only as a "
                    "one-time purchase."
                    if index < 5
                    else "This desk lamp is an active monthly recurring subscription."
                ),
            )
            for index in range(1, 6)
        )
        source = _source("five-record-source", entries)
        registry = _registry((source,), entries)
        state = create_review_recovery(
            scenario=context.recovery_state.scenario,
            authorization=context.recovery_state.initial_authorization,
            semantic_evidence=None,
            registry=registry,
            created_at=T0,
        )
        recovered = recover_review_once(
            state=state,
            registry=registry,
            semantic_verifier=context.semantic_verifier,
            recorded_at=T0 + timedelta(seconds=1),
        )
        assert recovered.final_action is DecisionAction.REVIEW
        assert recovered.evidence_provider_calls == 0
        assert recovered.current_evidence is None
        assert recovered.audit_events[-1].outcome_codes == (
            AcquisitionItemStatus.BUDGET_INSUFFICIENT.value,
        )
    finally:
        service.close()


@pytest.mark.parametrize(
    "values",
    [(("billing.model", "ONE_TIME"), ("billing.model", "RECURRING")),
     (("content.class", "SAFE"), ("content.class", "PROHIBITED"))],
)
def test_simultaneously_authoritative_records_that_disagree_force_review(
    values: tuple[tuple[str, str], tuple[str, str]],
) -> None:
    first = _entry("one-time-record")
    second = _entry("recurring-record")
    source = _source("conflicting-source", (first, second))
    records = (
        replace(
            source.manifest.records[0],
            claims=_annotated(TrustedEvidenceClaim(*values[0])),
        ),
        replace(
            source.manifest.records[1],
            claims=_annotated(TrustedEvidenceClaim(*values[1])),
        ),
    )
    source = replace(source, manifest=replace(source.manifest, records=records))
    batch = _acquire(_registry((source,), (first, second)), (source.source_id,))
    assert batch.complete is False
    assert batch.conflict_codes == ("SIMULTANEOUS_AUTHORITY_CONFLICT",)
    assert batch.acquired_entries == ()


def test_explicit_record_supersession_uses_only_the_new_record() -> None:
    old = _entry("terms-v1")
    new = _entry("terms-v2")
    source = _source(
        "superseding-source",
        (old, new),
        supersedes={"terms-v2": "terms-v1"},
        claims=(("billing.model", "ONE_TIME"),),
    )
    batch = _acquire(_registry((source,), (old, new)), (source.source_id,))
    assert batch.complete is True
    assert batch.expected_applicable_ids == ("terms-v2",)
    assert tuple(entry.evidence_id for entry in batch.acquired_entries) == ("terms-v2",)


def test_expired_superseding_record_does_not_resurrect_old_record() -> None:
    old = _entry("record-v1")
    new = _entry("record-v2")
    source = _source(
        "record-supersession-source",
        (old, new),
        supersedes={"record-v2": "record-v1"},
        claims=(("billing.model", "ONE_TIME"),),
    )
    old_record, new_record = source.manifest.records
    source = replace(
        source,
        manifest=replace(
            source.manifest,
            records=(old_record, replace(new_record, expires_at=T0)),
        ),
    )
    provider = _Provider((old, new))
    batch = _acquire(
        _registry((source,), (old, new), provider=provider),
        (source.source_id,),
    )
    assert batch.complete is False
    assert batch.expected_applicable_ids == ()
    assert batch.provider_calls == 0
    assert provider.calls == 0
    assert batch.items[0].status is AcquisitionItemStatus.SOURCE_EXPIRED


def test_sku_specific_source_rejects_global_record() -> None:
    global_entry = _entry("global-record", sku=None)
    source = _source("sku-source", (global_entry,))
    batch = _acquire(_registry((source,), (global_entry,)), (source.source_id,))
    assert batch.complete is False
    assert batch.items[0].status is AcquisitionItemStatus.WRONG_BINDING


def test_same_sku_under_another_merchant_is_rejected_before_provider() -> None:
    entry = _entry("merchant-one-record")
    source = _source("merchant-one-source", (entry,))
    batch = _acquire(
        _registry((source,), (entry,)),
        (source.source_id,),
        merchant_id="merchant-other",
    )
    assert batch.complete is False
    assert batch.provider_calls == 0
    assert batch.items[0].status is AcquisitionItemStatus.WRONG_BINDING


def test_global_and_sku_specific_records_combine_but_neither_overrides_conflict() -> None:
    global_entry = _entry("global-billing", sku=None)
    sku_entry = _entry("sku-billing")
    global_source = _source(
        "global-source",
        (global_entry,),
        scope=EvidenceScope.MERCHANT_GLOBAL,
        sku=None,
        claims=(("billing.model", "ONE_TIME"),),
    )
    sku_source = _source(
        "sku-source",
        (sku_entry,),
        claims=(("billing.model", "RECURRING"),),
    )
    registry = _registry((global_source, sku_source), (global_entry, sku_entry))
    batch = _acquire(registry, (global_source.source_id, sku_source.source_id))
    assert batch.complete is False
    assert batch.conflict_codes == ("SIMULTANEOUS_AUTHORITY_CONFLICT",)


def test_unannotated_one_time_and_recurring_authorities_stay_review_without_execution(
    tmp_path: Path,
) -> None:
    """Missing claim metadata cannot delegate authority ranking to semantics."""

    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        initial, context = _initial_review(service)
        initial_entries = context.semantic_evidence.bundle.entries
        initial_global = next(entry for entry in initial_entries if entry.sku is None)
        initial_sku = next(entry for entry in initial_entries if entry.sku is not None)
        one_time = _entry(
            "unannotated-one-time",
            sku=None,
            text="This product is sold as a one-time purchase.",
        )
        recurring = _entry(
            "unannotated-recurring",
            text="This product automatically renews every month.",
        )
        global_source = _source(
            "unannotated-global",
            (initial_global, one_time),
            scope=EvidenceScope.MERCHANT_GLOBAL,
            sku=None,
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
        )
        recurring_source = _source(
            "unannotated-sku",
            (initial_sku, recurring),
            kinds=(EvidenceKind.PURPOSE, EvidenceKind.RECURRENCE),
        )
        global_source = replace(
            global_source,
            manifest=replace(
                global_source.manifest,
                records=tuple(
                    replace(record, claims=())
                    for record in global_source.manifest.records
                ),
            ),
        )
        recurring_source = replace(
            recurring_source,
            manifest=replace(
                recurring_source.manifest,
                records=tuple(
                    replace(record, claims=())
                    for record in recurring_source.manifest.records
                ),
            ),
        )
        provider = _Provider((initial_global, one_time, initial_sku, recurring))
        registry = _registry(
            (global_source, recurring_source),
            provider.entries,
            provider=provider,
        )
        forbidden_model = _ForbiddenSemanticModel()
        context.semantic_verifier = SemanticVerifier(
            model=forbidden_model,
            cache=context.semantic_verifier.cache,
        )
        context.recovery_registry = registry
        custom_state = create_review_recovery(
            scenario=context.recovery_state.scenario,
            authorization=context.recovery_state.initial_authorization,
            semantic_evidence=context.semantic_evidence,
            registry=registry,
            created_at=T0,
        )
        # Keep the already persisted initial chain, changing only the
        # server-owned diagnostic/source plan used by this adversarial round.
        context.recovery_state = replace(
            context.recovery_state,
            gap_analysis=custom_state.gap_analysis,
        )

        recovered = service.recover(initial["run_id"])
        result = recovered["result"]
        assert result["decision"] == "REVIEW"
        assert CONFLICT_CLAIM_METADATA_INCOMPLETE in next(
            event.outcome_codes
            for event in context.recovery_state.audit_events
            if event.event.value == "ACQUISITION_RESULT"
        )
        assert forbidden_model.calls == 0
        assert provider.calls == 1
        assert result["execution"]["capability"] is None
        assert result["execution"]["razorpay_calls"] == 0
        assert result["observed_counters"]["offline_adapter_calls"] == 0
    finally:
        service.close()


def test_preset_id_has_no_effect_on_evidence_or_authorization_hashes(
    tmp_path: Path,
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        intent = PRESETS["recoverable"]["intent"]
        first = service.run_sync(
            user_intent=intent,
            preset_id="safe",
            request_id="preset_isolation_first",
        )
        second = service.run_sync(
            user_intent=intent,
            preset_id="recoverable",
            request_id="preset_isolation_second",
        )
        first_context = service.get_run(first["run_id"]).private_context
        second_context = service.get_run(second["run_id"]).private_context
        assert first["result"]["raw_trace"]["retrieval"][
            "trusted_evidence_selected_ids"
        ] == second["result"]["raw_trace"]["retrieval"][
            "trusted_evidence_selected_ids"
        ]
        assert first_context.recovery_state.current_evidence_sha256 == (
            second_context.recovery_state.current_evidence_sha256
        )
        assert sha256_canonical(first_context.checkout.authorization_result) == (
            sha256_canonical(second_context.checkout.authorization_result)
        )
    finally:
        service.close()


def test_constraint_prose_does_not_change_structured_recovery_family(
    tmp_path: Path,
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)
        scenario = context.recovery_state.scenario
        changed_constraints = tuple(
            replace(constraint, text="Completely different buyer-controlled wording.")
            for constraint in scenario.mandate.payload.constraints.semantic
        )
        changed_mandate = replace(
            scenario.mandate,
            payload=replace(
                scenario.mandate.payload,
                constraints=replace(
                    scenario.mandate.payload.constraints,
                    semantic=changed_constraints,
                ),
            ),
        )
        original = context.recovery_state.gap_analysis
        changed = detect_evidence_gaps(
            authorization=context.recovery_state.initial_authorization,
            mandate=changed_mandate,
            merchant_id=scenario.transaction.payload.merchant_id,
            skus=tuple(line.sku for line in scenario.transaction.payload.lines),
            current_entries=(),
            registry=service.recovery_registry,
            created_at=T0,
        )
        assert tuple(gap.missing_evidence_kind for gap in changed.gaps) == tuple(
            gap.missing_evidence_kind for gap in original.gaps
        )
        assert tuple(gap.candidate_evidence_ids for gap in changed.gaps) == tuple(
            gap.candidate_evidence_ids for gap in original.gaps
        )
    finally:
        service.close()


def test_delayed_recovery_after_mandate_expiry_has_no_capability_or_provider_call(
    tmp_path: Path,
) -> None:
    clock = _MutableClock(T0)
    service = CommerceLabService(state_dir=tmp_path / "state", clock=clock)
    try:
        initial, _ = _initial_review(service)
        clock.value = T0 + timedelta(hours=1)
        recovered = service.recover(initial["run_id"])
        state = service.get_run(initial["run_id"]).private_context.recovery_state
        assert recovered["result"]["decision"] == "BLOCK"
        assert recovered["result"]["execution"]["capability"] is None
        assert recovered["result"]["execution"]["razorpay_calls"] == 0
        assert state.evidence_provider_calls == 0
        assert state.scenario.server_time == clock.value
        assert state.recovery_started_at == clock.value
        assert state.recovery_authorized_at == clock.value
    finally:
        service.close()


def test_recovery_uses_supplied_current_nonce_state_before_provider(
    tmp_path: Path,
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)
        nonce = context.recovery_state.scenario.mandate.payload.nonce
        recovered = recover_review_once(
            state=context.recovery_state,
            registry=service.recovery_registry,
            semantic_verifier=context.semantic_verifier,
            recorded_at=T0 + timedelta(minutes=1),
            nonce_state=NonceLedgerState(frozenset({nonce})),
        )
        assert recovered.final_action is DecisionAction.BLOCK
        assert recovered.evidence_provider_calls == 0
        assert recovered.scenario.nonce_state.is_consumed(nonce)
    finally:
        service.close()


def test_successful_recovery_issues_capability_at_fresh_recovery_time(
    tmp_path: Path,
) -> None:
    clock = _MutableClock(T0)
    service = CommerceLabService(state_dir=tmp_path / "state", clock=clock)
    try:
        initial, context = _initial_review(service)
        recovery_time = T0 + timedelta(minutes=5)
        clock.value = recovery_time
        recovered = service.recover(initial["run_id"])
        capability = context.checkout.execution_authorization
        assert recovered["result"]["decision"] == "ALLOW"
        assert capability.payload.issued_at == recovery_time
        assert context.recovery_state.initial_evaluated_at == T0
        assert context.recovery_state.recovery_started_at == recovery_time
        assert context.recovery_state.recovery_authorized_at == recovery_time
    finally:
        service.close()


def test_cache_exception_consumes_each_round_and_bounds_provider_calls(
    tmp_path: Path,
) -> None:
    clock = _MutableClock(T0)
    service = CommerceLabService(state_dir=tmp_path / "state", clock=clock)
    try:
        initial, context = _initial_review(service)
        context.semantic_verifier = SemanticVerifier(
            model=context.semantic_verifier.model,
            cache=_FailingCache(),
        )
        first = service.recover(initial["run_id"])
        assert first["result"]["decision"] == "REVIEW"
        assert context.recovery_state.rounds_used == 1
        assert context.recovery_state.evidence_provider_calls == 1
        second = service.recover(initial["run_id"])
        assert second["result"]["decision"] == "REVIEW"
        assert context.recovery_state.rounds_used == 2
        assert context.recovery_state.evidence_provider_calls == 2
        with pytest.raises(RuntimeError, match="round budget exhausted"):
            service.recover(initial["run_id"])
        assert context.recovery_state.evidence_provider_calls == 2
        assert context.client.adapter_calls == 0
    finally:
        service.close()


def test_provider_failure_consumes_reserved_round(tmp_path: Path) -> None:
    entry = _entry("provider-failure-record")
    source = _source("provider-failure-source", (entry,))
    registry = _registry((source,), (entry,), provider=_FailingProvider())
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)
        state = create_review_recovery(
            scenario=context.recovery_state.scenario,
            authorization=context.recovery_state.initial_authorization,
            semantic_evidence=None,
            registry=registry,
            created_at=T0,
        )
        recovered = recover_review_once(
            state=state,
            registry=registry,
            semantic_verifier=context.semantic_verifier,
            recorded_at=T0 + timedelta(seconds=1),
        )
        assert recovered.final_action is DecisionAction.REVIEW
        assert recovered.rounds_used == 1
        assert recovered.round_in_flight is None
        assert recovered.evidence_provider_calls == 1
        assert recovered.audit_events[-1].outcome_codes == (
            AcquisitionItemStatus.SOURCE_UNAVAILABLE.value,
        )
    finally:
        service.close()


def test_authorization_exception_leaves_round_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)

        def fail_authorization(**_kwargs: object) -> object:
            raise RuntimeError("injected authorization failure")

        monkeypatch.setattr(
            "mandateguard.recovery.orchestration.authorize_transaction",
            fail_authorization,
        )
        recovered = recover_review_once(
            state=context.recovery_state,
            registry=service.recovery_registry,
            semantic_verifier=context.semantic_verifier,
            recorded_at=T0 + timedelta(seconds=1),
        )
        assert recovered.final_action is DecisionAction.REVIEW
        assert recovered.rounds_used == 1
        assert recovered.evidence_provider_calls == 1
        assert recovered.audit_events[-1].outcome_codes == (
            AcquisitionItemStatus.AUTHORIZATION_FAILED.value,
        )
    finally:
        service.close()


def test_duplicate_source_aliases_do_not_starve_later_candidate() -> None:
    first = _entry("first-record")
    second = _entry("second-record")
    primary = _source("alias-a", (first,))
    alias = _source("alias-b", (first,))
    later = _source(
        "later-source",
        (replace(second, sku=None),),
        scope=EvidenceScope.MERCHANT_GLOBAL,
        sku=None,
    )
    registry = _registry((primary, alias, later), (first, second))
    candidates = registry.candidates(
        merchant_id="merchant-lumen",
        sku="aurora-focus-lamp",
        evidence_kind=EvidenceKind.PURPOSE,
        at_time=T0,
    )
    assert tuple(source.source_id for source in candidates) == (
        "alias-a",
        "later-source",
    )


def test_multiline_gap_detection_resolves_each_sku_independently(
    tmp_path: Path,
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)
        scenario = context.recovery_state.scenario
        original = scenario.transaction.payload.lines[0]
        second_line = TransactionLine(
            sku="aurora-desk-riser",
            effective_unit_price_minor=49900,
            quantity=1,
            line_total_minor=49900,
            recurring=False,
        )
        payload = replace(
            scenario.transaction.payload,
            lines=(original, second_line),
            declared_order_total_minor=original.line_total_minor + 49900,
            declared_aggregate_quantity=2,
        )
        transaction = Transaction(
            payload=payload,
            declared_transaction_hash=transaction_body_sha256(payload),
        )
        first_entry = _entry("aurora-complete", sku=original.sku)
        second_entry = _entry("riser-complete", sku=second_line.sku)
        first_source = _source("aurora-source", (first_entry,), sku=original.sku)
        second_source = _source("riser-source", (second_entry,), sku=second_line.sku)
        registry = _registry(
            (first_source, second_source), (first_entry, second_entry)
        )
        gaps = detect_evidence_gaps(
            authorization=context.recovery_state.initial_authorization,
            mandate=scenario.mandate,
            merchant_id=payload.merchant_id,
            skus=tuple(line.sku for line in transaction.payload.lines),
            current_entries=(),
            registry=registry,
            created_at=T0,
        )
        assert gaps.status is GapAnalysisStatus.RECOVERABLE
        assert {gap.sku for gap in gaps.gaps} == {
            "aurora-focus-lamp",
            "aurora-desk-riser",
        }
        expected_source = {
            "aurora-focus-lamp": "aurora-source",
            "aurora-desk-riser": "riser-source",
        }
        for gap in gaps.gaps:
            assert gap.candidate_evidence_ids == (expected_source[gap.sku],)
    finally:
        service.close()


def test_multiline_gap_detection_fails_closed_when_one_sku_has_no_source(
    tmp_path: Path,
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)
        scenario = context.recovery_state.scenario
        covered = _entry("covered-line")
        source = _source("covered-source", (covered,))
        gaps = detect_evidence_gaps(
            authorization=context.recovery_state.initial_authorization,
            mandate=scenario.mandate,
            merchant_id=scenario.transaction.payload.merchant_id,
            skus=("aurora-focus-lamp", "unregistered-second-line"),
            current_entries=(),
            registry=_registry((source,), (covered,)),
            created_at=T0,
        )
        assert gaps.status is GapAnalysisStatus.INCOMPLETE_COVERAGE
        assert any(
            gap.sku == "unregistered-second-line"
            and gap.candidate_evidence_ids == ()
            for gap in gaps.gaps
        )
    finally:
        service.close()


def test_incomplete_manifest_response_forces_review() -> None:
    first = _entry("expected-one")
    second = _entry("expected-two")
    source = _source("complete-source", (first, second))
    batch = _acquire(_registry((source,), (first,)), (source.source_id,))
    assert batch.complete is False
    assert batch.items[0].status is AcquisitionItemStatus.SOURCE_INCOMPLETE
    assert batch.acquired_entries == ()


def test_manifest_hash_mismatch_forces_review() -> None:
    entry = _entry("hash-bound-record")
    source = _source(
        "hash-bound-source",
        (entry,),
        expected_hashes={entry.evidence_id: "0" * 64},
    )
    batch = _acquire(_registry((source,), (entry,)), (source.source_id,))
    assert batch.complete is False
    assert batch.items[0].status is AcquisitionItemStatus.TAMPERED


def test_duplicate_evidence_id_with_different_manifest_hash_forces_review() -> None:
    entry = _entry("duplicate-id")
    first = _source("duplicate-source-a", (entry,))
    second = _source(
        "duplicate-source-b",
        (replace(entry, sku=None),),
        scope=EvidenceScope.MERCHANT_GLOBAL,
        sku=None,
        expected_hashes={entry.evidence_id: "0" * 64},
    )
    batch = _acquire(
        _registry((first, second), (entry,)),
        (first.source_id, second.source_id),
    )
    assert batch.complete is False
    assert batch.provider_calls == 0
    assert batch.conflict_codes == ("DUPLICATE_ID_HASH_CONFLICT",)
    assert batch.acquired_entries == ()


def test_expired_manifest_record_is_rejected_before_provider() -> None:
    entry = _entry("expired-record")
    source = _source("expired-source", (entry,))
    expired_record = replace(source.manifest.records[0], expires_at=T0)
    source = replace(
        source,
        manifest=replace(source.manifest, records=(expired_record,)),
    )
    batch = _acquire(_registry((source,), (entry,)), (source.source_id,))
    assert batch.complete is False
    assert batch.provider_calls == 0
    assert batch.items[0].status is AcquisitionItemStatus.SOURCE_EXPIRED


def test_source_not_effective_is_rejected_before_provider() -> None:
    entry = _entry("future-source-record")
    source = _source("future-source", (entry,))
    source = replace(
        source,
        manifest=replace(
            source.manifest,
            effective_at=T0 + timedelta(days=1),
        ),
    )
    provider = _Provider((entry,))
    batch = _acquire(
        _registry((source,), (entry,), provider=provider),
        (source.source_id,),
    )
    assert batch.complete is False
    assert batch.provider_calls == 0
    assert provider.calls == 0
    assert batch.items[0].status is AcquisitionItemStatus.SOURCE_NOT_EFFECTIVE


def test_expired_manifest_is_rejected_before_provider() -> None:
    entry = _entry("expired-manifest-record")
    source = _source("expired-manifest-source", (entry,))
    source = replace(
        source,
        manifest=replace(source.manifest, expires_at=T0),
    )
    provider = _Provider((entry,))
    batch = _acquire(
        _registry((source,), (entry,), provider=provider),
        (source.source_id,),
    )
    assert batch.complete is False
    assert batch.provider_calls == 0
    assert provider.calls == 0
    assert batch.items[0].status is AcquisitionItemStatus.SOURCE_EXPIRED


def test_manifest_supersession_selects_only_the_replacement() -> None:
    old_entry = _entry("manifest-old")
    new_entry = _entry("manifest-new")
    old = _source("manifest-source-v1", (old_entry,))
    new = _source(
        "manifest-source-v2",
        (new_entry,),
        supersedes_manifest_id=old.manifest.manifest_id,
    )
    provider = _Provider((new_entry,))
    registry = _registry((old, new), (new_entry,), provider=provider)

    candidates = registry.candidates(
        merchant_id="merchant-lumen",
        sku="aurora-focus-lamp",
        evidence_kind=EvidenceKind.RECURRENCE,
        at_time=T0,
    )
    assert tuple(source.source_id for source in candidates) == (new.source_id,)
    old_batch = _acquire(registry, (old.source_id,))
    assert old_batch.provider_calls == 0
    assert old_batch.items[0].status is AcquisitionItemStatus.SOURCE_SUPERSEDED
    new_batch = _acquire(registry, (new.source_id,))
    assert new_batch.complete is True
    assert tuple(entry.evidence_id for entry in new_batch.acquired_entries) == (
        new_entry.evidence_id,
    )


def test_expired_superseding_manifest_never_resurrects_old_manifest() -> None:
    old_entry = _entry("permanently-retired-v1")
    new_entry = _entry("expired-replacement-v2")
    old = _source("permanent-source-v1", (old_entry,))
    new = _source(
        "permanent-source-v2",
        (new_entry,),
        supersedes_manifest_id=old.manifest.manifest_id,
    )
    new = replace(new, manifest=replace(new.manifest, expires_at=T0))
    provider = _Provider((old_entry, new_entry))
    registry = _registry((old, new), (old_entry, new_entry), provider=provider)

    assert registry.candidates(
        merchant_id="merchant-lumen",
        sku="aurora-focus-lamp",
        evidence_kind=EvidenceKind.RECURRENCE,
        at_time=T0,
    ) == ()
    old_batch = _acquire(registry, (old.source_id,))
    new_batch = _acquire(registry, (new.source_id,))
    assert old_batch.items[0].status is AcquisitionItemStatus.SOURCE_SUPERSEDED
    assert new_batch.items[0].status is AcquisitionItemStatus.SOURCE_EXPIRED
    assert provider.calls == 0


def test_registry_rejects_overlapping_same_scope_sources_even_if_kinds_differ() -> None:
    purpose_entry = _entry("same-scope-purpose")
    recurrence_entry = _entry("same-scope-recurrence")
    purpose = _source(
        "same-scope-purpose-source",
        (purpose_entry,),
        kinds=(EvidenceKind.PURPOSE,),
    )
    recurrence = _source(
        "same-scope-recurrence-source",
        (recurrence_entry,),
        kinds=(EvidenceKind.RECURRENCE,),
    )
    with pytest.raises(ValueError, match="permanently unrecoverable"):
        _registry((purpose, recurrence), (purpose_entry, recurrence_entry))


def test_sku_specific_manifest_requires_a_sku_at_construction() -> None:
    entry = _entry("missing-sku-manifest")
    with pytest.raises(ValueError, match="sku"):
        TrustedEvidenceManifest(
            manifest_id="missing-sku-manifest:1",
            source_id="missing-sku-source",
            merchant_id="merchant-lumen",
            scope_type=EvidenceScope.SKU_SPECIFIC,
            sku=None,
            evidence_kinds=(EvidenceKind.PURPOSE,),
            manifest_version="1",
            effective_at=T0,
            expires_at=None,
            records=(
                TrustedEvidenceRecord(
                    evidence_id=entry.evidence_id,
                    expected_entry_sha256=sha256_canonical(entry),
                    effective_at=T0,
                ),
            ),
        )


def test_initial_evidence_omission_cannot_increase_authority(tmp_path: Path) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        _, context = _initial_review(service)
        initial_entry = _entry("initial-unmanifested")
        initial_bundle = SemanticEvidenceBundle(
            merchant_id=initial_entry.merchant_id,
            entries=(initial_entry,),
        )
        initial_evidence = SemanticEvidence(
            bundle=initial_bundle,
            semantic_evidence_sha256=semantic_evidence_sha256(initial_bundle),
        )
        acquired_entry = _entry("manifest-complete-record")
        source = _source("manifest-complete-source", (acquired_entry,))
        registry = _registry((source,), (acquired_entry,))
        state = create_review_recovery(
            scenario=context.recovery_state.scenario,
            authorization=context.recovery_state.initial_authorization,
            semantic_evidence=initial_evidence,
            registry=registry,
            created_at=T0,
        )
        recovered = recover_review_once(
            state=state,
            registry=registry,
            semantic_verifier=context.semantic_verifier,
            recorded_at=T0 + timedelta(seconds=1),
        )
        assert recovered.final_action is DecisionAction.REVIEW
        assert recovered.current_evidence == initial_evidence
        assert recovered.audit_events[-1].outcome_codes == (
            AcquisitionItemStatus.INITIAL_EVIDENCE_UNMANIFESTED.value,
        )
    finally:
        service.close()


def test_authorization_subset_property_requires_exact_complete_applicable_ids() -> None:
    entries = tuple(_entry(f"property-record-{index}") for index in range(3))
    source = _source("property-source", entries)
    for subset_size in range(1, len(entries) + 1):
        for subset in combinations(entries, subset_size):
            batch = _acquire(_registry((source,), tuple(subset)), (source.source_id,))
            expected = tuple(sorted(entry.evidence_id for entry in entries))
            if tuple(sorted(entry.evidence_id for entry in subset)) == expected:
                assert batch.complete is True
                assert batch.actual_applicable_ids == batch.expected_applicable_ids
            else:
                assert batch.complete is False
                assert batch.acquired_entries == ()


def test_recovery_audit_is_persisted_with_complete_provenance(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    service = CommerceLabService(state_dir=state_dir, clock=lambda: T0)
    review_id = None
    try:
        initial, context = _initial_review(service)
        service.recover(initial["run_id"])
        review_id = context.recovery_state.review_id
        events = service.recovery_audit_store.read(context.recovery_state.review_id)
        assert [event["sequence"] for event in events] == list(
            range(1, len(events) + 1)
        )
        initial_review = next(
            event for event in events if event["event"] == "INITIAL_REVIEW"
        )
        assert initial_review["authorization_result_sha256"]
        assert initial_review["constraint_statuses"]
        assert initial_review["evidence_set_sha256"]
        gap = next(event for event in events if event["event"] == "GAP_IDENTIFIED")
        assert gap["gap_kinds"]
        assert gap["diagnostic_version"]
        assert gap["registry_sha256"]
        assert gap["manifest_versions"]
        assert gap["manifest_sha256s"]
        acquisition = next(
            event for event in events if event["event"] == "ACQUISITION_RESULT"
        )
        assert acquisition["acquisition_complete"] is True
        assert acquisition["expected_evidence_ids"] == acquisition["actual_evidence_ids"]
        assert acquisition["evidence_set_sha256"] != initial_review[
            "evidence_set_sha256"
        ]
        reauthorization = next(
            event for event in events if event["event"] == "REAUTHORIZATION"
        )
        assert reauthorization["semantic_input_sha256"]
        assert reauthorization["semantic_output_sha256"]
        assert reauthorization["recovery_authorized_at"]
    finally:
        service.close()
    assert review_id is not None
    reopened = SQLiteRecoveryAuditStore(state_dir / "recovery-audit.sqlite3")
    try:
        persisted = reopened.read(review_id)
        assert persisted == events
    finally:
        reopened.close()


def test_round_audit_append_failure_wedges_review_safely_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        initial, context = _initial_review(service)
        provider_calls_before = (
            context.operational_counters.trusted_evidence_provider_calls
        )

        def fail_append(_store: object, _events: object) -> None:
            raise RecoveryAuditStoreError("injected append failure")

        monkeypatch.setattr(SQLiteRecoveryAuditStore, "append", fail_append)
        with pytest.raises(RuntimeError, match=RECOVERY_AUDIT_UNAVAILABLE):
            service.recover(initial["run_id"])

        state = context.recovery_state
        assert state.rounds_used == 1
        assert state.round_in_flight == 1
        assert context.recovery_audit_state == "AUDIT_PERSISTENCE_FAILED"
        assert (
            context.operational_counters.trusted_evidence_provider_calls
            == provider_calls_before
        )
        assert context.client.adapter_calls == 0
        assert context.checkout.execution_authorization is None
        with pytest.raises(RuntimeError, match=RECOVERY_AUDIT_UNAVAILABLE):
            service.recover(initial["run_id"])
        assert context.client.adapter_calls == 0
    finally:
        service.close()


def test_future_evaluation_metrics_are_observed_from_runtime_counters(
    tmp_path: Path,
) -> None:
    service = CommerceLabService(state_dir=tmp_path / "state", clock=lambda: T0)
    try:
        initial, _ = _initial_review(service)
        recovered = service.recover(initial["run_id"])
        counters = recovered["result"]["observed_counters"]
        assert counters == {
            "openai_calls": 0,
            "razorpay_http_calls": 0,
            "offline_adapter_calls": 1,
            "trusted_evidence_provider_calls": 1,
            "acquisition_rounds": 1,
            "new_evidence_items": 1,
            "planner_direct_allow_count": 0,
        }
        assert set(counters) == set(OBSERVED_COUNTER_NAMES)
    finally:
        service.close()
