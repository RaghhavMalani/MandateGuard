"""Opt-in real Razorpay Test Mode Order smoke test; never imported by pytest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import secrets
import tempfile

from mandateguard.core.hashing import (
    CommittedHashes,
    catalog_snapshot_sha256,
    transaction_body_sha256,
)
from mandateguard.core.nonce_ledger import NonceLedgerState
from mandateguard.execution import (
    ExecutionError,
    ExecutionRefusal,
    HMACSHA256Signer,
    HMACSHA256Verifier,
    RazorpayTestOrdersAdapter,
    SQLiteExecutionLedger,
    SignedExecutionAuthorization,
    TrustedExecutionConfig,
    execute_razorpay_order,
    issue_execution_authorization,
)
from mandateguard.models.catalog import CatalogItem, CatalogSnapshot
from mandateguard.models.mandate import (
    HardConstraints,
    IssuerAttestation,
    Mandate,
    MandateConstraints,
    MandatePayload,
)
from mandateguard.models.transaction import (
    Transaction,
    TransactionLine,
    TransactionPayload,
)
from mandateguard.replay.scenario import ReplayScenario
from mandateguard.semantic.orchestration import authorize_transaction


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def _inputs(now: datetime) -> tuple[Mandate, Transaction, CatalogSnapshot]:
    mandate = Mandate(
        payload=MandatePayload(
            mandate_id="d6000000-0000-4000-8000-000000000001",
            nonce="d6_smoke_mandate_nonce_0001",
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=15),
            subject_ref="manual-smoke-subject",
            currency="INR",
            constraints=MandateConstraints(
                hard=HardConstraints(
                    max_total_minor=100,
                    max_quantity=1,
                    recurring_allowed=False,
                    merchant_allowlist=("razorpay-smoke-merchant",),
                    sku_allowlist=("d6-smoke-sku",),
                ),
                semantic=(),
            ),
        ),
        issuer_attestation=IssuerAttestation(
            assurance="DECLARED_ONLY",
            issuer_id="manual-smoke-composition-root",
        ),
    )
    line = TransactionLine(
        sku="d6-smoke-sku",
        effective_unit_price_minor=100,
        quantity=1,
        line_total_minor=100,
        recurring=False,
    )
    payload = TransactionPayload(
        transaction_id="d6-manual-smoke-transaction",
        merchant_id="razorpay-smoke-merchant",
        cart_currency="INR",
        order_currency="INR",
        declared_order_total_minor=100,
        declared_aggregate_quantity=1,
        cart_recurring=False,
        order_recurring=False,
        lines=(line,),
    )
    transaction = Transaction(
        payload=payload,
        declared_transaction_hash=transaction_body_sha256(payload),
    )
    catalog = CatalogSnapshot(
        snapshot_id="d6-manual-smoke-catalog",
        merchant_id="razorpay-smoke-merchant",
        currency="INR",
        items=(
            CatalogItem(
                sku="d6-smoke-sku",
                merchant_id="razorpay-smoke-merchant",
                effective_unit_price_minor=100,
                recurring=False,
            ),
        ),
    )
    return mandate, transaction, catalog


def main() -> int:
    key_id = _required_environment("RAZORPAY_KEY_ID")
    key_secret = _required_environment("RAZORPAY_KEY_SECRET")
    hmac_key = _required_environment("MANDATEGUARD_EXECUTION_HMAC_KEY").encode(
        "utf-8"
    )
    if not key_id.startswith("rzp_test_"):
        raise SystemExit("RAZORPAY_KEY_ID must begin with rzp_test_")
    if len(hmac_key) < 32:
        raise SystemExit("MANDATEGUARD_EXECUTION_HMAC_KEY must be at least 32 bytes")

    now = datetime.now(timezone.utc)
    mandate, transaction, catalog = _inputs(now)
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
        replay_seed=6001,
        evaluated_at=now,
    )
    result = authorize_transaction(
        mandate=scenario.mandate,
        transaction=scenario.transaction,
        catalog_snapshot=scenario.catalog_snapshot,
        server_time=scenario.server_time,
        nonce_state=scenario.nonce_state,
        committed_hashes=scenario.psp_committed_hashes,
        replay_seed=scenario.replay_seed,
        evaluated_at=scenario.evaluated_at,
    )
    decision_nonce = secrets.token_urlsafe(24)
    account_scope = "razorpay-test-" + sha256(key_id.encode("utf-8")).hexdigest()[:16]
    config = TrustedExecutionConfig(
        merchant_id=transaction.payload.merchant_id,
        account_scope=account_scope,
    )
    signer = HMACSHA256Signer(key_id="manual-smoke-hmac-v1", key=hmac_key)
    capability = issue_execution_authorization(
        authorization_result=result,
        authorization_scenario=scenario,
        semantic_evidence=None,
        semantic_verifier=None,
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
        decision_nonce=decision_nonce,
        config=config,
        signer=signer,
    )
    if not isinstance(capability, SignedExecutionAuthorization):
        assert isinstance(capability, ExecutionRefusal)
        raise SystemExit(f"capability issuance refused: {capability.reason.value}")

    ledger_path = Path(tempfile.gettempdir()) / "mandateguard-d6-execution.sqlite3"
    with SQLiteExecutionLedger(ledger_path) as ledger:
        try:
            receipt = execute_razorpay_order(
                authorization=capability,
                authorization_result=result,
                mandate=mandate,
                transaction=transaction,
                now=now,
                config=config,
                verifier=HMACSHA256Verifier({"manual-smoke-hmac-v1": hmac_key}),
                ledger=ledger,
                client=RazorpayTestOrdersAdapter(
                    key_id=key_id,
                    key_secret=key_secret,
                ),
            )
        except ExecutionError as error:
            raise SystemExit(str(error)) from None
    if isinstance(receipt, ExecutionRefusal):
        raise SystemExit(f"execution refused: {receipt.reason.value}")

    print(f"MandateGuard final action: {result.final_action.value}")
    print(f"transaction hash: {capability.payload.transaction_body_sha256}")
    print(f"execution request hash: {receipt.execution_request_sha256}")
    print(f"receipt: {receipt.receipt}")
    print(f"Razorpay order ID: {receipt.razorpay_order_id}")
    print(f"status: {receipt.status}")
    print(f"amount: {receipt.amount}")
    print(f"currency: {receipt.currency}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
