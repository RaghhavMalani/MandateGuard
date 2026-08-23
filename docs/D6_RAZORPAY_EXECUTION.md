# D6: Capability-scoped Razorpay Test Mode order execution

Status: **Razorpay Test Mode adapter implemented; real API smoke test pending.**

D6 adds an execution boundary around the frozen D1-D5 authorization output. It
creates Razorpay Orders in Test Mode only. It does not implement live payments,
Checkout, payment authorization, capture, refunds, payouts, subscriptions, or
webhooks.

## Signed capability boundary

An existing `AuthorizationResult` can produce an execution capability only
when its final action is `ALLOW` and the complete historical authorization
context reproduces it. `BLOCK` produces
`AUTHORIZATION_BLOCKED`; `REVIEW` produces `AUTHORIZATION_REVIEW_REQUIRED`.
Neither refusal contains a capability that an executor can use.

The issuer accepts one recorded `ReplayScenario` rather than separately
supplied mandate and transaction objects. For a claimed `ALLOW`, it reruns
`authorize_transaction` from the scenario's mandate, transaction, catalog,
server time, nonce state, PSP commitments, replay seed, and evaluation time.
The canonical hash of the reproduced result must equal the supplied result
hash or issuance returns `AUTHORIZATION_CONTEXT_MISMATCH` without signing.

When semantic constraints are present, re-derivation always uses
`SemanticMode.REPLAY`. The exact semantic evidence and integrity-checked cache
record must be available. Missing or invalid replay state returns
`AUTHORIZATION_CONTEXT_UNVERIFIABLE`; issuance never falls back to live model
evaluation and never calls the model.

This proves that the supplied historical policy inputs, when rerun through the
actual Tier A/B policy and recorded Tier C replay result, reproduce the exact
authorization result being signed. It does not prove merchant evidence
authenticity beyond D4's trust assumptions, upstream human intent, or
third-party provenance of the recorded context.

The capability payload commits:

- schema version, `ALLOW` action, execution decision nonce, issue time, and expiry;
- environment, audience, account scope, and MandateGuard merchant ID;
- the canonical mandate-payload hash;
- the canonical transaction-body hash;
- the canonical hash of the exact `AuthorizationResult` used for issuance;
- the canonical exact Razorpay order-request hash; and
- semantic input/output hashes when Tier C participated, otherwise null.

Every payload field is serialized as canonical JSON and signed with
HMAC-SHA256. The signature envelope includes a key ID. Verification uses a
trusted key-ID-to-key mapping and `hmac.compare_digest`; unknown keys and any
payload or signature modification are refused.

HMAC provides integrity and authenticity between components that share the
trusted secret. It does **not** provide third-party non-repudiation or external
attestation.

`ExecutionAuthorizationPayload` is structurally restricted to `ALLOW`;
`BLOCK` and `REVIEW` payload objects cannot be constructed. The execution gate
retains an action check as defense in depth.

Capabilities must have `issued_at < expires_at`, cannot exceed five minutes,
and cannot outlive the mandate. Execution uses an injected clock, refuses at
the exact expiry boundary, and permits at most 30 seconds of future clock skew.
There is no wall-clock read inside the execution policy or gate.

## Exact transaction and request binding

Issuance builds the immutable request from trusted inputs only:

```text
amount   = transaction.payload.declared_order_total_minor
currency = transaction.payload.order_currency
receipt  = "mg_" + first 37 hexadecimal characters of SHA-256(decision_nonce)
```

Buyer code cannot supply amount, currency, receipt, notes, partial-payment
flags, or arbitrary Razorpay fields. The receipt is a 40-character printable
ASCII, nonce-derived, collision-resistant identifier. It remains identical for
the same capability and contains no buyer-provided free text.

At execution, the gate does not trust copied hashes or a caller-provided order
request. It recomputes the mandate hash and `AuthorizationResult` hash,
recomputes the transaction body hash from the supplied `Transaction`, rebuilds
the three-field request from that transaction and the signed decision nonce,
and recomputes the exact request hash. Only that rebuilt request can enter the
Razorpay adapter.

This independently closes both transaction substitution and outbound request
substitution. The adapter serializes only `amount`, `currency`, and `receipt`.

The frozen `declared_transaction_hash` wrapper field remains outside
`transaction_body_sha256` and is checked by frozen B5. No D6 amount, currency,
receipt, or provider request field depends on that wrapper value.

## Merchant and Test Mode scope

Trusted configuration maps one MandateGuard merchant ID to one Razorpay test
account scope. Capabilities bind `environment=TEST` and
`audience=razorpay-orders`; the executor requires exact environment, audience,
account-scope, and merchant matches. Transaction fields never select
credentials.

The adapter accepts only key IDs beginning with `rzp_test_`. There is no bypass.
Its API origin is fixed to `https://api.razorpay.com`; tests replace the HTTP
transport, not the origin. Credentials exist only in the manual composition
root and never enter domain objects, errors, audit data, or committed fixtures.

## Single use and side-effect ordering

The SQLite execution ledger persists:

```text
decision_nonce | execution_request_sha256 | status | razorpay_order_id
```

The nonce is the primary key. An atomic `BEGIN IMMEDIATE` reservation inserts
`RESERVED`; any existing nonce in any state refuses with `NONCE_ALREADY_USED`.
This survives process restart.

Before the single network call, D6 verifies the signature, time, trusted scope,
mandate hash, authorization-result hash, recomputed transaction hash, rebuilt
request hash, and atomic nonce reservation—in that order. There is no retry
loop.

- A confirmed matching provider response transitions `RESERVED -> SUCCEEDED`.
- A definite provider rejection transitions `RESERVED -> REJECTED`.
- A timeout, transport ambiguity, or malformed/mismatched successful response
  transitions `RESERVED -> UNCERTAIN`.

`UNCERTAIN` is terminal for automatic execution and is never retried. The
nonce-derived Razorpay receipt is secondary idempotency defense; it does not
replace the local ledger.

## Response and proof semantics

A successful response must have `entity == "order"`, a non-empty order ID,
the exact request amount, currency, and receipt, `status == "created"`, and a
2xx status. A malformed or mismatched 2xx response is not success because the
external side effect may nevertheless have happened; it becomes `UNCERTAIN`.

An `ExecutionReceipt` proves only that the configured Razorpay Test Mode Orders
API returned a matching created-Order response for the exact request
MandateGuard sent. It does not prove customer payment, payment authorization,
capture, settlement, or real-money movement. A created Order is not a completed
payment.

## Opt-in manual smoke test

The manual script is `scripts/razorpay_test_order_smoke.py`. It is not collected
by pytest. At its composition boundary it reads only:

- `RAZORPAY_KEY_ID` (must begin `rzp_test_`)
- `RAZORPAY_KEY_SECRET`
- `MANDATEGUARD_EXECUTION_HMAC_KEY` (at least 32 bytes)

It builds and retains the exact `ReplayScenario` for a deterministic Tier A/B
`ALLOW` transaction with no semantic constraints, generates a fresh execution
decision nonce, and exercises authorization, context re-derivation, capability
issuance, signature verification, the SQLite gate, and the real Razorpay Test
Mode Orders endpoint. It prints only the final action, transaction hash,
execution request hash, receipt, Razorpay order ID, status, amount, and
currency.

No real Test Mode order has yet been submitted from this repository state. On
the first successful manual run, sanitized evidence may record the time, git
commit, order ID, amount, currency, `created` status, and execution request hash.
Secrets must never be recorded.
