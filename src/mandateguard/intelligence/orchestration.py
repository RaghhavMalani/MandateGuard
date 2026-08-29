"""End-to-end buyer -> retrieval -> MandateGuard -> optional D6 execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from time import perf_counter
from typing import Any
from uuid import UUID

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.execution.authorization import issue_execution_authorization
from mandateguard.execution.executor import execute_razorpay_order
from mandateguard.execution.models import (
    ExecutionError,
    ExecutionReceipt,
    ExecutionRefusal,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
)
from mandateguard.execution.razorpay import RazorpayOrdersClient
from mandateguard.execution.signing import ExecutionSigner, ExecutionVerifier
from mandateguard.execution.ledger import ExecutionLedger
from mandateguard.intelligence.buyer import CommerceBuyer
from mandateguard.intelligence.models import (
    AgenticCheckoutTrace,
    BuyerOutput,
    CacheStatus,
    ExecutionStatus,
    InterpretedPurchaseIntent,
    PurchaseProposal,
    RetrievalResult,
    RetrievalSource,
)
from mandateguard.intelligence.retrieval.hybrid import (
    DEFAULT_ALPHA,
    DEFAULT_TOP_K,
    HybridRetriever,
    RetrievalMode,
)
from mandateguard.intelligence.retrieval.query import (
    build_retrieval_query,
    mandate_documents,
)
from mandateguard.intelligence.store import TrustedCommerceStore
from mandateguard.models.decision import DecisionAction
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
    SemanticConstraint,
)
from mandateguard.models.transaction import (
    Transaction,
    TransactionLine,
    TransactionPayload,
)
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.cache import (
    InMemorySemanticCache,
    SemanticCacheError,
    SemanticCacheIntegrityError,
)
from mandateguard.semantic.evidence import (
    SemanticEvidence,
    SemanticEvidenceBundle,
    semantic_evidence_sha256,
)
from mandateguard.semantic.models import (
    AuthorizationResult,
    SemanticRequest,
)
from mandateguard.semantic.orchestration import authorize_transaction
from mandateguard.semantic.verifier import SemanticMode, SemanticVerifier


class AgenticCheckoutError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    """Trusted execution dependencies that are never reachable by the buyer."""

    config: TrustedExecutionConfig
    signer: ExecutionSigner
    verifier: ExecutionVerifier
    ledger: ExecutionLedger
    client: RazorpayOrdersClient

    def __post_init__(self) -> None:
        if not isinstance(self.config, TrustedExecutionConfig):
            raise TypeError("config must be TrustedExecutionConfig")


@dataclass(frozen=True, slots=True)
class AgenticCheckoutResult:
    trace: AgenticCheckoutTrace
    buyer_output: BuyerOutput
    mandate: Mandate
    transaction: Transaction
    retrieval: RetrievalResult
    authorization_result: AuthorizationResult
    execution_authorization: SignedExecutionAuthorization | None
    execution_result: ExecutionReceipt | ExecutionRefusal | None


class _CacheFailureModel:
    __slots__ = ("model_id", "reason")

    def __init__(self, model_id: str, reason: str) -> None:
        self.model_id = model_id
        self.reason = reason

    def evaluate(self, request: SemanticRequest) -> object:
        return {
            "constraint_results": [
                {
                    "constraint_id": constraint.constraint_id,
                    "status": "ABSTAIN",
                    "reason": self.reason,
                }
                for constraint in request.constraints
            ]
        }


def _semantic_constraints(
    interpreted: InterpretedPurchaseIntent,
) -> tuple[SemanticConstraint, ...]:
    constraints: list[SemanticConstraint] = []
    if interpreted.purpose is not None:
        constraints.append(
            SemanticConstraint(
                constraint_id="purpose.1",
                kind="purpose",
                text=(
                    f"Declared purchase purpose: {interpreted.purpose}. "
                    "Trusted evidence must establish suitability."
                ),
            )
        )
    constraints.extend(
        SemanticConstraint(
            constraint_id=f"exclusion.{index}",
            kind="exclusion",
            text=f"Excluded product characteristic: {exclusion}.",
        )
        for index, exclusion in enumerate(interpreted.exclusions, start=1)
    )
    return tuple(constraints)


def build_mandate_from_intent(
    *,
    user_intent: str,
    interpreted: InterpretedPurchaseIntent,
    evaluated_at: datetime,
    subject_ref: str = "agentic-commerce-user",
) -> Mandate:
    """Construct a bounded V1 mandate from one validated intent interpretation."""

    if not isinstance(interpreted, InterpretedPurchaseIntent):
        raise TypeError("interpreted must be InterpretedPurchaseIntent")
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError("evaluated_at must be timezone-aware")
    digest = sha256(user_intent.strip().encode("utf-8")).digest()
    mandate_id = str(UUID(bytes=digest[:16], version=4))
    nonce = "mg_intent_" + digest.hex()[:32]
    return Mandate(
        payload=MandatePayload(
            mandate_id=mandate_id,
            nonce=nonce,
            issued_at=evaluated_at - timedelta(seconds=1),
            expires_at=evaluated_at + timedelta(minutes=15),
            subject_ref=subject_ref,
            currency=interpreted.currency,
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=interpreted.max_total_minor,
                    max_quantity=interpreted.quantity,
                    recurring_allowed=interpreted.recurring_allowed,
                    merchant_allowlist=interpreted.merchant_allowlist,
                    sku_allowlist=interpreted.sku_allowlist,
                ),
                semantic=_semantic_constraints(interpreted),
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY",
            issuer_id="agentic-commerce-intent-interpreter-v1",
        ),
    )


def _transaction_for_proposal(
    *, proposal: PurchaseProposal, store: TrustedCommerceStore
) -> Transaction:
    product = store.get_product(
        merchant_id=proposal.merchant_id, sku=proposal.sku
    )
    proposal_hash = sha256(
        repr(
            (
                proposal.merchant_id,
                proposal.sku,
                proposal.quantity,
                proposal.declared_total_minor,
                proposal.currency,
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    line = TransactionLine(
        sku=proposal.sku,
        effective_unit_price_minor=product.effective_unit_price_minor,
        quantity=proposal.quantity,
        line_total_minor=proposal.declared_total_minor,
        recurring=product.recurring,
    )
    payload = TransactionPayload(
        transaction_id=f"agentic-{proposal_hash}",
        merchant_id=proposal.merchant_id,
        cart_currency=proposal.currency,
        order_currency=proposal.currency,
        declared_order_total_minor=proposal.declared_total_minor,
        declared_aggregate_quantity=proposal.quantity,
        cart_recurring=product.recurring,
        order_recurring=product.recurring,
        lines=(line,),
    )
    return Transaction(
        payload=payload,
        declared_transaction_hash=transaction_body_sha256(payload),
    )


def _selected_semantic_evidence(
    *,
    retrieval: RetrievalResult,
    store: TrustedCommerceStore,
    proposal: PurchaseProposal,
) -> SemanticEvidence:
    entries = []
    for ranked in retrieval.ranked_documents:
        document = ranked.document
        if document.source_type is not RetrievalSource.MERCHANT_EVIDENCE:
            continue
        if document.evidence_id is None:
            raise AgenticCheckoutError("retrieval evidence ID is unavailable")
        resolved = store.resolve_evidence_ids(
            (document.evidence_id,),
            merchant_id=proposal.merchant_id,
            sku=proposal.sku,
        )
        entries.extend(resolved)
    unique = {entry.evidence_id: entry for entry in entries}
    if not unique:
        raise AgenticCheckoutError(
            "retrieval produced no trusted merchant evidence for semantic authorization"
        )
    bundle = SemanticEvidenceBundle(
        merchant_id=proposal.merchant_id,
        entries=tuple(unique.values()),
    )
    return SemanticEvidence(
        bundle=bundle,
        semantic_evidence_sha256=semantic_evidence_sha256(bundle),
    )


def _safe_authorize(
    *,
    scenario: ReplayScenario,
    semantic_evidence: SemanticEvidence,
    semantic_verifier: SemanticVerifier,
) -> tuple[AuthorizationResult, bool, str | None]:
    kwargs: dict[str, Any] = {
        "mandate": scenario.mandate,
        "transaction": scenario.transaction,
        "catalog_snapshot": scenario.catalog_snapshot,
        "server_time": scenario.server_time,
        "nonce_state": scenario.nonce_state,
        "committed_hashes": scenario.psp_committed_hashes,
        "replay_seed": scenario.replay_seed,
        "evaluated_at": scenario.evaluated_at,
        "semantic_evidence": semantic_evidence,
        "semantic_verifier": semantic_verifier,
        "semantic_mode": SemanticMode.LIVE,
    }
    try:
        return authorize_transaction(**kwargs), False, None
    except SemanticCacheIntegrityError:
        failure_reason = "CACHE_INTEGRITY_FAILURE"
    except SemanticCacheError:
        failure_reason = "CACHE_UNAVAILABLE"
    fallback = SemanticVerifier(
        model=_CacheFailureModel(semantic_verifier.model_id, failure_reason),
        cache=InMemorySemanticCache(),
        prompt_version=semantic_verifier.prompt_version,
        detector_version=semantic_verifier.detector_version,
    )
    kwargs["semantic_verifier"] = fallback
    return authorize_transaction(**kwargs), True, failure_reason


def _authorization_trace(result: AuthorizationResult) -> dict[str, Any]:
    deterministic = result.deterministic_decision
    semantic = result.semantic_decision
    return {
        "tier_a_statuses": [
            {
                "family": item.family.value,
                "status": item.status.value,
                "reason": (
                    item.finding.message if item.finding is not None else item.reason
                ),
            }
            for item in deterministic.tier_a_results
        ],
        "tier_b_findings": [
            {
                "family": finding.family.value,
                "message": finding.message,
                "details": dict(finding.details),
            }
            for finding in deterministic.findings
            if finding.family.value.startswith("B")
        ],
        "semantic_verdict": semantic.verdict.value if semantic is not None else None,
        "semantic_reason": (
            [item.reason for item in semantic.constraint_results]
            if semantic is not None
            else []
        ),
    }


def run_agentic_checkout(
    *,
    user_intent: str,
    buyer: CommerceBuyer,
    store: TrustedCommerceStore,
    retriever: HybridRetriever,
    semantic_verifier: SemanticVerifier,
    evaluated_at: datetime | None = None,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = DEFAULT_ALPHA,
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID,
    execute: bool = False,
    execution_runtime: ExecutionRuntime | None = None,
    decision_nonce: str | None = None,
) -> AgenticCheckoutResult:
    """Run one vertical slice. Payment I/O is opt-in and ALLOW-capability gated."""

    if not isinstance(user_intent, str) or not user_intent.strip() or len(user_intent) > 8000:
        raise ValueError("user_intent must be a bounded non-empty string")
    if not isinstance(buyer, CommerceBuyer):
        raise TypeError("buyer must implement CommerceBuyer")
    if not isinstance(store, TrustedCommerceStore):
        raise TypeError("store must be TrustedCommerceStore")
    if not isinstance(retriever, HybridRetriever):
        raise TypeError("retriever must be HybridRetriever")
    if not isinstance(semantic_verifier, SemanticVerifier):
        raise TypeError("semantic_verifier must be SemanticVerifier")
    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    total_started = perf_counter()
    buyer_started = perf_counter()
    buyer_output = buyer.purchase(user_intent.strip())
    buyer_latency_ms = (perf_counter() - buyer_started) * 1000.0

    proposal = buyer_output.proposal
    product = store.get_product(
        merchant_id=proposal.merchant_id, sku=proposal.sku
    )
    # Buyer IDs request evidence; this lookup proves all requests are registered.
    store.resolve_evidence_ids(
        proposal.selected_evidence_ids,
        merchant_id=proposal.merchant_id,
        sku=proposal.sku,
    )
    mandate = build_mandate_from_intent(
        user_intent=user_intent,
        interpreted=buyer_output.interpreted_intent,
        evaluated_at=now,
    )
    transaction = _transaction_for_proposal(proposal=proposal, store=store)
    catalog = store.catalog_snapshot(merchant_id=proposal.merchant_id)

    retrieval_started = perf_counter()
    query, query_hash = build_retrieval_query(
        user_intent=user_intent,
        mandate=mandate,
        proposal=proposal,
        product=product,
    )
    documents = mandate_documents(mandate) + store.retrieval_documents(
        merchant_id=proposal.merchant_id, sku=proposal.sku
    )
    retrieval = retriever.retrieve(
        query=query,
        query_sha256=query_hash,
        documents=documents,
        alpha=alpha,
        top_k=top_k,
        mode=retrieval_mode,
    )
    retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000.0
    semantic_evidence = _selected_semantic_evidence(
        retrieval=retrieval, store=store, proposal=proposal
    )

    scenario = ReplayScenario(
        mandate=mandate,
        transaction=transaction,
        catalog_snapshot=catalog,
        server_time=now,
        nonce_state=NonceLedgerState(),
        psp_committed_hashes=CommittedHashes(
            transaction_sha256=transaction_body_sha256(transaction),
            catalog_snapshot_sha256=catalog_snapshot_sha256(catalog),
        ),
        replay_seed=1001,
        evaluated_at=now,
    )
    authorization_started = perf_counter()
    authorization, cache_failed, cache_failure_reason = _safe_authorize(
        scenario=scenario,
        semantic_evidence=semantic_evidence,
        semantic_verifier=semantic_verifier,
    )
    authorization_latency_ms = (perf_counter() - authorization_started) * 1000.0
    cache = semantic_verifier.cache
    cache_status = getattr(cache, "last_status", None)
    if cache_failed:
        cache_status = CacheStatus.MISS
    semantic_latency_ms = 0.0
    semantic_input_tokens: int | None = None
    semantic_output_tokens: int | None = None
    if cache_status is not CacheStatus.HIT and not cache_failed:
        semantic_latency_ms = float(
            getattr(semantic_verifier.model, "last_latency_ms", 0.0)
        )
        semantic_input_tokens = getattr(
            semantic_verifier.model, "last_input_tokens", None
        )
        semantic_output_tokens = getattr(
            semantic_verifier.model, "last_output_tokens", None
        )
    initial_cache_key = (
        authorization.semantic_decision.semantic_input_sha256
        if authorization.semantic_decision is not None
        else None
    )

    capability: SignedExecutionAuthorization | None = None
    execution_result: ExecutionReceipt | ExecutionRefusal | None = None
    execution_status = ExecutionStatus.NOT_REQUESTED
    execution_detail: str | None = None
    if execute and authorization.final_action is not DecisionAction.ALLOW:
        execution_status = ExecutionStatus.NOT_AUTHORIZED
    elif execute:
        if execution_runtime is None:
            execution_status = ExecutionStatus.ERROR
            execution_detail = "execution runtime is not configured"
        elif execution_runtime.config.merchant_id != proposal.merchant_id:
            execution_status = ExecutionStatus.ERROR
            execution_detail = "execution merchant configuration mismatch"
        else:
            nonce = decision_nonce or secrets.token_urlsafe(24)
            issued = issue_execution_authorization(
                authorization_result=authorization,
                authorization_scenario=scenario,
                semantic_evidence=semantic_evidence,
                semantic_verifier=semantic_verifier,
                issued_at=now,
                expires_at=now + timedelta(minutes=2),
                decision_nonce=nonce,
                config=execution_runtime.config,
                signer=execution_runtime.signer,
            )
            if isinstance(issued, ExecutionRefusal):
                execution_status = ExecutionStatus.ERROR
                execution_detail = issued.reason.value
                execution_result = issued
            else:
                capability = issued
                try:
                    execution_result = execute_razorpay_order(
                        authorization=capability,
                        authorization_result=authorization,
                        mandate=mandate,
                        transaction=transaction,
                        now=now,
                        config=execution_runtime.config,
                        verifier=execution_runtime.verifier,
                        ledger=execution_runtime.ledger,
                        client=execution_runtime.client,
                    )
                except ExecutionError as error:
                    execution_status = ExecutionStatus.ERROR
                    execution_detail = error.reason.value
                else:
                    if isinstance(execution_result, ExecutionRefusal):
                        execution_status = ExecutionStatus.ERROR
                        execution_detail = execution_result.reason.value
                    else:
                        execution_status = ExecutionStatus.EXECUTED

    total_latency_ms = (perf_counter() - total_started) * 1000.0
    trace = AgenticCheckoutTrace(
        user_intent=user_intent.strip(),
        buyer={
            "selected_merchant": proposal.merchant_id,
            "selected_sku": proposal.sku,
            "reason": proposal.reason,
            "interpreted_intent": buyer_output.interpreted_intent.to_mapping(),
        },
        retrieval={
            "query": retrieval.query,
            "query_sha256": retrieval.query_sha256,
            "alpha": retrieval.alpha,
            "top_k": retrieval.top_k,
            "evidence_ids": [
                item.document.evidence_id
                for item in retrieval.ranked_documents
                if item.document.evidence_id is not None
            ],
            "scores": [
                {
                    "document_id": item.score.document_id,
                    "source_type": item.score.source_type.value,
                    "lexical_score": item.score.lexical_score,
                    "semantic_score": item.score.semantic_score,
                    "hybrid_score": item.score.hybrid_score,
                }
                for item in retrieval.ranked_documents
            ],
        },
        authorization=_authorization_trace(authorization),
        cache={
            "status": cache_status.value if isinstance(cache_status, CacheStatus) else None,
            "key_prefix": initial_cache_key[:12] if initial_cache_key else None,
            "integrity_failure": cache_failed,
            "failure_reason": cache_failure_reason,
        },
        decision=authorization.final_action.value,
        execution={
            "status": execution_status.value,
            "detail": execution_detail,
        },
        timings={
            "buyer_latency_ms": buyer_latency_ms,
            "retrieval_latency_ms": retrieval_latency_ms,
            "embedding_latency_ms": retrieval.embedding_latency_ms,
            "semantic_latency_ms": semantic_latency_ms,
            "authorization_latency_ms": authorization_latency_ms,
            "total_latency_ms": total_latency_ms,
        },
        models={
            "buyer_model": buyer_output.model_id,
            "embedding_model": (
                retriever.embedding_provider.model_id
                if retriever.embedding_provider is not None
                else "none"
            ),
            "semantic_model": semantic_verifier.model_id,
        },
        usage={
            "buyer_input_tokens": buyer_output.input_tokens,
            "buyer_output_tokens": buyer_output.output_tokens,
            "embedding_input_tokens": retrieval.input_tokens,
            "semantic_input_tokens": semantic_input_tokens,
            "semantic_output_tokens": semantic_output_tokens,
        },
    )
    return AgenticCheckoutResult(
        trace=trace,
        buyer_output=buyer_output,
        mandate=mandate,
        transaction=transaction,
        retrieval=retrieval,
        authorization_result=authorization,
        execution_authorization=capability,
        execution_result=execution_result,
    )
