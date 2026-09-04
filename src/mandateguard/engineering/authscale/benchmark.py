"""Run the real MandateGuard authorization path over the synthetic universe.

There is no second controller here. Every case goes through the same three
components a production request would:

    authorize_transaction        Tier A/B, and Tier C only after a deterministic
                                 ALLOW
    issue_execution_authorization  capability issuance, ALLOW-only
    execute_razorpay_order       the execution gate, then at most one provider
                                 dispatch under the mandate-state guard

The provider client is a counting stub. It performs no network I/O and exists so
that "how many provider calls happened, and on which decisions" is a measured
number rather than an assurance. A call it records is a call the architecture
*would* have made.

Pipeline outcome, not controller outcome, is what the frozen taxonomy labels.
A gate family's controller decision is ALLOW by construction; its pipeline
outcome is BLOCK because the gate refuses it. Both are recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import gc
from hashlib import sha256
import platform
import sys
from time import perf_counter
from typing import Any, Iterable, Sequence

from mandateguard.execution import (
    ExecutionRefusal,
    ExecutionReceipt,
    InMemoryMandateStateRegistry,
    RazorpayOrderRequest,
    RazorpayOrderResult,
    SQLiteExecutionLedger,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
    execute_razorpay_order,
    issue_execution_authorization,
)
from mandateguard.execution.signing import HMACSHA256Signer, HMACSHA256Verifier
from mandateguard.intelligence.offline import DeterministicSemanticModel
from mandateguard.models.decision import DecisionAction
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.cache import InMemorySemanticCache
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticVerifier
from mandateguard.engineering.authscale.universe import (
    FIXED_CLOCK,
    SEED,
    WORLD_VERSION,
    AuthorizationCase,
    SyntheticMerchantUniverse,
)


BENCHMARK_VERSION = "authorization-scale-benchmark-v1"

#: Synthetic, in-process, and never a credential. The benchmark signs and
#: verifies with the same key because it is measuring the architecture's own
#: recomputation, not a key-distribution story.
BENCHMARK_SIGNING_KEY = sha256(f"{WORLD_VERSION}:{SEED}:signing".encode()).digest()
SIGNING_KEY_ID = "authorization-scale-key-v1"
CAPABILITY_LIFETIME = timedelta(minutes=2)


class CountingProviderClient:
    """Records provider dispatches. Performs no network I/O, ever."""

    __slots__ = ("calls", "requests")

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[RazorpayOrderRequest] = []

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        self.calls += 1
        self.requests.append(request)
        return RazorpayOrderResult(
            razorpay_order_id=f"order_synthetic_{self.calls:08d}",
            amount=request.amount,
            currency=request.currency,
            receipt=request.receipt,
            status="created",
        )


@dataclass(slots=True)
class CaseOutcome:
    case_id: str
    family: str
    expected_safe_actions: tuple[str, ...]
    controller_action: str
    pipeline_action: str
    agrees_with_target_invariant: bool
    capability_issued: bool
    #: Calls made by the submission this case is labelled on.
    provider_calls: int
    #: Calls made by a legitimate setup submission that precedes the labelled
    #: one. Only CAPABILITY_REPLAY has such a setup, and its call was authorized.
    setup_provider_calls: int
    refusal_reason: str | None
    authorization_ms: float


@dataclass(slots=True)
class _Counters:
    total_cases: int = 0
    allow: int = 0
    block: int = 0
    review: int = 0
    target_invariant_agreement: int = 0
    capabilities_issued: int = 0
    provider_adapter_calls: int = 0
    provider_calls_before_allow: int = 0
    provider_calls_on_block: int = 0
    provider_calls_on_review: int = 0
    replay_rejections: int = 0
    revocation_rejections: int = 0
    request_mutation_rejections: int = 0
    merchant_sku_mismatch_rejections: int = 0
    setup_provider_calls: int = 0
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    by_family: dict[str, dict[str, Any]] = field(default_factory=dict)


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return round(ordered[position], 4)


def _resident_memory_bytes() -> int | None:
    """Best-effort RSS without adding a dependency to read it."""

    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        query = kernel32.K32GetProcessMemoryInfo
        query.restype = wintypes.BOOL
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    return None


def _semantic_verifier() -> SemanticVerifier:
    """The shipped offline verifier. No network, no credential, no model API."""

    return SemanticVerifier(
        model=DeterministicSemanticModel(), cache=InMemorySemanticCache()
    )


def run_case(
    case: AuthorizationCase, *, ledger: SQLiteExecutionLedger
) -> CaseOutcome:
    """Drive one case through the real controller, capability, and gate."""

    verifier_needed = case.semantic_evidence is not None
    semantic_verifier = _semantic_verifier() if verifier_needed else None

    started = perf_counter()
    result = authorize_transaction(
        mandate=case.mandate,
        transaction=case.transaction,
        catalog_snapshot=case.catalog_snapshot,
        server_time=FIXED_CLOCK,
        nonce_state=case.nonce_state,
        committed_hashes=case.committed_hashes,
        replay_seed=SEED,
        evaluated_at=FIXED_CLOCK,
        semantic_evidence=case.semantic_evidence,
        semantic_verifier=semantic_verifier,
    )
    controller_action = result.final_action.name

    scenario = ReplayScenario(
        mandate=case.mandate,
        transaction=case.transaction,
        catalog_snapshot=case.catalog_snapshot,
        server_time=FIXED_CLOCK,
        nonce_state=case.nonce_state,
        psp_committed_hashes=case.committed_hashes,
        replay_seed=SEED,
        evaluated_at=FIXED_CLOCK,
    )
    config = TrustedExecutionConfig(
        merchant_id=case.merchant_id,
        account_scope="synthetic-merchant-universe-account-scope",
    )
    signer = HMACSHA256Signer(key_id=SIGNING_KEY_ID, key=BENCHMARK_SIGNING_KEY)
    registry = InMemoryMandateStateRegistry()
    registry.register_active(case.mandate.payload.mandate_id, 1, updated_at=FIXED_CLOCK)

    decision_nonce = f"dn_{sha256(case.case_id.encode()).hexdigest()[:24]}"
    capability = issue_execution_authorization(
        authorization_result=result,
        authorization_scenario=scenario,
        semantic_evidence=case.semantic_evidence,
        semantic_verifier=semantic_verifier,
        issued_at=FIXED_CLOCK,
        expires_at=FIXED_CLOCK + CAPABILITY_LIFETIME,
        decision_nonce=decision_nonce,
        config=config,
        signer=signer,
        mandate_state_registry=registry,
    )

    provider = CountingProviderClient()
    replay_setup_provider_calls = 0
    refusal_reason: str | None = None
    capability_issued = isinstance(capability, SignedExecutionAuthorization)
    pipeline_action = controller_action

    if not capability_issued:
        assert isinstance(capability, ExecutionRefusal)
        refusal_reason = capability.reason.name
    else:
        # The world's post-issuance mutation happens here, after a capability
        # exists and before the gate recomputes anything.
        gate_transaction = case.gate_transaction or case.transaction
        gate_now = FIXED_CLOCK
        if case.gate_mutation == "MANDATE_REVOKED_AFTER_ISSUANCE":
            registry.revoke(
                case.mandate.payload.mandate_id, 1, revoked_at=FIXED_CLOCK
            )
        elif case.gate_mutation == "MANDATE_VERSION_SUPERSEDED_AFTER_ISSUANCE":
            registry.supersede(
                case.mandate.payload.mandate_id,
                1,
                superseded_by_version=2,
                updated_at=FIXED_CLOCK,
            )
        elif case.gate_mutation == "SUBMITTED_AT_EXACT_EXPIRY_BOUNDARY":
            gate_now = FIXED_CLOCK + CAPABILITY_LIFETIME

        execution_verifier = HMACSHA256Verifier({SIGNING_KEY_ID: BENCHMARK_SIGNING_KEY})

        def dispatch(transaction, now):
            return execute_razorpay_order(
                authorization=capability,
                authorization_result=result,
                mandate=case.mandate,
                transaction=transaction,
                now=now,
                config=config,
                verifier=execution_verifier,
                ledger=ledger,
                client=provider,
                mandate_state_registry=registry,
            )

        outcome = dispatch(gate_transaction, gate_now)
        if case.gate_mutation == "CAPABILITY_NONCE_SUBMITTED_TWICE":
            # The first submission is legitimate and its provider call is
            # authorized. The replay is the second submission, and it is the one
            # this family is labelled on, so the counter is reset first: a call
            # made by the legitimate setup must never be reported as a call on a
            # blocked decision.
            setup_calls = provider.calls
            provider = CountingProviderClient()
            outcome = dispatch(gate_transaction, gate_now)
            replay_setup_provider_calls = setup_calls

        if isinstance(outcome, ExecutionRefusal):
            refusal_reason = outcome.reason.name
            pipeline_action = "BLOCK"
        elif isinstance(outcome, ExecutionReceipt):
            pipeline_action = "ALLOW"
        else:  # pragma: no cover - the executor returns one of the two
            raise RuntimeError("unexpected executor return type")

    authorization_ms = (perf_counter() - started) * 1000.0
    agrees = pipeline_action in case.expected_safe_actions
    return CaseOutcome(
        case_id=case.case_id,
        family=case.family,
        expected_safe_actions=case.expected_safe_actions,
        controller_action=controller_action,
        pipeline_action=pipeline_action,
        agrees_with_target_invariant=agrees,
        capability_issued=capability_issued,
        provider_calls=provider.calls,
        setup_provider_calls=replay_setup_provider_calls,
        refusal_reason=refusal_reason,
        authorization_ms=authorization_ms,
    )


def run_benchmark(*, case_count: int, record_disagreements: int = 25) -> dict[str, Any]:
    """Execute the ladder rung of ``case_count`` cases and summarize it."""

    universe = SyntheticMerchantUniverse(case_count=case_count)
    ledger = SQLiteExecutionLedger(":memory:")
    counters = _Counters()
    latencies: list[float] = []

    gc.collect()
    baseline_memory = _resident_memory_bytes()
    wall_started = perf_counter()

    for case in universe.cases():
        outcome = run_case(case, ledger=ledger)
        counters.total_cases += 1
        latencies.append(outcome.authorization_ms)

        if outcome.pipeline_action == "ALLOW":
            counters.allow += 1
        elif outcome.pipeline_action == "BLOCK":
            counters.block += 1
        else:
            counters.review += 1

        if outcome.agrees_with_target_invariant:
            counters.target_invariant_agreement += 1
        elif len(counters.disagreements) < record_disagreements:
            counters.disagreements.append(
                {
                    "case_id": outcome.case_id,
                    "family": outcome.family,
                    "expected_safe_actions": list(outcome.expected_safe_actions),
                    "controller_action": outcome.controller_action,
                    "pipeline_action": outcome.pipeline_action,
                    "refusal_reason": outcome.refusal_reason,
                }
            )

        if outcome.capability_issued:
            counters.capabilities_issued += 1
        counters.provider_adapter_calls += (
            outcome.provider_calls + outcome.setup_provider_calls
        )
        counters.setup_provider_calls += outcome.setup_provider_calls
        if outcome.pipeline_action == "BLOCK":
            counters.provider_calls_on_block += outcome.provider_calls
        elif outcome.pipeline_action == "REVIEW":
            counters.provider_calls_on_review += outcome.provider_calls

        reason = outcome.refusal_reason or ""
        if outcome.family == "CAPABILITY_REPLAY" and outcome.pipeline_action == "BLOCK":
            counters.replay_rejections += 1
        if outcome.family == "MANDATE_REVOKED" and outcome.pipeline_action == "BLOCK":
            counters.revocation_rejections += 1
        if outcome.family == "REQUEST_MUTATION" and outcome.pipeline_action == "BLOCK":
            counters.request_mutation_rejections += 1
        if (
            outcome.family in {"WRONG_MERCHANT", "WRONG_SKU"}
            and outcome.pipeline_action == "BLOCK"
        ):
            counters.merchant_sku_mismatch_rejections += 1

        family = counters.by_family.setdefault(
            outcome.family,
            {
                "cases": 0,
                "agreed": 0,
                "expected_safe_actions": list(outcome.expected_safe_actions),
                "controller_actions": {},
                "pipeline_actions": {},
                "provider_calls": 0,
                "authorized_setup_provider_calls": 0,
                "refusal_reasons": {},
            },
        )
        family["cases"] += 1
        family["agreed"] += int(outcome.agrees_with_target_invariant)
        family["controller_actions"][outcome.controller_action] = (
            family["controller_actions"].get(outcome.controller_action, 0) + 1
        )
        family["pipeline_actions"][outcome.pipeline_action] = (
            family["pipeline_actions"].get(outcome.pipeline_action, 0) + 1
        )
        family["provider_calls"] += outcome.provider_calls
        family["authorized_setup_provider_calls"] += outcome.setup_provider_calls
        if reason:
            family["refusal_reasons"][reason] = (
                family["refusal_reasons"].get(reason, 0) + 1
            )

    wall_seconds = perf_counter() - wall_started
    loaded_memory = _resident_memory_bytes()

    # A provider call "before ALLOW" is one made on a case whose pipeline
    # outcome was not ALLOW. That is the invariant, stated as a count.
    counters.provider_calls_before_allow = (
        counters.provider_calls_on_block + counters.provider_calls_on_review
    )

    return {
        "benchmark_version": BENCHMARK_VERSION,
        "world_generation_version": WORLD_VERSION,
        "seed": SEED,
        "fixed_clock": FIXED_CLOCK.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "case_count": case_count,
        "merchant_count": universe.merchant_count,
        "case_descriptor_stream_sha256": universe.descriptor_stream_sha256(),
        "label_source": "construction recipe; the controller never labelled a case",
        "counters": {
            "total_cases": counters.total_cases,
            "target_invariant_agreement": counters.target_invariant_agreement,
            "target_invariant_agreement_rate": round(
                counters.target_invariant_agreement / max(1, counters.total_cases), 6
            ),
            "authorizations_per_second": round(case_count / wall_seconds, 2),
            "capabilities_issued": counters.capabilities_issued,
            "provider_adapter_calls": counters.provider_adapter_calls,
            "authorized_setup_provider_calls": counters.setup_provider_calls,
            "provider_calls_before_allow": counters.provider_calls_before_allow,
            "provider_calls_on_block": counters.provider_calls_on_block,
            "provider_calls_on_review": counters.provider_calls_on_review,
            "replay_rejections": counters.replay_rejections,
            "revocation_rejections": counters.revocation_rejections,
            "request_mutation_rejections": counters.request_mutation_rejections,
            "merchant_sku_mismatch_rejections": counters.merchant_sku_mismatch_rejections,
            "resident_memory_bytes": loaded_memory,
            "resident_memory_before_bytes": baseline_memory,
        },
        "actions": {
            "ALLOW": counters.allow,
            "BLOCK": counters.block,
            "REVIEW": counters.review,
        },
        "authorization_latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": round(max(latencies), 4) if latencies else 0.0,
        },
        "wall_seconds": round(wall_seconds, 3),
        "by_family": dict(sorted(counters.by_family.items())),
        "disagreements": counters.disagreements,
        "external_calls": {"openai": 0, "razorpay_http": 0, "hugging_face_api": 0},
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "python": sys.version.split()[0],
            "process_count": 1,
        },
        "scope_limit": (
            "One process, one machine, sequential, no concurrency and no network. "
            "The merchant universe is synthetic and is not a merchant network. "
            "Nothing here is a distributed-throughput measurement."
        ),
    }
