"""Run the INT-1 agentic commerce checkout demo; execution is opt-in."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = REPOSITORY_ROOT / "fixtures" / "agentic_commerce"
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "mandateguard-agentic-semantic.sqlite3"
DEFAULT_LEDGER = Path(tempfile.gettempdir()) / "mandateguard-agentic-execution.sqlite3"
DEFAULT_MANDATE_STATE = Path(tempfile.gettempdir()) / "mandateguard-agentic-mandates.sqlite3"
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mandateguard.execution import (  # noqa: E402
    HMACSHA256Signer,
    HMACSHA256Verifier,
    RazorpayTestOrdersAdapter,
    SQLiteExecutionLedger,
    SQLiteMandateStateRegistry,
    TrustedExecutionConfig,
)
from mandateguard.intelligence import (  # noqa: E402
    DeterministicCommerceBuyer,
    ExecutionRuntime,
    OpenAIResponsesBuyer,
    SQLiteSemanticCache,
    TrustedCommerceStore,
    run_agentic_checkout,
)
from mandateguard.intelligence.models import BuyerOutput  # noqa: E402
from mandateguard.intelligence.offline import (  # noqa: E402
    DeterministicSemanticModel,
    ResponsesUsageCapture,
    TimedSemanticModel,
)
from mandateguard.intelligence.retrieval import (  # noqa: E402
    DEFAULT_ALPHA,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_TOP_K,
    HashingEmbeddingProvider,
    HybridRetriever,
    OpenAIEmbeddingProvider,
)
from mandateguard.intelligence.tools import CommerceTools  # noqa: E402
from mandateguard.semantic import (  # noqa: E402
    OpenAIResponsesSemanticModel,
    SemanticVerifier,
)


@dataclass(frozen=True, slots=True)
class _FixedBuyer:
    output: BuyerOutput

    @property
    def model_id(self) -> str:
        return self.output.model_id

    def purchase(self, _user_intent: str) -> BuyerOutput:
        return self.output


def _load_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _live_dependencies(store: TrustedCommerceStore):
    _load_environment()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("--live-ai requires OPENAI_API_KEY")
    semantic_model = os.environ.get("MANDATEGUARD_SEMANTIC_MODEL")
    if not semantic_model:
        raise RuntimeError("--live-ai requires MANDATEGUARD_SEMANTIC_MODEL")
    buyer_model = os.environ.get("MANDATEGUARD_BUYER_MODEL") or semantic_model
    embedding_model = os.environ.get(
        "MANDATEGUARD_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
    )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("--live-ai requires the OpenAI Python package") from exc
    client = OpenAI()
    buyer = OpenAIResponsesBuyer(
        client=client,
        model_id=buyer_model,
        tools=CommerceTools(store),
    )
    embedding = OpenAIEmbeddingProvider(client=client, model_id=embedding_model)
    semantic_usage = ResponsesUsageCapture(client.responses)
    semantic = TimedSemanticModel(
        OpenAIResponsesSemanticModel(
            client=SimpleNamespace(responses=semantic_usage),
            model_id=semantic_model,
        ),
        usage_source=semantic_usage,
    )
    return buyer, embedding, semantic


def _execution_runtime(
    merchant_id: str,
) -> tuple[ExecutionRuntime, SQLiteExecutionLedger, SQLiteMandateStateRegistry]:
    _load_environment()
    key_id = _required_environment("RAZORPAY_KEY_ID")
    key_secret = _required_environment("RAZORPAY_KEY_SECRET")
    hmac_key = _required_environment("MANDATEGUARD_EXECUTION_HMAC_KEY").encode(
        "utf-8"
    )
    if not key_id.startswith("rzp_test_"):
        raise RuntimeError("RAZORPAY_KEY_ID must begin with rzp_test_")
    if len(hmac_key) < 32:
        raise RuntimeError("MANDATEGUARD_EXECUTION_HMAC_KEY must be at least 32 bytes")
    account_scope = "razorpay-test-" + sha256(key_id.encode("utf-8")).hexdigest()[:16]
    config = TrustedExecutionConfig(
        merchant_id=merchant_id,
        account_scope=account_scope,
    )
    signer = HMACSHA256Signer(key_id="agentic-commerce-hmac-v1", key=hmac_key)
    ledger = SQLiteExecutionLedger(DEFAULT_LEDGER)
    mandate_registry = SQLiteMandateStateRegistry(DEFAULT_MANDATE_STATE)
    return (
        ExecutionRuntime(
            config=config,
            signer=signer,
            verifier=HMACSHA256Verifier(
                {"agentic-commerce-hmac-v1": hmac_key}
            ),
            ledger=ledger,
            mandate_state_registry=mandate_registry,
            client=RazorpayTestOrdersAdapter(
                key_id=key_id,
                key_secret=key_secret,
            ),
        ),
        ledger,
        mandate_registry,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True, help="natural-language purchase intent")
    parser.add_argument("--live-ai", action="store_true", help="use OpenAI buyer, embeddings, and semantic model")
    parser.add_argument("--execute", action="store_true", help="opt in to Razorpay Test Mode Order creation")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_FIXTURES / "merchant_catalog.json",
        help="registered synthetic merchant catalog",
    )
    parser.add_argument(
        "--terms",
        type=Path,
        default=DEFAULT_FIXTURES / "merchant_terms.json",
        help="registered synthetic merchant evidence",
    )
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--trace-json", type=Path)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(argv)

    cache: SQLiteSemanticCache | None = None
    execution_ledger: SQLiteExecutionLedger | None = None
    mandate_registry: SQLiteMandateStateRegistry | None = None
    try:
        store = TrustedCommerceStore.from_files(
            catalog_path=args.catalog,
            merchant_terms_path=args.terms,
        )
        if args.live_ai:
            buyer, embedding, semantic_model = _live_dependencies(store)
        else:
            buyer = DeterministicCommerceBuyer(store)
            embedding = HashingEmbeddingProvider()
            semantic_model = TimedSemanticModel(DeterministicSemanticModel())
        cache = SQLiteSemanticCache(args.cache)
        verifier = SemanticVerifier(model=semantic_model, cache=cache)

        runtime = None
        if args.execute:
            # Resolve the buyer first only to select trusted merchant execution config;
            # the fixed typed output is then consumed by the same orchestration path.
            buyer_output = buyer.purchase(args.intent)
            buyer = _FixedBuyer(buyer_output)
            runtime, execution_ledger, mandate_registry = _execution_runtime(
                buyer_output.proposal.merchant_id
            )

        result = run_agentic_checkout(
            user_intent=args.intent,
            buyer=buyer,
            store=store,
            retriever=HybridRetriever(embedding),
            semantic_verifier=verifier,
            top_k=args.top_k,
            alpha=args.alpha,
            execute=args.execute,
            execution_runtime=runtime,
        )
        trace = result.trace.to_mapping()
        if args.trace_json is not None:
            args.trace_json.parent.mkdir(parents=True, exist_ok=True)
            args.trace_json.write_text(
                json.dumps(trace, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(f"decision={trace['decision']}")
        print(
            "proposal="
            f"{trace['buyer']['selected_merchant']}/"
            f"{trace['buyer']['selected_sku']}"
        )
        print(f"cache={trace['cache']['status']}")
        print(f"execution={trace['execution']['status']}")
        if args.trace_json is not None:
            print(f"trace={args.trace_json}")
        return 0
    except (TypeError, ValueError, RuntimeError) as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 1
    finally:
        if cache is not None:
            cache.close()
        if execution_ledger is not None:
            execution_ledger.close()
        if mandate_registry is not None:
            mandate_registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
