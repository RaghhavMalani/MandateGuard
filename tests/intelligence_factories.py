"""Deterministic INT-1 test composition helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mandateguard.execution import (
    HMACSHA256Signer,
    HMACSHA256Verifier,
    InMemoryMandateStateRegistry,
    SQLiteExecutionLedger,
    TrustedExecutionConfig,
)
from mandateguard.execution.models import RazorpayOrderRequest, RazorpayOrderResult
from mandateguard.intelligence.buyer import DeterministicCommerceBuyer
from mandateguard.intelligence.cache import SQLiteSemanticCache
from mandateguard.intelligence.models import BuyerOutput
from mandateguard.intelligence.offline import (
    DeterministicSemanticModel,
    TimedSemanticModel,
)
from mandateguard.intelligence.orchestration import (
    AgenticCheckoutResult,
    ExecutionRuntime,
    run_agentic_checkout,
)
from mandateguard.intelligence.retrieval import (
    HashingEmbeddingProvider,
    HybridRetriever,
)
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.semantic.verifier import SemanticVerifier


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "agentic_commerce"
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
ALLOW_INTENT = (
    "Buy the StudyGlow Desk Lamp under INR 2000 for individual study; "
    "avoid subscriptions."
)
BLOCK_INTENT = (
    "Buy the Market Edge Decision Course under INR 3000 for professional "
    "development; avoid gambling."
)
REVIEW_INTENT = (
    "Buy the Flexi Desk Companion under INR 1500 for individual study; "
    "avoid subscriptions."
)
SYNTHETIC_KEY = bytes(range(32))


def make_store() -> TrustedCommerceStore:
    return TrustedCommerceStore.from_files(
        catalog_path=FIXTURES / "merchant_catalog.json",
        merchant_terms_path=FIXTURES / "merchant_terms.json",
    )


@dataclass
class ScriptedBuyer:
    output: BuyerOutput
    model_id: str = "scripted-buyer-v1"
    calls: list[str] = field(default_factory=list)

    def purchase(self, user_intent: str) -> BuyerOutput:
        self.calls.append(user_intent)
        return self.output


@dataclass
class RecordingOrdersClient:
    calls: list[RazorpayOrderRequest] = field(default_factory=list)

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        self.calls.append(request)
        return RazorpayOrderResult(
            razorpay_order_id="order_agentic_test",
            amount=request.amount,
            currency=request.currency,
            receipt=request.receipt,
            status="created",
        )


def make_execution_runtime(
    tmp_path: Path, merchant_id: str, client: RecordingOrdersClient
) -> tuple[ExecutionRuntime, SQLiteExecutionLedger]:
    config = TrustedExecutionConfig(
        merchant_id=merchant_id,
        account_scope="synthetic-agentic-account",
    )
    ledger = SQLiteExecutionLedger(tmp_path / f"{merchant_id}-ledger.sqlite3")
    runtime = ExecutionRuntime(
        config=config,
        signer=HMACSHA256Signer(key_id="agentic-test-key", key=SYNTHETIC_KEY),
        verifier=HMACSHA256Verifier({"agentic-test-key": SYNTHETIC_KEY}),
        ledger=ledger,
        client=client,
        mandate_state_registry=InMemoryMandateStateRegistry(),
    )
    return runtime, ledger


CHECKOUT_IDENTITY_SEED = "offline-checkout-fixture-run"


def run_offline(
    tmp_path: Path,
    intent: str,
    *,
    cache_path: Path | None = None,
    semantic_model: DeterministicSemanticModel | None = None,
    execute: bool = False,
    execution_runtime: ExecutionRuntime | None = None,
    mandate_identity_seed: str = CHECKOUT_IDENTITY_SEED,
) -> tuple[AgenticCheckoutResult, SQLiteSemanticCache, DeterministicSemanticModel]:
    store = make_store()
    model = semantic_model or DeterministicSemanticModel()
    cache = SQLiteSemanticCache(cache_path or tmp_path / "semantic.sqlite3")
    verifier = SemanticVerifier(model=TimedSemanticModel(model), cache=cache)
    result = run_agentic_checkout(
        user_intent=intent,
        buyer=DeterministicCommerceBuyer(store),
        store=store,
        retriever=HybridRetriever(HashingEmbeddingProvider()),
        semantic_verifier=verifier,
        evaluated_at=NOW,
        execute=execute,
        execution_runtime=execution_runtime,
        decision_nonce="agentic_decision_nonce_12345" if execute else None,
        mandate_identity_seed=mandate_identity_seed,
    )
    return result, cache, model
