"""Execute the frozen 20-case Resolve recovery evaluation, or refuse to.

The runner is locked behind the preregistration gate in
``mandateguard.engineering.resolve_eval.preregistration``. It refuses unless the
plan is frozen, every fixture and manifest hash matches, the trust-sensitive
policy equals the product policy, the working tree is clean, the preregistration
commit binding holds, and no outcome artifact already exists. Any failure stops
the run before a single case is evaluated.

It adds no authorization logic. Every decision comes from
``authorize_transaction`` and the existing bounded recovery orchestration, at
the product's own evidence policy.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from threading import RLock
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mandateguard.core.nonce_ledger import NonceLedgerState  # noqa: E402
from mandateguard.engineering.resolve_eval.metrics import (  # noqa: E402
    METRIC_SCHEMA_VERSION,
    OBSERVED_COUNTER_NAMES,
    PREREGISTERED_OBSERVED_METRIC_NAMES,
)
from mandateguard.engineering.resolve_eval.preregistration import (  # noqa: E402
    OUTPUT_ROOT,
    PreregistrationError,
    require_execution_preconditions,
    utc_now,
)
from mandateguard.engineering.resolve_eval.worlds import (  # noqa: E402
    ResolveCaseWorld,
    build_registry,
)
from mandateguard.execution import (  # noqa: E402
    HMACSHA256Signer,
    HMACSHA256Verifier,
    SQLiteExecutionLedger,
    TrustedExecutionConfig,
    issue_execution_authorization,
)
from mandateguard.execution.executor import execute_razorpay_order  # noqa: E402
from mandateguard.execution.models import (  # noqa: E402
    ExecutionRefusal,
    ExecutionRefusalReason,
    RazorpayOrderRequest,
    RazorpayOrderResult,
)
from mandateguard.intelligence.cache.semantic_cache import (  # noqa: E402
    SQLiteSemanticCache,
)
from mandateguard.intelligence.offline import DeterministicSemanticModel  # noqa: E402
from mandateguard.models.decision import DecisionAction  # noqa: E402
from mandateguard.product.evidence_policy import PRODUCT_EVIDENCE_POLICY  # noqa: E402
from mandateguard.recovery import (  # noqa: E402
    AcquisitionItemStatus,
    EvidenceGapAnalysis,
    MAX_NEW_EVIDENCE_ITEMS,
    RecoveryEventType,
    TrustedEvidenceSourceRegistry,
    complete_recovery_round,
    create_review_recovery,
    reserve_recovery_round,
)
from mandateguard.replay.scenario import ReplayScenario  # noqa: E402
from mandateguard.semantic.evidence import (  # noqa: E402
    SemanticEvidenceProviderRegistry,
    SemanticEvidenceSourceUnavailableError,
)
from mandateguard.semantic.orchestration import authorize_transaction  # noqa: E402
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier  # noqa: E402


BUDGET_CODES = frozenset({AcquisitionItemStatus.BUDGET_INSUFFICIENT.value})
CONFLICT_CODES = frozenset(
    {
        AcquisitionItemStatus.CONFLICT.value,
        "SIMULTANEOUS_AUTHORITY_CONFLICT",
        "DUPLICATE_ID_HASH_CONFLICT",
        "CLAIM_METADATA_INCOMPLETE",
    }
)
INCOMPLETE_CODES = frozenset({AcquisitionItemStatus.SOURCE_INCOMPLETE.value})
BINDING_CODES = frozenset({AcquisitionItemStatus.WRONG_BINDING.value})
FRESHNESS_CODES = frozenset(
    {
        AcquisitionItemStatus.SOURCE_EXPIRED.value,
        AcquisitionItemStatus.SOURCE_NOT_EFFECTIVE.value,
        AcquisitionItemStatus.SOURCE_SUPERSEDED.value,
    }
)


class _FailingEvidenceProvider:
    """A preregistered deterministic trusted-provider outage."""

    __slots__ = ("merchant_id",)

    def __init__(self, merchant_id: str) -> None:
        self.merchant_id = merchant_id

    def fetch_semantic_evidence(self, *, merchant_id: str) -> Any:
        raise SemanticEvidenceSourceUnavailableError(
            f"preregistered provider outage for {merchant_id}"
        )


class _CountingOrdersClient:
    """Network-free execution double that counts every call it actually makes."""

    __slots__ = ("adapter_calls", "external_network_calls", "_lock")

    def __init__(self) -> None:
        self.adapter_calls = 0
        self.external_network_calls = 0
        self._lock = RLock()

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        with self._lock:
            self.adapter_calls += 1
        suffix = sha256(request.receipt.encode("ascii")).hexdigest()[:16]
        return RazorpayOrderResult(
            razorpay_order_id=f"order_offline_{suffix}",
            amount=request.amount,
            currency=request.currency,
            receipt=request.receipt,
            status="created",
        )


@dataclass(slots=True)
class ObservedCounters:
    """Counters incremented by real call sites, never by a mode string."""

    openai_calls: int = 0
    razorpay_http_calls: int = 0
    offline_adapter_calls: int = 0
    trusted_evidence_provider_calls: int = 0
    acquisition_rounds: int = 0
    new_evidence_items: int = 0
    planner_direct_allow_count: int = 0
    budget_exhaustion_count: int = 0
    authority_conflict_count: int = 0
    source_incomplete_count: int = 0
    binding_rejection_count: int = 0
    expired_recovery_count: int = 0
    replay_rejection_count: int = 0
    provider_calls_before_final_allow: int = 0
    violations: list[str] = field(default_factory=list)


def _replay_seed(case_id: str) -> int:
    return int(sha256(case_id.encode("ascii")).hexdigest()[:8], 16)


def _case_registry(
    base: TrustedEvidenceSourceRegistry,
    world: ResolveCaseWorld,
    worlds: tuple[ResolveCaseWorld, ...],
    observe,
) -> TrustedEvidenceSourceRegistry:
    """Return the shared registry, with a preregistered outage if declared.

    Manifests are identical in every case, so ``registry_sha256`` never changes:
    only the provider behind one merchant is replaced, and only when the frozen
    world declares the fault.
    """

    if world.provider_fault is None:
        return base.instrumented(observe)
    providers: dict[str, Any] = {}
    for other in worlds:
        for merchant_id, fixture_path in other.evidence_fixtures.items():
            if merchant_id == world.provider_fault.merchant_id:
                providers[merchant_id] = _FailingEvidenceProvider(merchant_id)
            else:
                providers.setdefault(merchant_id, _fixture_provider(fixture_path))
    faulted = TrustedEvidenceSourceRegistry(
        sources=base.sources,
        providers=SemanticEvidenceProviderRegistry(providers),
    )
    if faulted.registry_sha256 != base.registry_sha256:
        raise RuntimeError("provider fault changed the trusted manifest set")
    return faulted.instrumented(observe)


def _fixture_provider(path: Path) -> Any:
    from mandateguard.semantic.evidence import FixtureSemanticEvidenceProvider

    return FixtureSemanticEvidenceProvider(path)


def _classify(codes: tuple[str, ...], counters: ObservedCounters) -> None:
    unique = set(codes)
    if unique & BUDGET_CODES:
        counters.budget_exhaustion_count += 1
    if unique & CONFLICT_CODES:
        counters.authority_conflict_count += 1
    if unique & INCOMPLETE_CODES:
        counters.source_incomplete_count += 1
    if unique & BINDING_CODES:
        counters.binding_rejection_count += 1
    if unique & FRESHNESS_CODES:
        counters.expired_recovery_count += 1


def _run_case(
    world: ResolveCaseWorld,
    case: dict[str, Any],
    *,
    base_registry: TrustedEvidenceSourceRegistry,
    worlds: tuple[ResolveCaseWorld, ...],
    verifier: SemanticVerifier,
    ledger: SQLiteExecutionLedger,
    signing_key: bytes,
    counters: ObservedCounters,
) -> dict[str, Any]:
    provider_calls = 0

    def observe(_merchant_id: str) -> None:
        nonlocal provider_calls
        provider_calls += 1
        counters.trusted_evidence_provider_calls += 1

    registry = _case_registry(base_registry, world, worlds, observe)
    evaluated_at = utc_now()
    scenario = ReplayScenario(
        mandate=world.mandate,
        transaction=world.transaction,
        catalog_snapshot=world.catalog_snapshot,
        server_time=evaluated_at,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=world.committed_hashes,
        replay_seed=_replay_seed(world.case_id),
        evaluated_at=evaluated_at,
    )
    initial = authorize_transaction(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.psp_committed_hashes,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
        semantic_evidence=world.initial_evidence,
        semantic_verifier=verifier,
        semantic_mode=SemanticMode.LIVE,
    )
    initial_action = initial.final_action.value
    if initial.final_action is not DecisionAction.REVIEW:
        return {
            "case_id": world.case_id,
            "initial_action": initial_action,
            "final_action": initial_action,
            "status": "PRECONDITION_FAILED",
            "detail": "the case did not begin at REVIEW",
        }

    state = create_review_recovery(
        scenario=scenario,
        authorization=initial,
        semantic_evidence=world.initial_evidence,
        registry=registry,
        created_at=evaluated_at,
    )
    if not isinstance(state.gap_analysis, EvidenceGapAnalysis):
        counters.violations.append(f"{world.case_id}: planner output is not diagnostic")
    if any(
        hasattr(state.gap_analysis, name)
        for name in ("final_action", "action", "decision")
    ):
        counters.planner_direct_allow_count += 1
        counters.violations.append(f"{world.case_id}: planner exposed an action (S11)")

    refusals: list[str] = []
    outcome_codes: list[str] = []
    attempts = int(case["expected_recovery_attempts"])
    for _ in range(attempts):
        if state.final_action is not DecisionAction.REVIEW:
            break
        recovery_time = utc_now()
        try:
            reserved = reserve_recovery_round(
                state=state,
                registry=registry,
                recovery_started_at=recovery_time,
            )
        except RuntimeError as error:
            refusals.append(str(error))
            break
        counters.acquisition_rounds += 1
        before = len(reserved.audit_events)
        state = complete_recovery_round(
            state=reserved,
            registry=registry,
            semantic_verifier=verifier,
            recovery_time=recovery_time,
            catalog_snapshot=world.catalog_snapshot,
            nonce_state=reserved.scenario.nonce_state,
        )
        for event in state.audit_events[before:]:
            outcome_codes.extend(event.outcome_codes)
            _classify(event.outcome_codes, counters)

    counters.new_evidence_items += state.new_evidence_items
    if state.rounds_used > PRODUCT_EVIDENCE_POLICY.max_acquisition_rounds:
        counters.violations.append(f"{world.case_id}: acquisition round budget exceeded (S8)")
    if state.new_evidence_items > PRODUCT_EVIDENCE_POLICY.max_new_evidence_items:
        counters.violations.append(f"{world.case_id}: evidence item budget exceeded (S8)")

    for probe in world.binding_probes:
        batch = registry.acquire(
            source_ids=(probe.source_id,),
            merchant_id=world.merchant_id,
            skus=(world.sku,),
            existing_entries=(),
            item_limit=MAX_NEW_EVIDENCE_ITEMS,
            acquired_at=utc_now(),
        )
        statuses = tuple(item.status.value for item in batch.items)
        _classify(statuses, counters)
        if statuses != (probe.expected_status,) or batch.acquired_entries:
            counters.violations.append(
                f"{world.case_id}: binding probe {probe.source_id} was not rejected "
                "(S3/S4)"
            )

    acquired_entries = (
        state.current_evidence.bundle.entries if state.current_evidence else ()
    )
    foreign = [
        entry.evidence_id
        for entry in acquired_entries
        if entry.merchant_id != world.merchant_id
        or (entry.sku is not None and entry.sku != world.sku)
    ]
    if foreign:
        counters.violations.append(
            f"{world.case_id}: acquired evidence is wrongly bound (S3/S4): {foreign}"
        )

    initial_ids = {entry.evidence_id for entry in world.initial_evidence.bundle.entries}
    if state.recovery_authorized_at is not None and not initial_ids.issubset(
        {entry.evidence_id for entry in acquired_entries}
    ):
        counters.violations.append(
            f"{world.case_id}: recovery dropped applicable initial evidence (S9)"
        )

    final_action = state.final_action.value
    allowed = tuple(case["allowed_final_actions"])
    forbidden = tuple(case["forbidden_final_actions"])
    if final_action in forbidden:
        counters.violations.append(
            f"{world.case_id}: final action {final_action} is preregistered forbidden"
        )
    if final_action == "ALLOW" and not any(
        event.event is RecoveryEventType.REAUTHORIZATION
        for event in state.audit_events
    ):
        counters.violations.append(f"{world.case_id}: ALLOW without reauthorization (S1)")

    execution: dict[str, Any] = {
        "capability_issued": False,
        "order_created": False,
        "replay": None,
    }
    calls_before_allow = 0
    if state.final_action is DecisionAction.ALLOW:
        client = _CountingOrdersClient()
        calls_before_allow = client.adapter_calls
        counters.provider_calls_before_final_allow += calls_before_allow
        if calls_before_allow:
            counters.violations.append(
                f"{world.case_id}: payment provider called before final ALLOW (S7)"
            )
        issued_at = utc_now()
        config = TrustedExecutionConfig(
            merchant_id=world.merchant_id,
            account_scope="razorpay-test-resolve-evaluation",
        )
        capability = issue_execution_authorization(
            authorization_result=state.current_authorization,
            authorization_scenario=state.scenario,
            semantic_evidence=state.current_evidence,
            semantic_verifier=verifier,
            issued_at=issued_at,
            expires_at=min(
                issued_at + timedelta(minutes=2),
                state.scenario.mandate.payload.expires_at,
            ),
            decision_nonce="mg_resolve_eval_"
            + sha256(world.case_id.encode("ascii")).hexdigest()[:24],
            config=config,
            signer=HMACSHA256Signer(key_id="resolve-eval", key=signing_key),
        )
        if isinstance(capability, ExecutionRefusal):
            counters.violations.append(
                f"{world.case_id}: ALLOW capability refused: {capability.reason.value}"
            )
        else:
            execution["capability_issued"] = True
            verifier_impl = HMACSHA256Verifier({"resolve-eval": signing_key})
            result = execute_razorpay_order(
                authorization=capability,
                authorization_result=state.current_authorization,
                mandate=state.scenario.mandate,
                transaction=state.scenario.transaction,
                now=utc_now(),
                config=config,
                verifier=verifier_impl,
                ledger=ledger,
                client=client,
            )
            execution["order_created"] = not isinstance(result, ExecutionRefusal)
            counters.offline_adapter_calls += client.adapter_calls
            counters.razorpay_http_calls += client.external_network_calls
            calls_before_replay = client.adapter_calls
            replay = execute_razorpay_order(
                authorization=capability,
                authorization_result=state.current_authorization,
                mandate=state.scenario.mandate,
                transaction=state.scenario.transaction,
                now=utc_now(),
                config=config,
                verifier=verifier_impl,
                ledger=ledger,
                client=client,
            )
            rejected = (
                isinstance(replay, ExecutionRefusal)
                and replay.reason is ExecutionRefusalReason.NONCE_ALREADY_USED
                and client.adapter_calls == calls_before_replay
            )
            execution["replay"] = {
                "status": "REJECTED_BEFORE_NETWORK" if rejected else "UNEXPECTED",
                "additional_adapter_calls": client.adapter_calls - calls_before_replay,
            }
            if rejected:
                counters.replay_rejection_count += 1
            else:
                counters.violations.append(
                    f"{world.case_id}: recovered capability replay was not refused (S10)"
                )
    elif state.current_authorization is not None:
        execution["capability_issued"] = False

    if final_action in allowed:
        status = "PASS"
    elif final_action in forbidden:
        status = "SAFETY_VIOLATION"
    else:
        status = "UNRESOLVED_MISS"

    return {
        "case_id": world.case_id,
        "case_family": world.case_family,
        "merchant_id": world.merchant_id,
        "sku": world.sku,
        "amount_minor": world.amount_minor,
        "currency": world.currency,
        "initial_action": initial_action,
        "final_action": final_action,
        "expected_final_action": case["expected_final_action"],
        "allowed_final_actions": list(allowed),
        "forbidden_final_actions": list(forbidden),
        "status": status,
        "rounds_used": state.rounds_used,
        "new_evidence_items": state.new_evidence_items,
        "trusted_evidence_provider_calls": provider_calls,
        "provider_calls_before_final_allow": calls_before_allow,
        "initial_evidence_sha256": state.initial_evidence_sha256,
        "final_evidence_sha256": state.current_evidence_sha256,
        "acquisition_outcome_codes": sorted(set(outcome_codes)),
        "recovery_refusals": refusals,
        "execution": execution,
        "audit_event_count": len(state.audit_events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="permit an existing outcome artifact directory (explicit resume)",
    )
    arguments = parser.parse_args()

    try:
        frozen, commit_sha = require_execution_preconditions(
            REPOSITORY_ROOT, now=utc_now(), allow_resume=arguments.resume
        )
    except PreregistrationError as error:
        print(f"EVALUATION REFUSED: {error}", file=sys.stderr)
        return 1

    cases = {case["case_id"]: case for case in frozen.plan["cases"]}
    counters = ObservedCounters()
    outcomes: list[dict[str, Any]] = []
    base_registry = build_registry(frozen.worlds)

    with tempfile.TemporaryDirectory(prefix="mandateguard-resolve-eval-") as temporary:
        state_dir = Path(temporary)
        cache = SQLiteSemanticCache(state_dir / "semantic-cache.sqlite3")
        ledger = SQLiteExecutionLedger(state_dir / "execution-ledger.sqlite3")
        model = DeterministicSemanticModel()
        verifier = SemanticVerifier(model=model, cache=cache)
        signing_key = b"resolve-evaluation-offline-signing-key-32bytes"[:32]
        try:
            for world in frozen.worlds:
                outcomes.append(
                    _run_case(
                        world,
                        cases[world.case_id],
                        base_registry=base_registry,
                        worlds=frozen.worlds,
                        verifier=verifier,
                        ledger=ledger,
                        signing_key=signing_key,
                        counters=counters,
                    )
                )
        finally:
            cache.close()
            ledger.close()

    observed = {
        "initial_review_count": sum(
            item["initial_action"] == "REVIEW" for item in outcomes
        ),
        "resolved_count": sum(item["final_action"] != "REVIEW" for item in outcomes),
        "review_to_allow_count": sum(
            item["final_action"] == "ALLOW" for item in outcomes
        ),
        "review_to_block_count": sum(
            item["final_action"] == "BLOCK" for item in outcomes
        ),
        "review_to_review_count": sum(
            item["final_action"] == "REVIEW" for item in outcomes
        ),
        "trusted_evidence_provider_calls": counters.trusted_evidence_provider_calls,
        "provider_calls_before_final_allow": counters.provider_calls_before_final_allow,
        "offline_adapter_calls": counters.offline_adapter_calls,
        "razorpay_http_calls": counters.razorpay_http_calls,
        "openai_calls": counters.openai_calls,
        "acquisition_rounds": counters.acquisition_rounds,
        "new_evidence_items": counters.new_evidence_items,
        "planner_direct_allow_count": counters.planner_direct_allow_count,
        "budget_exhaustion_count": counters.budget_exhaustion_count,
        "authority_conflict_count": counters.authority_conflict_count,
        "source_incomplete_count": counters.source_incomplete_count,
        "binding_rejection_count": counters.binding_rejection_count,
        "expired_recovery_count": counters.expired_recovery_count,
        "replay_rejection_count": counters.replay_rejection_count,
    }
    if tuple(observed) != PREREGISTERED_OBSERVED_METRIC_NAMES:
        print("EVALUATION REFUSED: emitted metrics drifted", file=sys.stderr)
        return 1
    if observed["openai_calls"] or observed["razorpay_http_calls"]:
        counters.violations.append("the evaluation made an external call")

    released = sum(
        item["amount_minor"]
        for item in outcomes
        if item["initial_action"] == "REVIEW" and item["final_action"] == "ALLOW"
    )
    summary = {
        "evaluation_id": frozen.plan["evaluation_id"],
        "classification": frozen.plan["classification"],
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "preregistration_commit_sha": commit_sha,
        "plan_canonical_sha256": frozen.plan_canonical_sha256,
        "plan_raw_file_sha256": frozen.plan_raw_file_sha256,
        "registry_sha256": frozen.registry_sha256,
        "evidence_policy_id": PRODUCT_EVIDENCE_POLICY.policy_id,
        "runtime_observed_counters": list(OBSERVED_COUNTER_NAMES),
        "observed_metrics": observed,
        "synthetic_transaction_value_moved_from_review_to_executable_allow_minor": (
            released
        ),
        "safety_violations": counters.violations,
        "case_status_counts": {
            status: sum(item["status"] == status for item in outcomes)
            for status in ("PASS", "UNRESOLVED_MISS", "SAFETY_VIOLATION", "PRECONDITION_FAILED")
        },
        "outcomes": outcomes,
        "claims_limit": (
            "Synthetic, non-benchmark, twenty independent cases. These outcomes "
            "do not establish generalization, and the reported synthetic "
            "transaction value is not revenue, GMV, or conversion lift."
        ),
    }

    output_root = REPOSITORY_ROOT / OUTPUT_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_bytes(
        (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    print(json.dumps(observed, indent=2, sort_keys=True))
    if counters.violations:
        for violation in counters.violations:
            print(f"SAFETY VIOLATION: {violation}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
