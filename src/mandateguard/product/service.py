"""Thread-safe product composition around the frozen commerce controller."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import re
import secrets
from threading import Event, RLock, Thread
import tempfile
from typing import Any
from uuid import uuid4

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.execution import (
    HMACSHA256Signer,
    HMACSHA256Verifier,
    MandateStateBusyError,
    MandateStateCorruptionError,
    MandateStatus,
    RazorpayTestOrdersAdapter,
    SQLiteExecutionLedger,
    SQLiteMandateStateRegistry,
    TrustedExecutionConfig,
    build_razorpay_order_request,
    execution_request_sha256,
    issue_execution_authorization,
)
from mandateguard.execution.executor import execute_razorpay_order
from mandateguard.execution.models import (
    ExecutionLedgerStatus,
    ExecutionReceipt,
    ExecutionRefusal,
    ExecutionRefusalReason,
    RazorpayOrderRequest,
    RazorpayOrderResult,
)
from mandateguard.execution.signing import SignatureVerification
from mandateguard.intelligence.buyer import (
    CommerceBuyer,
    OpenAIResponsesBuyer,
    parse_offline_intent,
)
from mandateguard.intelligence.cache.semantic_cache import SQLiteSemanticCache
from mandateguard.intelligence.models import (
    BuyerOutput,
    InterpretedPurchaseIntent,
    RetrievalResult,
    RetrievalSource,
)
from mandateguard.intelligence.offline import (
    DeterministicSemanticModel,
    ResponsesUsageCapture,
    TimedSemanticModel,
)
from mandateguard.intelligence.orchestration import (
    AgenticCheckoutResult,
    ExecutionRuntime,
    InsufficientEvidenceAuthorizationResult,
    run_agentic_checkout,
)
from mandateguard.intelligence.retrieval import (
    DEFAULT_EMBEDDING_MODEL,
    HashingEmbeddingProvider,
    HybridRetriever,
    OpenAIEmbeddingProvider,
)
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.intelligence.tools import BuyerDraft, CommerceTools
from mandateguard.models.decision import DecisionAction
from mandateguard.models.finding import TierACheckStatus
from mandateguard.models.mandate import SemanticConstraintFamily
from mandateguard.recovery import (
    complete_recovery_round,
    EvidenceKind,
    GapAnalysisStatus,
    link_execution_outcome,
    MAX_ACQUISITION_ROUNDS,
    MAX_NEW_EVIDENCE_ITEMS,
    RecoveryAuditStoreError,
    ReviewRecoveryState,
    reserve_recovery_round,
    SQLiteRecoveryAuditStore,
    create_review_recovery,
    validate_observed_counters,
)
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic import OpenAIResponsesSemanticModel, SemanticVerifier
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    semantic_evidence_sha256,
)
from mandateguard.semantic.models import ConstraintStatus

from mandateguard.product.evidence import (
    FAILURE_RECOVERY_EVIDENCE,
    INT3_RESEARCH_FINDING,
)
from mandateguard.product.evidence_policy import (
    EvidencePolicy,
    PRODUCT_EVIDENCE_POLICY,
)
from mandateguard.product.discovery_service import DiscoverySurface
from mandateguard.product.recovery_config import build_recovery_registry
from mandateguard.product.scale_evidence import (
    load_model_quality,
    load_scale_evidence,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_NO_EXCLUSION_RE = re.compile(r"\bno\s+([^.;]+)", re.IGNORECASE)
_TERMINAL_STATES = frozenset({"COMPLETE", "ERROR"})
_MAX_RETAINED_RUNS = 256
RECOVERY_AUDIT_UNAVAILABLE = "RECOVERY_AUDIT_UNAVAILABLE"
DEMO_PRESETS: tuple[dict[str, str], ...] = (
    {
        "id": "safe",
        "label": "SAFE PURCHASE",
        "intent": (
            "Buy me a study lamp under ₹2,000 for individual study. "
            "No subscriptions."
        ),
    },
    {
        "id": "block",
        "label": "POLICY VIOLATION",
        "intent": (
            "Buy the Market Edge Decision Course under ₹3,000 for professional "
            "development. No gambling-related products. SKU: market-edge-course"
        ),
    },
    {
        "id": "review",
        "label": "AMBIGUOUS EVIDENCE",
        "intent": (
            "Buy the Flexi Desk Companion under ₹2,000 for individual study. "
            "No subscriptions. SKU: flexi-desk-companion"
        ),
    },
    {
        "id": "recoverable",
        "label": "RECOVERABLE REVIEW",
        "intent": (
            "Buy the Aurora Focus Lamp under ₹2,000 for individual study. "
            "No subscriptions. SKU: aurora-focus-lamp"
        ),
    },
)
REVOCATION_DEMO_PRESET: dict[str, str] = {
    "id": "revoked-after-allow",
    "label": "REVOKED AFTER ALLOW",
    "intent": (
        "Buy the StudyGlow Desk Lamp under ₹2,000 for individual study. "
        "No subscriptions. SKU: studyglow-desk-lamp"
    ),
}
PRODUCT_PRESETS = DEMO_PRESETS + (REVOCATION_DEMO_PRESET,)
_PRESETS_BY_ID = {item["id"]: item for item in PRODUCT_PRESETS}


# Resolve evaluation scenarios. Each one is an ordinary product intent run at the
# product default evidence policy; none of them carries an evidence override.
# `RR-BLOCK-SIGNAL-EDGE` is not a demo preset because the judge-facing
# `POLICY VIOLATION` preset must keep reaching BLOCK on its first evaluation.
RESOLVE_EVALUATION_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "case_id": "RR-ALLOW-AURORA",
        "merchant_id": "merchant-lumen",
        "sku": "aurora-focus-lamp",
        "intent": _PRESETS_BY_ID["recoverable"]["intent"],
    },
    {
        "case_id": "RR-BLOCK-SIGNAL-EDGE",
        "merchant_id": "merchant-veritas",
        "sku": "signal-edge-workshop",
        "intent": (
            "Buy the Signal Edge Workshop under ₹3,000 for professional "
            "development. No gambling-related products. SKU: signal-edge-workshop"
        ),
    },
    {
        "case_id": "RR-REVIEW-FLEXI",
        "merchant_id": "merchant-nova",
        "sku": "flexi-desk-companion",
        "intent": _PRESETS_BY_ID["review"]["intent"],
    },
)


TIMELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("USER_MANDATE", "User mandate"),
    ("AI_BUYER", "AI buyer"),
    ("PRODUCT", "Product"),
    ("EVIDENCE_RETRIEVAL", "Evidence retrieval"),
    ("DETERMINISTIC_VERIFICATION", "Deterministic verification"),
    ("SEMANTIC_VERIFICATION", "Semantic verification"),
    ("AUTHORIZATION", "Authorization"),
    ("EXECUTION", "Execution"),
)


_TIER_A_LABELS = {
    "A1": "Authoritative price",
    "A2": "SKU ownership",
    "A3": "Merchant binding",
    "A4": "Single-use mandate nonce",
    "A5": "Mandate expiry",
    "A6": "Transaction and catalog commitments",
    "A7": "Quantity, total, and transaction binding",
    "A8": "Catalog recurrence",
}
_TIER_B_LABELS = {
    "B1": "Line arithmetic",
    "B2": "Aggregate quantity",
    "B3": "Currency consistency",
    "B4": "Recurrence consistency",
    "B5": "Transaction commitment",
    "B6": "Price ceiling",
    "B7": "Quantity ceiling",
    "B8": "Recurrence permission",
    "B9": "Merchant allowlist",
    "B10": "SKU allowlist",
}


def _utc_now_text() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _load_local_environment(path: Path) -> None:
    """Load a simple local .env without replacing process configuration."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _offline_interpret(user_intent: str) -> InterpretedPurchaseIntent:
    """Extend the existing offline buyer parser with the brief's `No ...` form."""

    normalized_budget = re.sub(r"(?<=\d),(?=\d{3}\b)", "", user_intent)
    interpreted = parse_offline_intent(normalized_budget)
    exclusions = list(interpreted.exclusions)
    existing = {item.casefold() for item in exclusions}
    for match in _NO_EXCLUSION_RE.finditer(user_intent):
        for part in re.split(r",|\band\b", match.group(1), flags=re.IGNORECASE):
            item = re.sub(r"^no\s+", "", part.strip(" -"), flags=re.IGNORECASE)
            if item and item.casefold() not in existing:
                exclusions.append(item)
                existing.add(item.casefold())
    return InterpretedPurchaseIntent(
        max_total_minor=interpreted.max_total_minor,
        quantity=interpreted.quantity,
        currency=interpreted.currency,
        purpose=interpreted.purpose,
        recurring_allowed=interpreted.recurring_allowed,
        exclusions=tuple(exclusions),
        merchant_allowlist=interpreted.merchant_allowlist,
        sku_allowlist=interpreted.sku_allowlist,
    )


@dataclass(slots=True)
class CommerceRun:
    run_id: str
    request_id: str
    user_intent: str
    mode: str
    preset_id: str | None
    top_k: int
    defer_execution: bool = False
    state: str = "RUNNING"
    created_at: str = field(default_factory=_utc_now_text)
    updated_at: str = field(default_factory=_utc_now_text)
    timeline: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": step_id,
                "label": label,
                "status": "WAITING",
                "detail": None,
            }
            for step_id, label in TIMELINE_STEPS
        ]
    )
    audit: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    private_context: Any = None
    replay_in_flight: bool = False
    recovery_in_flight: bool = False
    mandate_action_in_flight: bool = False
    completion: Event = field(default_factory=Event)
    lock: RLock = field(default_factory=RLock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "run_id": self.run_id,
                "request_id": self.request_id,
                "mode": self.mode,
                "preset_id": self.preset_id,
                "state": self.state,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "timeline": [dict(item) for item in self.timeline],
                "audit": [
                    {**item, "details": dict(item.get("details", {}))}
                    for item in self.audit
                ],
                "result": self.output,
                "error": dict(self.error) if self.error is not None else None,
            }


class RunRecorder:
    __slots__ = ("run",)

    def __init__(self, run: CommerceRun) -> None:
        self.run = run

    def step(self, step_id: str, status: str, detail: str | None = None) -> None:
        with self.run.lock:
            for item in self.run.timeline:
                if item["id"] == step_id:
                    item["status"] = status
                    item["detail"] = detail
                    self.run.updated_at = _utc_now_text()
                    return
        raise ValueError(f"unknown timeline step: {step_id}")

    def audit(self, event: str, **details: Any) -> None:
        with self.run.lock:
            self.run.audit.append(
                {
                    "sequence": len(self.run.audit) + 1,
                    "event": event,
                    "recorded_at": _utc_now_text(),
                    "details": details,
                }
            )
            self.run.updated_at = _utc_now_text()

    def tool(self, name: str) -> None:
        with self.run.lock:
            self.run.tool_calls.append(
                {
                    "sequence": len(self.run.tool_calls) + 1,
                    "name": name,
                }
            )


class ObservedCommerceTools(CommerceTools):
    __slots__ = ("_observe",)

    def __init__(
        self, store: TrustedCommerceStore, observe: Callable[[str], None]
    ) -> None:
        super().__init__(store)
        object.__setattr__(self, "_observe", observe)

    def dispatch(self, name: str, arguments: object) -> Mapping[str, Any] | BuyerDraft:
        self._observe(name)
        return super().dispatch(name, arguments)


class ToolDrivenOfflineBuyer:
    """Deterministic buyer that genuinely uses the existing narrow tool boundary."""

    __slots__ = ("model_id", "tools")

    def __init__(self, tools: ObservedCommerceTools) -> None:
        self.model_id = "offline-deterministic-buyer-v1"
        self.tools = tools

    def purchase(self, user_intent: str) -> BuyerOutput:
        interpreted = _offline_interpret(user_intent)
        max_unit_price = interpreted.max_total_minor // interpreted.quantity
        search = self.tools.dispatch(
            "search_catalog",
            {
                "query": user_intent,
                "filters": {
                    "currency": interpreted.currency,
                    "max_unit_price_minor": max_unit_price,
                    "merchant_ids": (
                        list(interpreted.merchant_allowlist)
                        if interpreted.merchant_allowlist is not None
                        else None
                    ),
                    "sku_ids": (
                        list(interpreted.sku_allowlist)
                        if interpreted.sku_allowlist is not None
                        else None
                    ),
                    "recurring": None if interpreted.recurring_allowed else False,
                    "limit": 10,
                },
            },
        )
        if not isinstance(search, Mapping):
            raise RuntimeError("offline catalog search returned an invalid result")
        products = search.get("products")
        if not isinstance(products, list) or not products:
            raise RuntimeError("no registered product satisfies the hard discovery filters")
        selected = products[0]
        merchant_id = selected["merchant_id"]
        sku = selected["sku"]
        self.tools.dispatch("get_product", {"merchant_id": merchant_id, "sku": sku})
        evidence_result = self.tools.dispatch(
            "get_merchant_evidence", {"merchant_id": merchant_id, "sku": sku}
        )
        if not isinstance(evidence_result, Mapping):
            raise RuntimeError("offline evidence lookup returned an invalid result")
        evidence = evidence_result.get("evidence")
        if not isinstance(evidence, list):
            raise RuntimeError("offline evidence lookup returned an invalid result")
        draft = self.tools.dispatch(
            "propose_purchase",
            {
                "interpreted_intent": interpreted.to_mapping(),
                "proposal": {
                    "merchant_id": merchant_id,
                    "sku": sku,
                    "quantity": interpreted.quantity,
                    "declared_total_minor": (
                        selected["effective_unit_price_minor"] * interpreted.quantity
                    ),
                    "currency": selected["currency"],
                    "reason": (
                        "Highest lexical catalog match within the interpreted hard "
                        "filters."
                    ),
                    "selected_evidence_ids": [
                        item["evidence_id"] for item in evidence
                    ],
                    "user_intent_summary": user_intent.strip()[:1000],
                },
            },
        )
        if not isinstance(draft, BuyerDraft):
            raise RuntimeError("offline buyer did not produce a typed proposal")
        return BuyerOutput(
            proposal=draft.proposal,
            interpreted_intent=draft.interpreted_intent,
            model_id=self.model_id,
        )


class ObservedBuyer:
    __slots__ = ("delegate", "model_id", "on_complete")

    def __init__(
        self, delegate: CommerceBuyer, on_complete: Callable[[BuyerOutput], None]
    ) -> None:
        self.delegate = delegate
        self.model_id = delegate.model_id
        self.on_complete = on_complete

    def purchase(self, user_intent: str) -> BuyerOutput:
        output = self.delegate.purchase(user_intent)
        self.on_complete(output)
        return output


class ObservedHybridRetriever(HybridRetriever):
    __slots__ = ("_on_start", "_on_complete")

    def __init__(
        self,
        embedding_provider: object,
        *,
        on_start: Callable[[], None],
        on_complete: Callable[[RetrievalResult], None],
    ) -> None:
        super().__init__(embedding_provider)
        object.__setattr__(self, "_on_start", on_start)
        object.__setattr__(self, "_on_complete", on_complete)

    def retrieve(self, **kwargs: Any) -> RetrievalResult:
        self._on_start()
        result = super().retrieve(**kwargs)
        self._on_complete(result)
        return result


class ObservedSemanticModel:
    __slots__ = ("delegate", "model_id", "on_start")

    def __init__(
        self,
        delegate: object,
        on_start: Callable[[], None],
    ) -> None:
        self.delegate = delegate
        self.model_id = delegate.model_id
        self.on_start = on_start

    def evaluate(self, request: object) -> object:
        self.on_start()
        return self.delegate.evaluate(request)

    @property
    def last_latency_ms(self) -> float:
        return float(getattr(self.delegate, "last_latency_ms", 0.0))

    @property
    def last_input_tokens(self) -> int | None:
        return getattr(self.delegate, "last_input_tokens", None)

    @property
    def last_output_tokens(self) -> int | None:
        return getattr(self.delegate, "last_output_tokens", None)


class OperationalCounters:
    """Counters incremented by real call sites, never derived from ``run.mode``.

    An accidental OpenAI, Razorpay, or trusted-provider call on a path that is
    supposed to be offline increments the matching counter here, so the
    evaluation can fail the run instead of structurally assuming zero.
    """

    __slots__ = (
        "openai_calls",
        "razorpay_http_calls",
        "offline_adapter_calls",
        "trusted_evidence_provider_calls",
        "_lock",
    )

    def __init__(self) -> None:
        self.openai_calls = 0
        self.razorpay_http_calls = 0
        self.offline_adapter_calls = 0
        self.trusted_evidence_provider_calls = 0
        self._lock = RLock()

    def record_openai_call(self) -> None:
        with self._lock:
            self.openai_calls += 1

    def record_razorpay_http_call(self) -> None:
        with self._lock:
            self.razorpay_http_calls += 1

    def record_offline_adapter_call(self) -> None:
        with self._lock:
            self.offline_adapter_calls += 1

    def record_trusted_evidence_provider_call(self) -> None:
        with self._lock:
            self.trusted_evidence_provider_calls += 1


class ObservedCreateResource:
    """Count one external API call for every delegated ``create`` invocation."""

    __slots__ = ("delegate", "observe")

    def __init__(self, delegate: object, observe: Callable[[], None]) -> None:
        if not callable(getattr(delegate, "create", None)):
            raise TypeError("delegate must expose create")
        self.delegate = delegate
        self.observe = observe

    def create(self, **kwargs: object) -> object:
        self.observe()
        return self.delegate.create(**kwargs)


class OfflineTestOrdersClient:
    """Network-free result double behind the real D6 gate."""

    __slots__ = ("calls", "_lock")

    def __init__(self) -> None:
        self.calls: list[RazorpayOrderRequest] = []
        self._lock = RLock()

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        with self._lock:
            self.calls.append(request)
        order_suffix = sha256(request.receipt.encode("ascii")).hexdigest()[:16]
        return RazorpayOrderResult(
            razorpay_order_id=f"order_offline_{order_suffix}",
            amount=request.amount,
            currency=request.currency,
            receipt=request.receipt,
            status="created",
        )


class ObservedOrdersClient:
    __slots__ = (
        "delegate",
        "external",
        "adapter_calls",
        "external_network_calls",
        "counters",
        "on_start",
        "on_complete",
        "on_error",
        "_lock",
    )

    def __init__(
        self,
        delegate: object,
        *,
        counters: OperationalCounters,
        on_start: Callable[[], None],
        on_complete: Callable[[], None],
        on_error: Callable[[], None],
    ) -> None:
        self.delegate = delegate
        # Observed from the adapter actually installed, not from the run mode: a
        # real Razorpay adapter on a supposedly offline path still counts.
        self.external = isinstance(delegate, RazorpayTestOrdersAdapter)
        self.adapter_calls = 0
        self.external_network_calls = 0
        self.counters = counters
        self.on_start = on_start
        self.on_complete = on_complete
        self.on_error = on_error
        self._lock = RLock()

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        with self._lock:
            self.adapter_calls += 1
            if self.external:
                self.external_network_calls += 1
        if self.external:
            self.counters.record_razorpay_http_call()
        else:
            self.counters.record_offline_adapter_call()
        self.on_start()
        try:
            result = self.delegate.create_order(request)
        except BaseException:
            self.on_error()
            raise
        self.on_complete()
        return result


@dataclass(slots=True)
class _RunContext:
    checkout: AgenticCheckoutResult
    execution_runtime: ExecutionRuntime
    client: ObservedOrdersClient
    evaluated_at: datetime
    store: TrustedCommerceStore
    semantic_verifier: SemanticVerifier
    semantic_evidence: SemanticEvidence | None
    operational_counters: OperationalCounters
    recovery_registry: Any
    trust_configuration: dict[str, Any]
    recovery_state: ReviewRecoveryState | None = None
    payment_provider_calls_before_final_allow: int | None = None
    recovery_audit_state: str = "AVAILABLE"


class CommerceLabService:
    """Own product runs without adding any authorization decision logic."""

    default_mode = "offline"

    def __init__(
        self,
        *,
        repository_root: Path = REPOSITORY_ROOT,
        state_dir: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        evidence_policy: EvidencePolicy = PRODUCT_EVIDENCE_POLICY,
    ) -> None:
        self.repository_root = repository_root
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if not isinstance(evidence_policy, EvidencePolicy):
            raise TypeError("evidence_policy must be EvidencePolicy")
        self.evidence_policy = evidence_policy
        _load_local_environment(repository_root / ".env")
        state_dir, state_persistence = self._resolve_state_dir(state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = state_dir
        self.state_persistence = state_persistence
        self.store = TrustedCommerceStore.from_files(
            catalog_path=repository_root
            / "fixtures"
            / "agentic_commerce"
            / "merchant_catalog.json",
            merchant_terms_path=repository_root
            / "fixtures"
            / "agentic_commerce"
            / "merchant_terms.json",
        )
        self.recovery_registry = build_recovery_registry(repository_root)
        # The discovery catalog is optional: the product must start and serve
        # every authorization journey whether or not the large catalog and its
        # frozen indexes were built into this deployment.
        self.discovery = DiscoverySurface(
            processed_dir=repository_root / "data" / "processed",
            models_dir=repository_root / "data" / "models",
            store=self.store,
        )
        # Read once, at startup, from the artifacts that recorded them.
        self.scale_evidence = load_scale_evidence(repository_root=repository_root)
        self.model_quality = load_model_quality(repository_root=repository_root)
        self.semantic_cache = SQLiteSemanticCache(state_dir / "semantic-cache.sqlite3")
        self.recovery_audit_store = SQLiteRecoveryAuditStore(
            state_dir / "recovery-audit.sqlite3"
        )
        self.execution_ledger = SQLiteExecutionLedger(
            state_dir / "execution-ledger.sqlite3"
        )
        self.mandate_state_registry = SQLiteMandateStateRegistry(
            state_dir / "mandate-state.sqlite3"
        )
        self._offline_signing_key = secrets.token_bytes(32)
        self._runs: OrderedDict[str, CommerceRun] = OrderedDict()
        self._requests: dict[str, tuple[str, str, str, str | None, bool]] = {}
        self._lock = RLock()

    @staticmethod
    def _resolve_state_dir(state_dir: Path | None) -> tuple[Path, str]:
        """Prefer an explicit directory, then `MANDATEGUARD_STATE_DIR`, then temp.

        A configured directory keeps the semantic cache, the execution ledger,
        and the recovery audit chain across service reopens on the same
        filesystem. Without one, the local state is temporary, which is
        acceptable for development and the offline demo.
        """

        if state_dir is not None:
            return Path(state_dir), "CONFIGURED_DIRECTORY"
        configured = os.environ.get("MANDATEGUARD_STATE_DIR", "").strip()
        if configured:
            return Path(configured), "CONFIGURED_DIRECTORY"
        return (
            Path(tempfile.mkdtemp(prefix="mandateguard-commerce-lab-")),
            "EPHEMERAL_TEMPORARY_DIRECTORY",
        )

    def trust_configuration(self, *, top_k: int | None = None) -> dict[str, Any]:
        """Return the trust-sensitive configuration a run at `top_k` would use."""

        return self.evidence_policy.describe(
            top_k=self.evidence_policy.top_k if top_k is None else top_k,
            registry_sha256=self.recovery_registry.registry_sha256,
            semantic_mode="LIVE",
        )

    def close(self) -> None:
        self.semantic_cache.close()
        self.recovery_audit_store.close()
        self.execution_ledger.close()
        self.mandate_state_registry.close()

    def __enter__(self) -> CommerceLabService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def live_configuration(self) -> dict[str, Any]:
        required = (
            "OPENAI_API_KEY",
            "MANDATEGUARD_SEMANTIC_MODEL",
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "MANDATEGUARD_EXECUTION_HMAC_KEY",
        )
        missing = [name for name in required if not os.environ.get(name)]
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        hmac_key = os.environ.get("MANDATEGUARD_EXECUTION_HMAC_KEY", "")
        problems: list[str] = []
        if key_id and not key_id.startswith("rzp_test_"):
            problems.append("RAZORPAY_KEY_ID must use the rzp_test_ prefix")
        if hmac_key and len(hmac_key.encode("utf-8")) < 32:
            problems.append("MANDATEGUARD_EXECUTION_HMAC_KEY must be at least 32 bytes")
        if importlib.util.find_spec("openai") is None:
            problems.append("OpenAI Python package is not installed")
        available = not missing and not problems
        return {
            "available": available,
            "missing_configuration": missing,
            "problems": problems,
            "execution_environment": "RAZORPAY_TEST_MODE",
        }

    def public_config(self) -> dict[str, Any]:
        return {
            "product": "MANDATEGUARD",
            "thesis": "The agent decides. MandateGuard verifies. Razorpay executes.",
            "default_mode": self.default_mode,
            "modes": {
                "offline": {
                    "available": True,
                    "label": "OFFLINE DEMO MODE",
                    "description": (
                        "Deterministic local buyer, retrieval, verifier, capability, "
                        "ledger, and Test Mode-compatible order double. No network calls."
                    ),
                },
                "live": {
                    "label": "LIVE TEST MODE",
                    **self.live_configuration(),
                },
            },
            "presets": [dict(item) for item in PRODUCT_PRESETS],
            "research": dict(INT3_RESEARCH_FINDING),
            "failure_recovery": [dict(item) for item in FAILURE_RECOVERY_EVIDENCE],
            "resolve": {
                # Read straight off the server-owned evidence policy so the
                # judge-facing bounded-scale claim cannot drift from the policy
                # the controller actually applies.
                "top_k": self.evidence_policy.top_k,
                "alpha": float(self.evidence_policy.alpha),
                "max_acquisition_rounds": MAX_ACQUISITION_ROUNDS,
                "max_new_evidence_items": MAX_NEW_EVIDENCE_ITEMS,
                "planner": "DETERMINISTIC_CONSTRAINT_FAMILY_PLANNER_V1",
                "int3_runtime_use": "NOT_INTEGRATED",
                "evidence_policy_id": self.evidence_policy.policy_id,
                "state_persistence": self.state_persistence,
            },
            "safety": {
                "external_calls_on_page_load": 0,
                "buyer_has_razorpay_authority": False,
                "browser_receives_secrets": False,
                "mandate_state_owner": "TRUSTED_SERVER",
                "revocation_authority": "DEMO USER REVOCATION",
            },
            "discovery": self.discovery.public_config(),
            "system_scale": dict(self.scale_evidence),
            "model_quality": dict(self.model_quality),
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "mandateguard-commerce-lab",
            "default_mode": self.default_mode,
            "live_mode_available": self.live_configuration()["available"],
            "state_persistence": self.state_persistence,
            "discovery_catalog_available": self.discovery.available,
        }

    def discovery_search(self, *, intent: str, top_k: int = 6) -> dict[str, Any]:
        """Run one discovery search. No decision, no capability, no provider."""

        return self.discovery.search(intent, top_k=top_k)

    def discovery_select(
        self, *, intent: str, catalog_product_id: str
    ) -> dict[str, Any]:
        """Report what can be done with one discovered listing."""

        return self.discovery.select(intent, catalog_product_id)

    def start_run(
        self,
        *,
        user_intent: str,
        mode: str,
        request_id: str,
        preset_id: str | None = None,
        top_k: int | None = None,
        defer_execution: bool | None = None,
    ) -> tuple[CommerceRun, bool]:
        if not isinstance(user_intent, str) or not user_intent.strip():
            raise ValueError("user_intent must be a non-empty string")
        if len(user_intent) > 4000:
            raise ValueError("user_intent must contain at most 4000 characters")
        if mode not in {"offline", "live"}:
            raise ValueError("mode must be offline or live")
        if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("request_id must be a bounded identifier")
        if preset_id is not None and preset_id not in _PRESETS_BY_ID:
            raise ValueError("preset_id is not registered")
        if defer_execution is not None and not isinstance(defer_execution, bool):
            raise TypeError("defer_execution must be boolean or None")
        # `preset_id` selects an intent and nothing else. The evidence policy is
        # server-owned; only an explicit engineering override may replace it, and
        # the override is recorded in the run's trust configuration.
        if top_k is None:
            top_k = self.evidence_policy.top_k
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        normalized_intent = user_intent.strip()
        should_defer = (
            preset_id == REVOCATION_DEMO_PRESET["id"]
            if defer_execution is None
            else defer_execution
        )
        if mode == "live" and not self.live_configuration()["available"]:
            raise RuntimeError("live test mode is unavailable; check server configuration")
        with self._lock:
            existing = self._requests.get(request_id)
            if existing is not None:
                (
                    run_id,
                    existing_mode,
                    existing_intent,
                    existing_preset,
                    existing_defer,
                ) = existing
                if (
                    existing_mode != mode
                    or existing_intent != normalized_intent
                    or existing_preset != preset_id
                    or existing_defer != should_defer
                ):
                    raise ValueError("request_id is already bound to another request")
                return self._runs[run_id], True
            run = CommerceRun(
                run_id="run_" + uuid4().hex,
                request_id=request_id,
                user_intent=normalized_intent,
                mode=mode,
                preset_id=preset_id,
                top_k=top_k,
                defer_execution=should_defer,
            )
            self._runs[run.run_id] = run
            self._requests[request_id] = (
                run.run_id,
                mode,
                normalized_intent,
                preset_id,
                should_defer,
            )
            self._evict_oldest_runs()
        Thread(target=self._execute_run, args=(run,), daemon=True).start()
        return run, False

    def run_sync(
        self,
        *,
        user_intent: str,
        mode: str = "offline",
        request_id: str | None = None,
        preset_id: str | None = None,
        top_k: int | None = None,
        defer_execution: bool | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        run, _ = self.start_run(
            user_intent=user_intent,
            mode=mode,
            request_id=request_id or ("test_" + uuid4().hex),
            preset_id=preset_id,
            top_k=top_k,
            defer_execution=defer_execution,
        )
        if not run.completion.wait(timeout_seconds):
            raise TimeoutError("commerce lab run did not finish")
        return run.snapshot()

    def _evict_oldest_runs(self) -> None:
        """Bound demo memory without touching any authorization decision."""

        while len(self._runs) > _MAX_RETAINED_RUNS:
            evicted_id, _ = self._runs.popitem(last=False)
            for key, entry in list(self._requests.items()):
                if entry[0] == evicted_id:
                    del self._requests[key]

    def get_run(self, run_id: str) -> CommerceRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def replay(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError("run was not found")
        with run.lock:
            if run.state != "COMPLETE" or run.private_context is None:
                raise RuntimeError("run is not ready for replay")
            if run.replay_in_flight:
                raise RuntimeError("replay is already in flight")
            context: _RunContext = run.private_context
            checkout = context.checkout
            if checkout.authorization_result.final_action is not DecisionAction.ALLOW:
                raise RuntimeError("only an ALLOW capability can be replay-tested")
            if checkout.execution_authorization is None:
                raise RuntimeError("run does not contain an execution capability")
            run.replay_in_flight = True
        try:
            calls_before = context.client.adapter_calls
            external_before = context.client.external_network_calls
            replay_result = execute_razorpay_order(
                authorization=checkout.execution_authorization,
                authorization_result=checkout.authorization_result,
                mandate=checkout.mandate,
                transaction=checkout.transaction,
                now=self._trusted_now(),
                config=context.execution_runtime.config,
                verifier=context.execution_runtime.verifier,
                ledger=context.execution_runtime.ledger,
                client=context.client,
                mandate_state_registry=(
                    context.execution_runtime.mandate_state_registry
                ),
            )
            calls_after = context.client.adapter_calls
            external_after = context.client.external_network_calls
            rejected_before_network = (
                isinstance(replay_result, ExecutionRefusal)
                and replay_result.reason is ExecutionRefusalReason.NONCE_ALREADY_USED
                and calls_after == calls_before
                and external_after == external_before
            )
            replay_payload = {
                "status": (
                    "REJECTED_BEFORE_NETWORK"
                    if rejected_before_network
                    else "UNEXPECTED_REPLAY_RESULT"
                ),
                "reason": (
                    replay_result.reason.value
                    if isinstance(replay_result, ExecutionRefusal)
                    else "EXECUTION_WAS_NOT_REFUSED"
                ),
                "razorpay_additional_calls": calls_after - calls_before,
                "external_additional_calls": external_after - external_before,
            }
            with run.lock:
                assert run.output is not None
                run.output["execution"]["replay"] = replay_payload
                run.audit.append(
                    {
                        "sequence": len(run.audit) + 1,
                        "event": "CAPABILITY_REPLAY_REJECTED",
                        "recorded_at": _utc_now_text(),
                        "details": {
                            "reason": replay_payload["reason"],
                            "additional_calls": replay_payload[
                                "razorpay_additional_calls"
                            ],
                        },
                    }
                )
                run.updated_at = _utc_now_text()
            return run.snapshot()
        finally:
            with run.lock:
                run.replay_in_flight = False

    def revoke_mandate(self, run_id: str) -> dict[str, Any]:
        """Apply the server-owned DEMO USER REVOCATION transition.

        Revocation only ever touches the mandate identity of *this* run. The
        identity is derived from the server-issued ``run_id``, so knowing one
        run cannot revoke another run's consent.

        If an execution for this mandate already holds the consent-state guard,
        the revocation queues behind it and commits once the guarded provider
        section returns. If that wait exceeds the registry's budget, this raises
        ``MandateStateBusyError``: the revocation is *not* committed, and the
        in-flight provider operation is neither cancelled nor retried.
        """

        run = self.get_run(run_id)
        if run is None:
            raise KeyError("run was not found")
        with run.lock:
            if run.state != "COMPLETE" or run.private_context is None:
                raise RuntimeError("run is not ready for mandate revocation")
            if run.mandate_action_in_flight:
                raise RuntimeError("another mandate action is already in flight")
            context: _RunContext = run.private_context
            capability = context.checkout.execution_authorization
            if capability is None:
                raise RuntimeError("run does not contain an execution capability")
            if isinstance(context.checkout.execution_result, ExecutionReceipt):
                raise RuntimeError("execution already reached the provider")
            run.mandate_action_in_flight = True

        try:
            current = self.mandate_state_registry.get_current(
                capability.payload.mandate_id
            )
            if current is None:
                raise RuntimeError("mandate state is missing")
            if current.status is not MandateStatus.REVOKED:
                state = self.mandate_state_registry.revoke(
                    capability.payload.mandate_id,
                    capability.payload.mandate_version,
                    revoked_at=self._trusted_now(),
                )
                recorder = RunRecorder(run)
                recorder.audit(
                    "MANDATE_REVOKED",
                    mandate_id=state.mandate_id,
                    mandate_version=state.version,
                    transition="ACTIVE -> REVOKED",
                    authority="DEMO USER REVOCATION",
                )
                recorder.step(
                    "EXECUTION",
                    "REVOKED",
                    "Current consent revoked; Razorpay calls: 0",
                )
            with run.lock:
                run.output = self._present_result(run, context)
                run.updated_at = _utc_now_text()
            return run.snapshot()
        finally:
            with run.lock:
                run.mandate_action_in_flight = False

    def attempt_execution(self, run_id: str) -> dict[str, Any]:
        """Present one deferred capability to the current-state execution gate."""

        run = self.get_run(run_id)
        if run is None:
            raise KeyError("run was not found")
        with run.lock:
            if run.state != "COMPLETE" or run.private_context is None:
                raise RuntimeError("run is not ready for execution")
            if run.mandate_action_in_flight:
                raise RuntimeError("another mandate action is already in flight")
            context: _RunContext = run.private_context
            checkout = context.checkout
            capability = checkout.execution_authorization
            if capability is None:
                raise RuntimeError("run does not contain an execution capability")
            if checkout.execution_result is not None:
                raise RuntimeError("capability has already been presented for execution")
            run.mandate_action_in_flight = True

        try:
            calls_before = context.client.adapter_calls
            external_before = context.client.external_network_calls
            result = execute_razorpay_order(
                authorization=capability,
                authorization_result=checkout.authorization_result,
                mandate=checkout.mandate,
                transaction=checkout.transaction,
                now=self._trusted_now(),
                config=context.execution_runtime.config,
                verifier=context.execution_runtime.verifier,
                ledger=context.execution_runtime.ledger,
                client=context.client,
                mandate_state_registry=context.execution_runtime.mandate_state_registry,
            )
            calls_after = context.client.adapter_calls
            external_after = context.client.external_network_calls
            context.checkout = replace(checkout, execution_result=result)
            recorder = RunRecorder(run)
            if isinstance(result, ExecutionRefusal):
                if (
                    result.reason
                    in {
                        ExecutionRefusalReason.MANDATE_REVOKED,
                        ExecutionRefusalReason.MANDATE_SUPERSEDED,
                        ExecutionRefusalReason.MANDATE_STATE_MISSING,
                        ExecutionRefusalReason.MANDATE_VERSION_MISMATCH,
                        ExecutionRefusalReason.MANDATE_ID_MISMATCH,
                    }
                    and (
                        calls_after != calls_before
                        or external_after != external_before
                    )
                ):
                    raise RuntimeError(
                        "mandate-state refusal occurred after provider activity"
                    )
                recorder.step(
                    "EXECUTION",
                    "REJECTED",
                    f"{result.reason.value}; Razorpay calls: {calls_after}",
                )
                recorder.audit(
                    "EXECUTION_REFUSED_MANDATE_STATE",
                    mandate_id=capability.payload.mandate_id,
                    mandate_version=capability.payload.mandate_version,
                    reason=result.reason.value,
                    decision_nonce=capability.payload.decision_nonce,
                    execution_request_sha256=(
                        capability.payload.execution_request_sha256
                    ),
                    authorization_result_sha256=(
                        capability.payload.authorization_result_sha256
                    ),
                    provider_additional_calls=calls_after - calls_before,
                )
            else:
                recorder.step("EXECUTION", "PASS", "Order creation response validated")
                recorder.audit(
                    "RAZORPAY_ORDER_CREATED",
                    order_id=result.razorpay_order_id,
                    environment=("OFFLINE_DEMO" if run.mode == "offline" else "TEST"),
                )
            with run.lock:
                run.output = self._present_result(run, context)
                run.updated_at = _utc_now_text()
            return run.snapshot()
        finally:
            with run.lock:
                run.mandate_action_in_flight = False

    def recover(self, run_id: str) -> dict[str, Any]:
        """Run one user-triggered trusted acquisition round for an existing REVIEW."""

        run = self.get_run(run_id)
        if run is None:
            raise KeyError("run was not found")
        with run.lock:
            if run.state != "COMPLETE" or run.private_context is None:
                raise RuntimeError("run is not ready for evidence acquisition")
            if run.recovery_in_flight:
                raise RuntimeError("evidence acquisition is already in flight")
            context: _RunContext = run.private_context
            if context.recovery_state is None:
                raise RuntimeError("NO_RECOVERABLE_GAP")
            if context.recovery_audit_state != "AVAILABLE":
                raise RuntimeError(RECOVERY_AUDIT_UNAVAILABLE)
            if context.recovery_state.final_action is not DecisionAction.REVIEW:
                raise RuntimeError("review has already been resolved")
            run.recovery_in_flight = True

        recorder = RunRecorder(run)
        previous_event_count = len(context.recovery_state.audit_events)
        try:
            recovery_time = self._trusted_now()
            payment_calls_before = context.client.adapter_calls
            reserved = reserve_recovery_round(
                state=context.recovery_state,
                registry=context.recovery_registry,
                recovery_started_at=recovery_time,
            )
            # The durable reservation is committed before provider acquisition.
            context.recovery_state = reserved
            self._append_recovery_audit(
                context, reserved.audit_events[previous_event_count:]
            )
            self._record_recovery_events(
                recorder,
                reserved.audit_events,
                start=previous_event_count,
            )
            acquisition_event_count = len(reserved.audit_events)
            catalog = context.store.catalog_snapshot(
                merchant_id=reserved.scenario.transaction.payload.merchant_id
            )
            recovered = complete_recovery_round(
                state=reserved,
                registry=context.recovery_registry,
                semantic_verifier=context.semantic_verifier,
                recovery_time=recovery_time,
                catalog_snapshot=catalog,
                nonce_state=reserved.scenario.nonce_state,
            )
            # Provenance is durable before any capability can be issued.
            self._append_recovery_audit(
                context, recovered.audit_events[acquisition_event_count:]
            )
            context.recovery_state = recovered
            context.evaluated_at = recovery_time
            capability = None
            execution_result = None
            if recovered.final_action is DecisionAction.ALLOW:
                if recovered.current_evidence is None:
                    raise RuntimeError("resolved ALLOW is missing its evidence set")
                decision_nonce = "mg_resolve_" + sha256(
                    f"{run.request_id}:{recovered.rounds_used}".encode("ascii")
                ).hexdigest()[:32]
                current_state = self.mandate_state_registry.get_current(
                    recovered.scenario.mandate.payload.mandate_id
                )
                mandate_version = (
                    1
                    if current_state is None
                    else current_state.version
                    if current_state.status is MandateStatus.ACTIVE
                    else current_state.version + 1
                )
                self.mandate_state_registry.register_active(
                    recovered.scenario.mandate.payload.mandate_id,
                    mandate_version,
                    updated_at=recovery_time,
                )
                issued = issue_execution_authorization(
                    authorization_result=recovered.current_authorization,
                    authorization_scenario=recovered.scenario,
                    semantic_evidence=recovered.current_evidence,
                    semantic_verifier=context.semantic_verifier,
                    issued_at=recovery_time,
                    expires_at=min(
                        recovery_time + timedelta(minutes=2),
                        recovered.scenario.mandate.payload.expires_at,
                    ),
                    decision_nonce=decision_nonce,
                    config=context.execution_runtime.config,
                    signer=context.execution_runtime.signer,
                    mandate_state_registry=self.mandate_state_registry,
                    mandate_version=mandate_version,
                )
                if isinstance(issued, ExecutionRefusal):
                    raise RuntimeError(
                        f"recovered authorization capability refused: {issued.reason.value}"
                    )
                capability = issued
                if not run.defer_execution:
                    execution_result = execute_razorpay_order(
                        authorization=capability,
                        authorization_result=recovered.current_authorization,
                        mandate=recovered.scenario.mandate,
                        transaction=recovered.scenario.transaction,
                        now=self._trusted_now(),
                        config=context.execution_runtime.config,
                        verifier=context.execution_runtime.verifier,
                        ledger=context.execution_runtime.ledger,
                        client=context.client,
                        mandate_state_registry=self.mandate_state_registry,
                    )
            context.checkout = replace(
                context.checkout,
                authorization_result=recovered.current_authorization,
                execution_authorization=capability,
                execution_result=execution_result,
                mandate_version=(
                    capability.payload.mandate_version
                    if capability is not None
                    else context.checkout.mandate_version
                ),
            )
            context.semantic_evidence = recovered.current_evidence
            context.payment_provider_calls_before_final_allow = payment_calls_before
            if capability is not None:
                linked = link_execution_outcome(
                    state=recovered,
                    registry=context.recovery_registry,
                    recorded_at=recovery_time,
                    decision_nonce=capability.payload.decision_nonce,
                    execution_request_sha256=(
                        capability.payload.execution_request_sha256
                    ),
                    execution_receipt_id=(
                        execution_result.razorpay_order_id
                        if isinstance(execution_result, ExecutionReceipt)
                        else None
                    ),
                )
                self._append_recovery_audit(
                    context, linked.audit_events[len(recovered.audit_events) :]
                )
                context.recovery_state = linked
                recovered = linked
            self._complete_timeline(recorder, context.checkout)
            self._record_recovery_events(
                recorder,
                recovered.audit_events,
                start=acquisition_event_count,
            )
            if capability is not None:
                recorder.audit(
                    "MANDATE_REGISTERED_ACTIVE",
                    mandate_id=capability.payload.mandate_id,
                    mandate_version=capability.payload.mandate_version,
                )
                recorder.audit(
                    "CAPABILITY_ISSUED",
                    decision_nonce_prefix=capability.payload.decision_nonce[:12],
                    source="RECOVERED_FRESH_AUTHORIZATION",
                )
            if isinstance(execution_result, ExecutionReceipt):
                recorder.audit(
                    "RAZORPAY_ORDER_CREATED",
                    order_id=execution_result.razorpay_order_id,
                    environment=("OFFLINE_DEMO" if run.mode == "offline" else "TEST"),
                )
            with run.lock:
                run.output = self._present_result(run, context)
                run.updated_at = _utc_now_text()
            return run.snapshot()
        finally:
            with run.lock:
                run.recovery_in_flight = False

    def _append_recovery_audit(
        self, context: _RunContext, events: tuple[Any, ...]
    ) -> None:
        """Persist provenance or leave the review safely non-executable.

        Recovery is fail-closed on audit persistence: if the append fails, the
        round stays consumed, no provider call or capability follows, and the
        review is marked `AUDIT_PERSISTENCE_FAILED`. Later acquisition attempts
        on the same review return `RECOVERY_AUDIT_UNAVAILABLE` instead of a
        confusing reservation error. Clearing that state is a deliberate
        operator action against the persistent store, not an automatic retry.
        """

        try:
            self.recovery_audit_store.append(events)
        except RecoveryAuditStoreError as error:
            context.recovery_audit_state = "AUDIT_PERSISTENCE_FAILED"
            raise RuntimeError(RECOVERY_AUDIT_UNAVAILABLE) from error

    def _trusted_now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise RuntimeError("trusted server clock must return a timezone-aware datetime")
        return now

    def _execute_run(self, run: CommerceRun) -> None:
        recorder = RunRecorder(run)
        active_step = "USER_MANDATE"
        try:
            recorder.step("USER_MANDATE", "RUNNING", "Mandate received by server")
            recorder.audit("MANDATE_RECEIVED", mode=run.mode)
            recorder.step("USER_MANDATE", "PASS", "Bounded intent accepted")
            active_step = "AI_BUYER"
            recorder.step("AI_BUYER", "RUNNING", "Commerce-only buyer executing")

            tools = ObservedCommerceTools(self.store, recorder.tool)
            operational_counters = OperationalCounters()
            run_registry = self.recovery_registry.instrumented(
                lambda _merchant_id: (
                    operational_counters.record_trusted_evidence_provider_call()
                )
            )
            if run.mode == "offline":
                buyer_delegate: CommerceBuyer = ToolDrivenOfflineBuyer(tools)
                embedding = HashingEmbeddingProvider()
                semantic_delegate = TimedSemanticModel(DeterministicSemanticModel())
                evaluated_at = self._trusted_now()
            else:
                from openai import OpenAI

                client = OpenAI()
                observed_client = type(
                    "ObservedOpenAIClient",
                    (),
                    {
                        "responses": ObservedCreateResource(
                            client.responses,
                            operational_counters.record_openai_call,
                        ),
                        "embeddings": ObservedCreateResource(
                            client.embeddings,
                            operational_counters.record_openai_call,
                        ),
                    },
                )()
                semantic_model_id = os.environ["MANDATEGUARD_SEMANTIC_MODEL"]
                buyer_model_id = (
                    os.environ.get("MANDATEGUARD_BUYER_MODEL") or semantic_model_id
                )
                embedding_model_id = os.environ.get(
                    "MANDATEGUARD_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
                )
                buyer_delegate = OpenAIResponsesBuyer(
                    client=observed_client,
                    model_id=buyer_model_id,
                    tools=tools,
                )
                embedding = OpenAIEmbeddingProvider(
                    client=observed_client, model_id=embedding_model_id
                )
                semantic_usage = ResponsesUsageCapture(observed_client.responses)
                semantic_delegate = TimedSemanticModel(
                    OpenAIResponsesSemanticModel(
                        client=type("ResponsesClient", (), {"responses": semantic_usage})(),
                        model_id=semantic_model_id,
                    ),
                    usage_source=semantic_usage,
                )
                evaluated_at = self._trusted_now()

            def buyer_complete(output: BuyerOutput) -> None:
                nonlocal active_step
                recorder.step("AI_BUYER", "PASS", "Typed proposal produced")
                recorder.audit(
                    "BUYER_PRODUCT_SELECTED",
                    merchant_id=output.proposal.merchant_id,
                    sku=output.proposal.sku,
                )
                active_step = "PRODUCT"
                recorder.step("PRODUCT", "RUNNING", "Registered product resolving")
                recorder.step("PRODUCT", "PASS", "Merchant and SKU resolved")
                active_step = "EVIDENCE_RETRIEVAL"

            buyer = ObservedBuyer(buyer_delegate, buyer_complete)

            def retrieval_start() -> None:
                recorder.step(
                    "EVIDENCE_RETRIEVAL", "RUNNING", "Registered corpus ranking"
                )

            def retrieval_complete(result: RetrievalResult) -> None:
                nonlocal active_step
                trusted = sum(
                    item.document.source_type is RetrievalSource.MERCHANT_EVIDENCE
                    for item in result.ranked_documents
                )
                recorder.step(
                    "EVIDENCE_RETRIEVAL",
                    "PASS",
                    f"{trusted} trusted merchant evidence items in top-k",
                )
                recorder.audit(
                    "EVIDENCE_RETRIEVED", top_k=result.top_k, trusted_count=trusted
                )
                active_step = "DETERMINISTIC_VERIFICATION"
                recorder.step(
                    "DETERMINISTIC_VERIFICATION",
                    "RUNNING",
                    "Frozen Tier A/B controller executing",
                )

            retriever = ObservedHybridRetriever(
                embedding,
                on_start=retrieval_start,
                on_complete=retrieval_complete,
            )

            def semantic_start() -> None:
                nonlocal active_step
                recorder.step(
                    "DETERMINISTIC_VERIFICATION",
                    "PASS",
                    "Tier A/B allowed semantic evaluation",
                )
                active_step = "SEMANTIC_VERIFICATION"
                recorder.step(
                    "SEMANTIC_VERIFICATION",
                    "RUNNING",
                    "Frozen semantic verifier executing",
                )

            semantic_model = ObservedSemanticModel(semantic_delegate, semantic_start)
            verifier = SemanticVerifier(
                model=semantic_model,
                cache=self.semantic_cache,
            )

            if run.mode == "offline":
                execution_key = self._offline_signing_key
                key_id = "commerce-lab-offline-hmac-v1"
                account_scope = "razorpay-test-offline-demo"
                provider = OfflineTestOrdersClient()
            else:
                execution_key = os.environ["MANDATEGUARD_EXECUTION_HMAC_KEY"].encode(
                    "utf-8"
                )
                key_id = "commerce-lab-live-hmac-v1"
                razorpay_key_id = os.environ["RAZORPAY_KEY_ID"]
                account_scope = "razorpay-test-" + sha256(
                    razorpay_key_id.encode("utf-8")
                ).hexdigest()[:16]
                provider = RazorpayTestOrdersAdapter(
                    key_id=razorpay_key_id,
                    key_secret=os.environ["RAZORPAY_KEY_SECRET"],
                )

            execution_client = ObservedOrdersClient(
                provider,
                counters=operational_counters,
                on_start=lambda: self._execution_started(recorder),
                on_complete=lambda: recorder.step(
                    "EXECUTION", "PASS", "Order creation response validated"
                ),
                on_error=lambda: recorder.step(
                    "EXECUTION", "ERROR", "Provider execution failed safely"
                ),
            )
            config = TrustedExecutionConfig(
                merchant_id="merchant-scholarly",
                account_scope=account_scope,
            )

            # The selected merchant is not known until the buyer runs. The config used by
            # the frozen executor must match it, so resolve the proposal once through the
            # observed buyer and consume that fixed typed output in the normal pathway.
            first_output = buyer.purchase(run.user_intent)
            fixed_buyer = _FixedObservedBuyer(first_output)
            config = TrustedExecutionConfig(
                merchant_id=first_output.proposal.merchant_id,
                account_scope=account_scope,
            )
            runtime = ExecutionRuntime(
                config=config,
                signer=HMACSHA256Signer(key_id=key_id, key=execution_key),
                verifier=HMACSHA256Verifier({key_id: execution_key}),
                ledger=self.execution_ledger,
                client=execution_client,
                mandate_state_registry=self.mandate_state_registry,
            )
            decision_nonce = "mg_product_" + sha256(
                run.request_id.encode("ascii")
            ).hexdigest()[:32]
            checkout = run_agentic_checkout(
                user_intent=run.user_intent,
                buyer=fixed_buyer,
                store=self.store,
                retriever=retriever,
                semantic_verifier=verifier,
                evaluated_at=evaluated_at,
                top_k=run.top_k,
                alpha=self.evidence_policy.alpha,
                retrieval_mode=self.evidence_policy.retrieval_mode,
                execute=True,
                defer_execution=run.defer_execution,
                execution_runtime=runtime,
                decision_nonce=decision_nonce,
                # Consent identity is scoped to this server-issued run, so two
                # visitors who type the same sentence get two mandates and one
                # visitor's revocation cannot reach the other's capability.
                mandate_identity_seed=run.run_id,
            )
            self._complete_timeline(recorder, checkout)
            recorder.audit(
                "DETERMINISTIC_CHECK_COMPLETED",
                action=checkout.authorization_result.deterministic_decision.action.value,
            )
            semantic_decision = checkout.authorization_result.semantic_decision
            recorder.audit(
                "SEMANTIC_CHECK_COMPLETED",
                status=(
                    semantic_decision.verdict.value
                    if semantic_decision is not None
                    else "NOT_EVALUATED"
                ),
            )
            recorder.audit(
                "AUTHORIZATION_DECIDED",
                action=checkout.authorization_result.final_action.value,
            )
            if checkout.execution_authorization is not None:
                recorder.audit(
                    "MANDATE_REGISTERED_ACTIVE",
                    mandate_id=checkout.execution_authorization.payload.mandate_id,
                    mandate_version=(
                        checkout.execution_authorization.payload.mandate_version
                    ),
                )
                recorder.audit(
                    "CAPABILITY_ISSUED",
                    decision_nonce_prefix=(
                        checkout.execution_authorization.payload.decision_nonce[:12]
                    ),
                )
            if isinstance(checkout.execution_result, ExecutionReceipt):
                recorder.audit(
                    "RAZORPAY_ORDER_CREATED",
                    order_id=checkout.execution_result.razorpay_order_id,
                    environment=("OFFLINE_DEMO" if run.mode == "offline" else "TEST"),
                )
            semantic_evidence = self._semantic_evidence_for_checkout(checkout)
            recovery_state = None
            if checkout.authorization_result.final_action is DecisionAction.REVIEW:
                recovery_state = create_review_recovery(
                    scenario=self._replay_scenario(checkout, evaluated_at),
                    authorization=checkout.authorization_result,
                    semantic_evidence=semantic_evidence,
                    registry=run_registry,
                    created_at=evaluated_at,
                )
                self.recovery_audit_store.append(recovery_state.audit_events)
                self._record_recovery_events(recorder, recovery_state.audit_events)
            context = _RunContext(
                checkout=checkout,
                execution_runtime=runtime,
                client=execution_client,
                evaluated_at=evaluated_at,
                store=self.store,
                semantic_verifier=verifier,
                semantic_evidence=semantic_evidence,
                operational_counters=operational_counters,
                recovery_registry=run_registry,
                trust_configuration=self.trust_configuration(top_k=run.top_k),
                recovery_state=recovery_state,
            )
            output = self._present_result(run, context)
            with run.lock:
                run.private_context = context
                run.output = output
                run.state = "COMPLETE"
                run.updated_at = _utc_now_text()
        except (TypeError, ValueError, RuntimeError) as error:
            recorder.step(active_step, "ERROR", "The run stopped safely")
            with run.lock:
                run.state = "ERROR"
                run.error = {
                    "code": "RUN_REJECTED",
                    "message": str(error),
                }
                run.updated_at = _utc_now_text()
        except BaseException:
            recorder.step(active_step, "ERROR", "The run stopped safely")
            with run.lock:
                run.state = "ERROR"
                run.error = {
                    "code": "INTERNAL_ERROR",
                    "message": "The commerce lab stopped safely before further action.",
                }
                run.updated_at = _utc_now_text()
        finally:
            run.completion.set()

    def _semantic_evidence_for_checkout(
        self, checkout: AgenticCheckoutResult
    ) -> SemanticEvidence | None:
        proposal = checkout.buyer_output.proposal
        selected_ids = tuple(
            checkout.trace.retrieval.get("trusted_evidence_selected_ids", ())
        )
        if not selected_ids:
            return None
        entries = self.store.resolve_evidence_ids(
            selected_ids,
            merchant_id=proposal.merchant_id,
            sku=proposal.sku,
        )
        bundle = SemanticEvidenceBundle(
            merchant_id=proposal.merchant_id,
            entries=entries,
        )
        return SemanticEvidence(
            bundle=bundle,
            semantic_evidence_sha256=semantic_evidence_sha256(bundle),
        )

    def _replay_scenario(
        self, checkout: AgenticCheckoutResult, evaluated_at: datetime
    ) -> ReplayScenario:
        merchant_id = checkout.transaction.payload.merchant_id
        catalog = self.store.catalog_snapshot(merchant_id=merchant_id)
        return ReplayScenario(
            mandate=checkout.mandate,
            transaction=checkout.transaction,
            catalog_snapshot=catalog,
            server_time=evaluated_at,
            nonce_state=NonceLedgerState(),
            psp_committed_hashes=CommittedHashes(
                transaction_sha256=transaction_body_sha256(checkout.transaction),
                catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
            ),
            replay_seed=1001,
            evaluated_at=evaluated_at,
        )

    @staticmethod
    def _record_recovery_events(
        recorder: RunRecorder,
        events: tuple[object, ...],
        *,
        start: int = 0,
    ) -> None:
        for event in events[start:]:
            recorder.audit(
                event.event.value,
                event_recorded_at=event.recorded_at.isoformat(),
                recovery_event_sha256=event.event_sha256,
                previous_recovery_event_sha256=event.previous_event_sha256,
                evidence_set_sha256=event.evidence_set_sha256,
                authorization_result_sha256=event.authorization_result_sha256,
                mandate_payload_sha256=event.mandate_payload_sha256,
                transaction_body_sha256=event.transaction_body_sha256,
                decision_nonce=event.decision_nonce,
                execution_request_sha256=event.execution_request_sha256,
                execution_receipt_id=event.execution_receipt_id,
                initial_evaluated_at=event.initial_evaluated_at.isoformat(),
                recovery_started_at=(
                    event.recovery_started_at.isoformat()
                    if event.recovery_started_at is not None
                    else None
                ),
                recovery_authorized_at=(
                    event.recovery_authorized_at.isoformat()
                    if event.recovery_authorized_at is not None
                    else None
                ),
                round=event.round_number,
                decision=event.decision.value,
                constraint_statuses=list(event.constraint_statuses),
                gap_kinds=list(event.gap_kinds),
                diagnostic_version=event.diagnostic_version,
                registry_sha256=event.registry_sha256,
                source_ids=list(event.source_ids),
                source_scopes=list(event.source_scopes),
                manifest_versions=list(event.manifest_versions),
                manifest_sha256s=list(event.manifest_sha256s),
                expected_evidence_ids=list(event.expected_evidence_ids),
                expected_evidence_hashes=list(event.expected_evidence_hashes),
                actual_evidence_ids=list(event.actual_evidence_ids),
                actual_evidence_hashes=list(event.actual_evidence_hashes),
                acquisition_complete=event.acquisition_complete,
                semantic_input_sha256=event.semantic_input_sha256,
                semantic_output_sha256=event.semantic_output_sha256,
                outcome_codes=list(event.outcome_codes),
            )

    @staticmethod
    def _execution_started(recorder: RunRecorder) -> None:
        recorder.step(
            "SEMANTIC_VERIFICATION", "PASS", "Semantic constraints passed"
        )
        recorder.step("AUTHORIZATION", "PASS", "Signed ALLOW capability validated")
        recorder.step("EXECUTION", "RUNNING", "D6 gate reserved single use")

    @staticmethod
    def _complete_timeline(
        recorder: RunRecorder, checkout: AgenticCheckoutResult
    ) -> None:
        authorization = checkout.authorization_result
        deterministic_action = authorization.deterministic_decision.action
        if deterministic_action is DecisionAction.BLOCK:
            recorder.step(
                "DETERMINISTIC_VERIFICATION", "BLOCK", "Tier A/B violation"
            )
            recorder.step(
                "SEMANTIC_VERIFICATION",
                "BLOCK",
                "Not evaluated after deterministic block",
            )
        elif deterministic_action is DecisionAction.REVIEW:
            recorder.step(
                "DETERMINISTIC_VERIFICATION", "REVIEW", "Evidence not evaluable"
            )
            recorder.step(
                "SEMANTIC_VERIFICATION",
                "REVIEW",
                "Not evaluated after deterministic review",
            )
        else:
            recorder.step(
                "DETERMINISTIC_VERIFICATION", "PASS", "All Tier A/B checks passed"
            )
            semantic = authorization.semantic_decision
            if semantic is None:
                recorder.step(
                    "SEMANTIC_VERIFICATION",
                    "REVIEW",
                    "Not evaluated: insufficient trusted evidence",
                )
            elif semantic.verdict.value == "PASS":
                recorder.step(
                    "SEMANTIC_VERIFICATION", "PASS", "Semantic constraints passed"
                )
            elif semantic.verdict.value == "VIOLATION":
                recorder.step(
                    "SEMANTIC_VERIFICATION",
                    "BLOCK",
                    "Semantic violation detected",
                )
            else:
                recorder.step(
                    "SEMANTIC_VERIFICATION",
                    "REVIEW",
                    "Semantic evidence was ambiguous",
                )
        final = authorization.final_action
        recorder.step(
            "AUTHORIZATION",
            final.value if final is not DecisionAction.ALLOW else "PASS",
            f"Final controller: {final.value}",
        )
        if final is DecisionAction.BLOCK:
            recorder.step("EXECUTION", "BLOCK", "Razorpay calls: 0")
        elif final is DecisionAction.REVIEW:
            recorder.step("EXECUTION", "REVIEW", "Razorpay calls: 0")
        elif checkout.execution_authorization is not None and checkout.execution_result is None:
            recorder.step(
                "EXECUTION",
                "AUTHORIZED",
                "Capability issued; Razorpay calls: 0",
            )
        elif isinstance(checkout.execution_result, ExecutionRefusal):
            recorder.step(
                "EXECUTION",
                "REJECTED",
                f"{checkout.execution_result.reason.value}; rejected before network",
            )

    def _present_result(
        self, run: CommerceRun, context: _RunContext
    ) -> dict[str, Any]:
        checkout = context.checkout
        proposal = checkout.buyer_output.proposal
        product = context.store.get_product(
            merchant_id=proposal.merchant_id, sku=proposal.sku
        )
        authorization = checkout.authorization_result
        deterministic = authorization.deterministic_decision
        tier_a = [
            {
                "family": item.family.value,
                "label": _TIER_A_LABELS[item.family.value],
                "status": item.status.value,
                "reason": (
                    item.finding.message if item.finding is not None else item.reason
                ),
            }
            for item in deterministic.tier_a_results
        ]
        tier_b_by_family = {
            item.family.value: item for item in deterministic.findings
            if item.family.value.startswith("B")
        }
        tier_b = [
            {
                "family": family,
                "label": label,
                "status": "FAIL" if family in tier_b_by_family else "PASS",
                "reason": (
                    tier_b_by_family[family].message
                    if family in tier_b_by_family
                    else None
                ),
            }
            for family, label in _TIER_B_LABELS.items()
        ]
        semantic_by_id = {}
        if authorization.semantic_decision is not None:
            semantic_by_id = {
                item.constraint_id: item
                for item in authorization.semantic_decision.constraint_results
            }
        semantic_checks = []
        for constraint in checkout.mandate.payload.constraints.semantic:
            result = semantic_by_id.get(constraint.constraint_id)
            semantic_checks.append(
                {
                    "constraint_id": constraint.constraint_id,
                    "family": constraint.kind,
                    "constraint": constraint.text,
                    "status": (
                        result.status.value if result is not None else "NOT_EVALUATED"
                    ),
                    "reason": result.reason if result is not None else None,
                }
            )
        score_by_document = {
            item.document.document_id: item.score
            for item in checkout.retrieval.ranked_documents
        }
        evidence_cards = []
        for ranked in checkout.retrieval.ranked_documents:
            document = ranked.document
            if document.source_type is not RetrievalSource.MERCHANT_EVIDENCE:
                continue
            entry = context.store.resolve_evidence_ids(
                (document.evidence_id,),
                merchant_id=proposal.merchant_id,
                sku=proposal.sku,
            )[0]
            evidence_cards.append(
                {
                    "evidence_id": entry.evidence_id,
                    "source_kind": entry.source_kind,
                    "merchant_id": entry.merchant_id,
                    "sku": entry.sku,
                    "scope": "PRODUCT" if entry.sku is not None else "MERCHANT",
                    "text": entry.text,
                    "retrieval_score": round(ranked.score.hybrid_score, 6),
                    "acquisition": "INITIAL_RETRIEVAL",
                }
            )
        presented_ids = {item["evidence_id"] for item in evidence_cards}
        if context.semantic_evidence is not None:
            for entry in context.semantic_evidence.bundle.entries:
                if entry.evidence_id in presented_ids:
                    continue
                evidence_cards.append(
                    {
                        "evidence_id": entry.evidence_id,
                        "source_kind": entry.source_kind,
                        "merchant_id": entry.merchant_id,
                        "sku": entry.sku,
                        "scope": "PRODUCT" if entry.sku is not None else "MERCHANT",
                        "text": entry.text,
                        "retrieval_score": None,
                        "acquisition": "BOUNDED_TRUSTED_ACQUISITION",
                    }
                )
                presented_ids.add(entry.evidence_id)
        cache_payload = dict(checkout.trace.cache)
        if cache_payload.get("status") is None:
            cache_payload["status"] = "NOT_USED"
        if context.recovery_state is not None and context.recovery_state.rounds_used:
            cache_payload["status"] = "REEVALUATED"
            if authorization.semantic_decision is not None:
                cache_payload["key_prefix"] = (
                    authorization.semantic_decision.semantic_input_sha256[:12]
                )
        final_reason = self._final_reason(checkout)
        execution = self._present_execution(run, context)
        recovery = self._present_recovery(context)
        recovery_state = context.recovery_state
        reauthorization_seen = bool(
            recovery_state
            and any(
                event.event.value == "REAUTHORIZATION"
                for event in recovery_state.audit_events
            )
        )
        counters = context.operational_counters
        observed_counters = {
            "openai_calls": counters.openai_calls,
            "razorpay_http_calls": counters.razorpay_http_calls,
            "offline_adapter_calls": counters.offline_adapter_calls,
            "trusted_evidence_provider_calls": (
                counters.trusted_evidence_provider_calls
            ),
            "acquisition_rounds": (
                recovery_state.rounds_used if recovery_state is not None else 0
            ),
            "new_evidence_items": (
                recovery_state.new_evidence_items
                if recovery_state is not None
                else 0
            ),
            # A recovered ALLOW that never went through a fresh controller
            # reauthorization would be a planner-issued ALLOW. It must stay 0.
            "planner_direct_allow_count": int(
                recovery_state is not None
                and recovery_state.final_action is DecisionAction.ALLOW
                and not reauthorization_seen
            ),
        }
        validate_observed_counters(
            observed_counters, context="commerce lab observed counters"
        )
        return {
            "decision": authorization.final_action.value,
            "decision_reason": final_reason,
            "buyer": {
                "mandate": run.user_intent,
                "product": product.name,
                "product_description": product.description,
                "merchant": proposal.merchant_id,
                "sku": proposal.sku,
                "price_minor": proposal.declared_total_minor,
                "currency": proposal.currency,
                "quantity": proposal.quantity,
                "tool_calls": [dict(item) for item in run.tool_calls],
                "rationale": proposal.reason,
                "buyer_provided_text": proposal.user_intent_summary,
                "authority_notice": "AI Buyer has no direct Razorpay authority.",
            },
            "evidence": {
                "classification": "TRUSTED MERCHANT EVIDENCE",
                "retrieval_method": "HYBRID_TFIDF_AND_EMBEDDING",
                "alpha": checkout.retrieval.alpha,
                "top_k": checkout.retrieval.top_k,
                "trusted_evidence_count": len(evidence_cards),
                "cards": evidence_cards,
                "buyer_text": {
                    "classification": "BUYER-PROVIDED TEXT",
                    "text": proposal.reason,
                    "trusted": False,
                },
                "ranked_source_count": len(score_by_document),
                "evidence_set_sha256": (
                    context.recovery_state.current_evidence_sha256
                    if context.recovery_state is not None
                    else (
                        context.semantic_evidence.semantic_evidence_sha256
                        if context.semantic_evidence is not None
                        else None
                    )
                ),
            },
            "authorization": {
                "deterministic": {
                    "action": deterministic.action.value,
                    "tier_a": tier_a,
                    "tier_b": tier_b,
                },
                "semantic": {
                    "verdict": (
                        authorization.semantic_decision.verdict.value
                        if authorization.semantic_decision is not None
                        else "NOT_EVALUATED"
                    ),
                    "checks": semantic_checks,
                    "cache": cache_payload,
                },
                "final_controller": authorization.final_action.value,
                "controller_source": "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
            },
            "execution": execution,
            "recovery": recovery,
            "transactability": self._present_transactability(context),
            "observed_counters": observed_counters,
            "trust_configuration": dict(context.trust_configuration),
            "models": dict(checkout.trace.models),
            "timings": dict(checkout.trace.timings),
            "raw_trace": checkout.trace.to_mapping(),
        }

    @staticmethod
    def _final_reason(checkout: AgenticCheckoutResult) -> str:
        authorization = checkout.authorization_result
        if isinstance(authorization, InsufficientEvidenceAuthorizationResult):
            return "Insufficient trusted evidence for semantic evaluation."
        deterministic = authorization.deterministic_decision
        if deterministic.action is not DecisionAction.ALLOW:
            for result in deterministic.tier_a_results:
                if result.status is not TierACheckStatus.PASS:
                    return (
                        result.finding.message
                        if result.finding is not None
                        else result.reason
                        or "Deterministic evidence was unavailable."
                    )
            if deterministic.findings:
                return deterministic.findings[0].message
        semantic = authorization.semantic_decision
        if semantic is not None:
            non_pass = [
                item.reason
                for item in semantic.constraint_results
                if item.status is not ConstraintStatus.PASS
            ]
            if non_pass:
                return non_pass[0]
        return "All applicable deterministic and semantic checks passed."

    def _present_recovery(self, context: _RunContext) -> dict[str, Any] | None:
        state = context.recovery_state
        if state is None:
            return None
        preferred = next(
            (
                gap
                for gap in state.gap_analysis.gaps
                if gap.missing_evidence_kind is EvidenceKind.RECURRENCE
            ),
            state.gap_analysis.gaps[0] if state.gap_analysis.gaps else None,
        )
        source = None
        if preferred is not None and preferred.candidate_evidence_ids:
            source = self.recovery_registry.source(
                preferred.candidate_evidence_ids[0]
            )
        audit_available = context.recovery_audit_state == "AVAILABLE"
        if not audit_available:
            status = "AUDIT_UNAVAILABLE"
        elif state.resolved:
            status = "RESOLVED"
        elif state.budget_exhausted:
            status = "BUDGET_EXHAUSTED"
        elif state.gap_analysis.status is GapAnalysisStatus.RECOVERABLE:
            status = "AVAILABLE" if state.rounds_used == 0 else "STILL_REVIEW"
        elif state.gap_analysis.status is GapAnalysisStatus.INCOMPLETE_COVERAGE:
            status = "INCOMPLETE_SOURCE_COVERAGE"
        else:
            status = "NO_RECOVERABLE_GAP"
        action_enabled = (
            audit_available
            and state.final_action is DecisionAction.REVIEW
            and not state.budget_exhausted
            and state.gap_analysis.status is GapAnalysisStatus.RECOVERABLE
        )
        return {
            "status": status,
            "review_id": state.review_id,
            "initial_decision": "REVIEW",
            "current_decision": state.final_action.value,
            "transition": (
                f"REVIEW -> {state.final_action.value}" if state.resolved else None
            ),
            "gap": (
                {
                    "constraint_id": preferred.constraint_id,
                    "constraint_family": preferred.constraint_family,
                    "reason": preferred.reason,
                    "missing_evidence_kind": preferred.missing_evidence_kind.value,
                    "merchant_id": preferred.merchant_id,
                    "sku": preferred.sku,
                    "candidate_evidence_ids": list(
                        preferred.candidate_evidence_ids
                    ),
                    "diagnostic_source": preferred.diagnostic_source,
                    "created_at": preferred.created_at.isoformat(),
                }
                if preferred is not None
                else None
            ),
            "trusted_source": (
                {
                    "source_id": source.source_id,
                    "label": source.display_name,
                    "scope": source.manifest.scope_type.value,
                    "manifest_sha256": source.manifest.manifest_sha256,
                    "manifest_version": source.manifest.manifest_version,
                }
                if source is not None
                else None
            ),
            "action": {
                "enabled": action_enabled,
                "label": "ACQUIRE TRUSTED EVIDENCE",
                "accepts_source_input": False,
                "accepts_url": False,
                "accepts_evidence_text": False,
            },
            "audit_state": context.recovery_audit_state,
            "rounds_used": state.rounds_used,
            "max_rounds": MAX_ACQUISITION_ROUNDS,
            "new_evidence_items": state.new_evidence_items,
            "max_new_evidence_items": MAX_NEW_EVIDENCE_ITEMS,
            "evidence_provider_calls": state.evidence_provider_calls,
            "payment_provider_calls_before_final_allow": (
                context.payment_provider_calls_before_final_allow
            ),
            "initial_evidence_sha256": state.initial_evidence_sha256,
            "current_evidence_sha256": state.current_evidence_sha256,
            "initial_evaluated_at": state.initial_evaluated_at.isoformat(),
            "recovery_started_at": (
                state.recovery_started_at.isoformat()
                if state.recovery_started_at is not None
                else None
            ),
            "recovery_authorized_at": (
                state.recovery_authorized_at.isoformat()
                if state.recovery_authorized_at is not None
                else None
            ),
            "resolved_after": (
                f"{state.rounds_used} trusted evidence acquisition"
                if state.resolved
                else None
            ),
            "audit_event_hashes": [
                event.event_sha256 for event in state.audit_events
            ],
        }

    def _present_transactability(self, context: _RunContext) -> dict[str, Any]:
        authorization = context.checkout.authorization_result
        tier_a = {
            item.family.value: item.status is TierACheckStatus.PASS
            for item in authorization.deterministic_decision.tier_a_results
        }
        semantic_status = {}
        if authorization.semantic_decision is not None:
            semantic_status = {
                item.constraint_id: item.status
                for item in authorization.semantic_decision.constraint_results
            }
        mandate = context.checkout.mandate
        purpose_ids = {
            item.constraint_id
            for item in mandate.payload.constraints.semantic
            if item.kind == "purpose"
        }
        recurrence_ids = {
            item.constraint_id
            for item in mandate.payload.constraints.semantic
            if item.constraint_family is SemanticConstraintFamily.RECURRENCE
        }
        state = context.recovery_state
        purpose_available = bool(
            state
            and any(
                gap.missing_evidence_kind is EvidenceKind.PURPOSE
                for gap in state.gap_analysis.gaps
            )
        )
        purpose_verified = bool(purpose_ids) and all(
            semantic_status.get(item) is ConstraintStatus.PASS for item in purpose_ids
        )
        recurrence_verified = bool(recurrence_ids) and all(
            semantic_status.get(item) is ConstraintStatus.PASS
            for item in recurrence_ids
        )
        readiness = (
            {"label": "PRICE", "status": "VERIFIED" if tier_a.get("A1") else "MISSING"},
            {"label": "SKU OWNERSHIP", "status": "VERIFIED" if tier_a.get("A2") else "MISSING"},
            {"label": "MERCHANT BINDING", "status": "VERIFIED" if tier_a.get("A3") else "MISSING"},
            {
                "label": "PURPOSE EVIDENCE",
                "status": (
                    "VERIFIED"
                    if purpose_verified
                    else "AVAILABLE" if purpose_available else "MISSING"
                ),
            },
            {
                "label": "RECURRENCE TERMS",
                "status": "VERIFIED" if recurrence_verified else "MISSING",
            },
        )
        if authorization.final_action is DecisionAction.REVIEW:
            status = "REVIEW"
            next_action = (
                "Additional trusted evidence may make this transaction evaluable."
                if state is not None
                and state.gap_analysis.status is GapAnalysisStatus.RECOVERABLE
                else "Complete authoritative evidence must be registered before reevaluation."
            )
            evidence_readiness = "INCOMPLETE"
        else:
            status = "EVIDENCE READY" if authorization.final_action is DecisionAction.ALLOW else "POLICY BLOCKED"
            next_action = "No evidence-readiness action is required."
            evidence_readiness = (
                "COMPLETE"
                if authorization.final_action is DecisionAction.ALLOW
                else "NOT_APPLICABLE"
            )
        return {
            "readiness": list(readiness),
            "status": status,
            "evidence_readiness": evidence_readiness,
            "next_action": next_action,
            "authority_notice": "Diagnostic only. This surface cannot authorize payments.",
        }

    def _present_execution(
        self, run: CommerceRun, context: _RunContext
    ) -> dict[str, Any]:
        checkout = context.checkout
        capability = checkout.execution_authorization
        receipt = checkout.execution_result
        authorization = checkout.authorization_result
        if capability is None:
            return {
                "status": "NOT_CALLED",
                "razorpay_calls": context.client.adapter_calls,
                "external_network_calls": context.client.external_network_calls,
                "reason": (
                    "Authorization blocked execution."
                    if authorization.final_action is DecisionAction.BLOCK
                    else "Review is required before execution."
                ),
                "capability": None,
                "order": None,
                "replay": None,
                "environment": (
                    "OFFLINE_DEMO" if run.mode == "offline" else "RAZORPAY_TEST_MODE"
                ),
            }
        ledger_record = context.execution_runtime.ledger.get(
            capability.payload.decision_nonce
        )
        mandate_state = context.execution_runtime.mandate_state_registry.get_current(
            capability.payload.mandate_id
        )
        signature_valid = (
            context.execution_runtime.verifier.verify(capability)
            is SignatureVerification.VALID
        )
        transaction_bound = (
            capability.payload.transaction_body_sha256
            == transaction_body_sha256(checkout.transaction)
        )
        rebuilt_request = build_razorpay_order_request(
            checkout.transaction, capability.payload.decision_nonce
        )
        request_bound = (
            capability.payload.execution_request_sha256
            == execution_request_sha256(rebuilt_request)
        )
        order = None
        if isinstance(receipt, ExecutionReceipt):
            order = {
                "order_id": receipt.razorpay_order_id,
                "amount": receipt.amount,
                "currency": receipt.currency,
                "receipt": receipt.receipt,
                "status": receipt.status,
            }
        refusal_reason = (
            receipt.reason.value if isinstance(receipt, ExecutionRefusal) else None
        )
        if order is not None:
            status = "ORDER_CREATED"
        elif isinstance(receipt, ExecutionRefusal):
            status = "REJECTED_BEFORE_NETWORK"
        else:
            status = "AUTHORIZED"
        current_status = (
            mandate_state.status.value if mandate_state is not None else "MISSING"
        )
        current_version = mandate_state.version if mandate_state is not None else None
        unused_capability = ledger_record is None and receipt is None
        return {
            "status": status,
            "razorpay_calls": context.client.adapter_calls,
            "external_network_calls": context.client.external_network_calls,
            "reason": refusal_reason,
            "capability": {
                "signature_verified": signature_valid,
                "transaction_bound": transaction_bound,
                "request_bound": request_bound,
                "merchant_bound": (
                    capability.payload.merchant_id
                    == checkout.transaction.payload.merchant_id
                    == context.execution_runtime.config.merchant_id
                ),
                "mandate_identity_bound": (
                    capability.payload.mandate_id
                    == checkout.mandate.payload.mandate_id
                ),
                "mandate_version_bound": (
                    checkout.mandate_version == capability.payload.mandate_version
                ),
                "expiry_valid": self._trusted_now() < capability.payload.expires_at,
                "single_use": (
                    ledger_record is not None
                    and ledger_record.status is ExecutionLedgerStatus.SUCCEEDED
                ),
                "nonce_consumed": ledger_record is not None,
            },
            "consent": {
                "status": current_status,
                "mandate_id": capability.payload.mandate_id,
                "mandate_version": capability.payload.mandate_version,
                "current_version": current_version,
                "current_version_matches": (
                    current_version == capability.payload.mandate_version
                ),
                "authority": "DEMO USER REVOCATION",
                "can_revoke": (
                    unused_capability
                    and mandate_state is not None
                    and mandate_state.status is MandateStatus.ACTIVE
                    and mandate_state.version == capability.payload.mandate_version
                ),
                "can_execute": unused_capability,
                "teaching": (
                    "The capability is still signed and unexpired. Current consent "
                    "no longer permits execution."
                    if current_status in {"REVOKED", "SUPERSEDED"}
                    else "MandateGuard revalidates its trusted mandate state "
                    "immediately before execution."
                ),
            },
            "order": order,
            "replay": None,
            "environment": (
                "OFFLINE_DEMO_TEST_DOUBLE"
                if run.mode == "offline"
                else "RAZORPAY_TEST_MODE"
            ),
        }


class _FixedObservedBuyer:
    __slots__ = ("model_id", "output")

    def __init__(self, output: BuyerOutput) -> None:
        self.output = output
        self.model_id = output.model_id

    def purchase(self, _user_intent: str) -> BuyerOutput:
        return self.output
