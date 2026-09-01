"""Thread-safe product composition around the frozen commerce controller."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

from mandateguard.core.hashing import transaction_body_sha256
from mandateguard.execution import (
    HMACSHA256Signer,
    HMACSHA256Verifier,
    RazorpayTestOrdersAdapter,
    SQLiteExecutionLedger,
    TrustedExecutionConfig,
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
    DEFAULT_ALPHA,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_TOP_K,
    HashingEmbeddingProvider,
    HybridRetriever,
    OpenAIEmbeddingProvider,
)
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.intelligence.tools import BuyerDraft, CommerceTools
from mandateguard.models.decision import DecisionAction
from mandateguard.models.finding import TierACheckStatus
from mandateguard.semantic import OpenAIResponsesSemanticModel, SemanticVerifier
from mandateguard.semantic.models import ConstraintStatus

from mandateguard.product.evidence import (
    FAILURE_RECOVERY_EVIDENCE,
    INT3_RESEARCH_FINDING,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_NO_EXCLUSION_RE = re.compile(r"\bno\s+([^.;]+)", re.IGNORECASE)
_TERMINAL_STATES = frozenset({"COMPLETE", "ERROR"})
_MAX_RETAINED_RUNS = 256
_OFFLINE_EVALUATED_AT = datetime(2026, 8, 30, 7, 41, 15, tzinfo=timezone.utc)


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
)
_PRESETS_BY_ID = {item["id"]: item for item in DEMO_PRESETS}


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

    def __init__(self, delegate: object, on_start: Callable[[], None]) -> None:
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
        "on_start",
        "on_complete",
        "on_error",
        "_lock",
    )

    def __init__(
        self,
        delegate: object,
        *,
        external: bool,
        on_start: Callable[[], None],
        on_complete: Callable[[], None],
        on_error: Callable[[], None],
    ) -> None:
        self.delegate = delegate
        self.external = external
        self.adapter_calls = 0
        self.external_network_calls = 0
        self.on_start = on_start
        self.on_complete = on_complete
        self.on_error = on_error
        self._lock = RLock()

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        with self._lock:
            self.adapter_calls += 1
            if self.external:
                self.external_network_calls += 1
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


class CommerceLabService:
    """Own product runs without adding any authorization decision logic."""

    default_mode = "offline"

    def __init__(
        self,
        *,
        repository_root: Path = REPOSITORY_ROOT,
        state_dir: Path | None = None,
    ) -> None:
        self.repository_root = repository_root
        _load_local_environment(repository_root / ".env")
        if state_dir is None:
            state_dir = Path(tempfile.mkdtemp(prefix="mandateguard-commerce-lab-"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = state_dir
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
        self.semantic_cache = SQLiteSemanticCache(state_dir / "semantic-cache.sqlite3")
        self.execution_ledger = SQLiteExecutionLedger(
            state_dir / "execution-ledger.sqlite3"
        )
        self._offline_signing_key = secrets.token_bytes(32)
        self._runs: OrderedDict[str, CommerceRun] = OrderedDict()
        self._requests: dict[str, tuple[str, str, str]] = {}
        self._lock = RLock()

    def close(self) -> None:
        self.semantic_cache.close()
        self.execution_ledger.close()

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
            "presets": [dict(item) for item in DEMO_PRESETS],
            "research": dict(INT3_RESEARCH_FINDING),
            "failure_recovery": [dict(item) for item in FAILURE_RECOVERY_EVIDENCE],
            "safety": {
                "external_calls_on_page_load": 0,
                "buyer_has_razorpay_authority": False,
                "browser_receives_secrets": False,
            },
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "mandateguard-commerce-lab",
            "default_mode": self.default_mode,
            "live_mode_available": self.live_configuration()["available"],
        }

    def start_run(
        self,
        *,
        user_intent: str,
        mode: str,
        request_id: str,
        preset_id: str | None = None,
        top_k: int = DEFAULT_TOP_K,
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
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        normalized_intent = user_intent.strip()
        if mode == "live" and not self.live_configuration()["available"]:
            raise RuntimeError("live test mode is unavailable; check server configuration")
        with self._lock:
            existing = self._requests.get(request_id)
            if existing is not None:
                run_id, existing_mode, existing_intent = existing
                if existing_mode != mode or existing_intent != normalized_intent:
                    raise ValueError("request_id is already bound to another request")
                return self._runs[run_id], True
            run = CommerceRun(
                run_id="run_" + uuid4().hex,
                request_id=request_id,
                user_intent=normalized_intent,
                mode=mode,
                preset_id=preset_id,
                top_k=top_k,
            )
            self._runs[run.run_id] = run
            self._requests[request_id] = (run.run_id, mode, normalized_intent)
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
        top_k: int = DEFAULT_TOP_K,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        run, _ = self.start_run(
            user_intent=user_intent,
            mode=mode,
            request_id=request_id or ("test_" + uuid4().hex),
            preset_id=preset_id,
            top_k=top_k,
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
                now=context.evaluated_at,
                config=context.execution_runtime.config,
                verifier=context.execution_runtime.verifier,
                ledger=context.execution_runtime.ledger,
                client=context.client,
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
            if run.mode == "offline":
                buyer_delegate: CommerceBuyer = ToolDrivenOfflineBuyer(tools)
                embedding = HashingEmbeddingProvider()
                semantic_delegate = TimedSemanticModel(DeterministicSemanticModel())
                evaluated_at = _OFFLINE_EVALUATED_AT
            else:
                from openai import OpenAI

                client = OpenAI()
                semantic_model_id = os.environ["MANDATEGUARD_SEMANTIC_MODEL"]
                buyer_model_id = (
                    os.environ.get("MANDATEGUARD_BUYER_MODEL") or semantic_model_id
                )
                embedding_model_id = os.environ.get(
                    "MANDATEGUARD_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
                )
                buyer_delegate = OpenAIResponsesBuyer(
                    client=client,
                    model_id=buyer_model_id,
                    tools=tools,
                )
                embedding = OpenAIEmbeddingProvider(
                    client=client, model_id=embedding_model_id
                )
                semantic_usage = ResponsesUsageCapture(client.responses)
                semantic_delegate = TimedSemanticModel(
                    OpenAIResponsesSemanticModel(
                        client=type("ResponsesClient", (), {"responses": semantic_usage})(),
                        model_id=semantic_model_id,
                    ),
                    usage_source=semantic_usage,
                )
                evaluated_at = datetime.now(timezone.utc)

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
                external = False
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
                external = True

            execution_client = ObservedOrdersClient(
                provider,
                external=external,
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
                alpha=DEFAULT_ALPHA,
                execute=True,
                execution_runtime=runtime,
                decision_nonce=decision_nonce,
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
            context = _RunContext(
                checkout=checkout,
                execution_runtime=runtime,
                client=execution_client,
                evaluated_at=evaluated_at,
                store=self.store,
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
                }
            )
        cache_payload = dict(checkout.trace.cache)
        if cache_payload.get("status") is None:
            cache_payload["status"] = "NOT_USED"
        final_reason = self._final_reason(checkout)
        execution = self._present_execution(run, context)
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

    @staticmethod
    def _present_execution(run: CommerceRun, context: _RunContext) -> dict[str, Any]:
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
        signature_valid = (
            context.execution_runtime.verifier.verify(capability)
            is SignatureVerification.VALID
        )
        transaction_bound = (
            capability.payload.transaction_body_sha256
            == transaction_body_sha256(checkout.transaction)
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
        return {
            "status": "ORDER_CREATED" if order is not None else "EXECUTION_ERROR",
            "razorpay_calls": context.client.adapter_calls,
            "external_network_calls": context.client.external_network_calls,
            "reason": None,
            "capability": {
                "signature_verified": signature_valid,
                "transaction_bound": transaction_bound,
                "request_bound": (
                    isinstance(receipt, ExecutionReceipt)
                    and receipt.execution_request_sha256
                    == capability.payload.execution_request_sha256
                ),
                "merchant_bound": (
                    capability.payload.merchant_id
                    == checkout.transaction.payload.merchant_id
                    == context.execution_runtime.config.merchant_id
                ),
                "expiry_valid": context.evaluated_at < capability.payload.expires_at,
                "single_use": (
                    ledger_record is not None
                    and ledger_record.status is ExecutionLedgerStatus.SUCCEEDED
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
