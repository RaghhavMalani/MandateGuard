# Mandate revocation: revalidating consent before execution

Status: **Implemented. Trusted mandate state is revalidated immediately before
provider I/O.**

## Problem

D6 issues a signed execution capability that binds a signature, transaction
hash, merchant, request hash, authorization-result hash, expiry, and a
single-use nonce. Every one of those properties describes *what was authorized
when the capability was issued*.

None of them prove *the user still authorizes this action now*.

A capability that is cryptographically perfect, unexpired, and correctly bound
to its transaction remains executable indefinitely within its lifetime. If the
user withdraws consent after issuance, nothing in the capability itself
records that. The delegated authority outlives the delegation.

This milestone adds one authorization property:

> A previously valid execution capability must no longer be executable if the
> underlying user mandate has been revoked or superseded before provider
> execution.

The resulting invariant:

```
VALID CAPABILITY
+ CURRENT MANDATE STATE != ACTIVE CURRENT VERSION
= REJECT BEFORE NETWORK
```

## Threat model

In scope:

- A capability issued against consent that was withdrawn before execution.
- A capability bound to a mandate version the server has since replaced,
  presented after the policy change (stale delegated authority).
- Replay of a capability that was already refused on mandate-state grounds.
- A caller supplying its own claimed mandate status. The execution request
  cannot carry trusted status; the gate ignores any state value embedded in
  the capability and queries the registry.
- The buyer surface and the semantic model attempting to influence state.
  Neither has a write path to the registry.

Out of scope, and deliberately not claimed:

- Real user identity verification.
- Bank, UPI, or Razorpay mandate revocation. MandateGuard does not talk to any
  such system and does not represent their state.
- Distributed consensus across independent MandateGuard deployments.

The correct framing is narrow and literal: **MandateGuard revalidates its own
trusted declared mandate state immediately before execution.**

## Mandate identity

Consent identity and mandate content are two different things, and the design
keeps both:

- `mandate_id` / `mandate_version` — the **consent-state identity**. This is the
  key the revocation registry is keyed by and the thing a capability is bound
  to for lifecycle purposes.
- `mandate_payload_sha256` — the **exact mandate-content binding**. A capability
  still cannot be moved onto a different mandate body.

`mandate_id` is derived by `build_mandate_from_intent` as a domain-separated
digest over a **server-issued identity seed** and the intent text:

```
mandate_id = UUID(sha256("mandateguard/mandate-identity/v1" || seed || intent))
```

`mandate_identity_seed` is a required argument with no default. The Commerce
Lab passes the run's server-generated `run_id`, so two visitors who type the
same sentence receive two different mandates and one visitor's revocation
cannot reach the other's capability. The seed is never buyer-supplied: the
intent text contributes content, not identity.

An earlier revision derived `mandate_id` from the intent alone. That made the
registry row shared across every principal who typed the same intent, which is
why the seed is now mandatory rather than optional.

### Why both bindings are required

A content hash alone cannot be looked up in a lifecycle registry; an identity
alone cannot detect payload tampering. The capability payload binds
`mandate_id` and `mandate_version` **in addition to** `mandate_payload_sha256`,
and the existing hash binding is not replaced. The gate also checks that the
capability's `mandate_id` equals the presented mandate's own id, refusing
`MANDATE_ID_MISMATCH` if a validly signed capability is paired with a different
mandate.

### Consequence for the semantic cache

`semantic_input_sha256` commits the whole mandate payload, so per-run consent
identity means two runs of the same intent are two different cache keys and
each performs its own semantic evaluation. This is not the cache being
bypassed: they are genuinely different authorization contexts. Live mode
already behaved this way, because its per-run `evaluated_at` also lands in the
mandate payload. The cache still elides the model whenever one authorization
context genuinely repeats, which is what
`test_exact_repeat_hits_semantic_cache_and_skips_model` covers.

### One clock per run

A run has a single trusted clock. The mandate's own validity window, the
deterministic decision, mandate-state transitions, and capability lifetime are
all stamped from the same instant. These are deliberately not separable: a
capability may not outlive the mandate it was issued under, and a registration
may not appear to move backwards against a revocation. An earlier revision froze
the offline demo's clock at service construction while revocation used the wall
clock, which made a revoked mandate reject every later run of the same intent
with `mandate state time moved backwards`.

## State model

`MandateState` (`src/mandateguard/execution/mandate_state.py`) is a frozen
dataclass carrying the smallest justified field set:

| Field | Meaning |
| --- | --- |
| `mandate_id` | Stable UUID identity of the mandate |
| `version` | Positive integer consent version |
| `status` | `ACTIVE`, `REVOKED`, or `SUPERSEDED` |
| `updated_at` | Timezone-aware transition time |
| `revoked_at` | Set only for `REVOKED`, and equal to `updated_at` |
| `superseded_by_version` | Set only for `SUPERSEDED`, strictly newer |

Construction enforces those invariants, so a terminal state cannot carry
another terminal state's metadata.

There are exactly three states. Expiry is **not** one of them: capability
lifetime remains owned by the existing `issued_at` / `expires_at` checks.

### Irreversibility

There is no `unrevoke`. `register_active` refuses any version less than or
equal to the current one, so a revoked or superseded version can never be
reactivated. Renewed consent must arrive as a **new version** (via
`supersede`), which produces a new current version and therefore requires a
freshly issued capability. Lifecycle direction is monotonic.

## Registry

`MandateStateRegistry` is a Protocol with `get_current`, `get_version`,
`register_active`, `revoke`, `supersede`, `record_execution_refusal`,
`audit_events`, and `execution_guard`.

Two implementations satisfy it:

- `SQLiteMandateStateRegistry` — persistent, used by the product service.
- `InMemoryMandateStateRegistry` — deterministic fake for tests, enforcing the
  identical transition rules so tests cannot pass against weaker semantics.

The registry is server-owned. The buyer cannot write it, the semantic model
cannot write it, and the execution request cannot supply trusted status. The
only write paths are the service's own transition methods.

State lives in three tables: `mandate_states` (per-version rows),
`mandate_current` (the single current version per mandate), and
`mandate_state_audit` (an append-only hash-chained transition log).

### Storage-consistency check

Every `get_current` on the SQLite registry first asserts that storage holds a
configuration the API could actually have produced: the pointer resolves to a
real row, at most one version is `ACTIVE`, the pointer identifies that `ACTIVE`
version when one exists, and no version is newer than the pointer. A violation
raises `MandateStateCorruptionError` and the gate refuses with
`MANDATE_STATE_CORRUPT` before any provider call.

The registry never repairs corruption. An impossible configuration means the
state directory was written outside these transitions, and choosing whichever
row still looks usable is precisely the behaviour an attacker would want.

## Execution-time lookup

`validate_and_reserve_execution` performs the mandate-state check **last**,
after every pre-existing binding has already been recomputed, and always
before nonce reservation and provider I/O. The full order:

1. Signature valid, and key known.
2. Action is `ALLOW` in both the capability and the authorization result.
3. Capability not expired, and not implausibly future-issued.
4. Environment, audience, account scope, and merchant match.
5. Mandate payload, authorization result, transaction body, and execution
   request hashes all recomputed and matched.
6. Capability `mandate_id` equals the presented mandate's id.
7. Current trusted state exists → else `MANDATE_STATE_MISSING`.
8. `state.version == capability.mandate_version` → else
   `MANDATE_SUPERSEDED` when the bound version is recorded `SUPERSEDED`,
   otherwise `MANDATE_VERSION_MISMATCH`.
9. `state.status is ACTIVE` → else `MANDATE_REVOKED` / `MANDATE_SUPERSEDED`.

Only then is the nonce reserved and the adapter called.

The gate queries the registry. It never trusts a mandate-state value carried
inside the capability, because that value would describe issuance time, which
is exactly the property under attack.

Issuance performs the same current-state check, so a capability is not minted
against already-invalid consent. That check is a convenience, not the control:
the execution gate re-queries independently.

### Refusal reasons

`MANDATE_REVOKED`, `MANDATE_SUPERSEDED`, `MANDATE_STATE_MISSING`,
`MANDATE_VERSION_MISMATCH`, `MANDATE_ID_MISMATCH`, `MANDATE_STATE_CORRUPT`.
Every one is returned
before any provider or network call. The product layer additionally asserts
that adapter and external-call counters did not move across a mandate-state
refusal, and raises rather than reporting a refusal that came after provider
activity.

## Nonce behavior on revocation

**A mandate-state refusal permanently consumes the capability.** The gate
reserves the nonce and immediately marks it `REJECTED`.

The reasoning: a capability that has been observed against invalid current
consent must not become usable later if state is ever mishandled or
accidentally reactivated. Refusing without consuming would leave a live
capability in an attacker's hands whose validity depends on future state.
Consuming it makes the refusal terminal.

Replay of a mandate-state-refused capability therefore returns
`NONCE_ALREADY_USED`, still with zero provider calls.

### Expiry precedence

Expiry is checked before mandate state, so a capability that is *both* expired
and revoked deterministically returns `CAPABILITY_EXPIRED`.

One consequence is worth stating plainly: because the expiry check returns
before nonce reservation, an expired capability does **not** consume its
nonce. This is safe — the capability is already permanently unusable on time
grounds — but it means nonce consumption is a property of the mandate-state
refusal path specifically, not of every refusal.

Revocation does not replace expiry. Both must hold: `now < expires_at` **and**
current state `ACTIVE` at the bound version.

## Resolve interaction

Resolve cannot bypass revocation.

A recovered `ALLOW` registers and binds to the same current mandate state as
any other `ALLOW`, and its capability carries the same `mandate_id` /
`mandate_version` binding. The path `initial REVIEW → Resolve → final ALLOW →
capability issued → mandate revoked → execution attempt` ends in
`REJECTED BEFORE NETWORK`, exactly as the non-recovered path does.

Recovery is a route to a fresh authorization decision. It is not a route to
authority that outlives consent.

## Cache and semantic boundary

Revocation is trusted control state, not model judgment. Checking it requires
no semantic re-evaluation, touches no cache, and makes **no OpenAI call**. The
kill switch is a local SQLite lookup on the execution path.

## Audit events

The registry keeps an append-only, hash-chained log per mandate:
`MANDATE_REGISTERED_ACTIVE`, `MANDATE_REVOKED`, `MANDATE_SUPERSEDED`, and
`EXECUTION_REFUSED_MANDATE_STATE`.

Each event records `mandate_id`, `mandate_version`, the status transition,
timestamp, reason, sequence number, and the previous event hash. Refusal
events additionally record the decision nonce, `execution_request_sha256`, and
`authorization_result_sha256`.

No secrets and no signatures are logged. `audit_events` revalidates the chain
on read and raises `MandateStateTransitionError` if sequence, linkage, or any
event hash fails to verify.

## Persistence

The product service opens the registry at
`MANDATEGUARD_STATE_DIR/mandate-state.sqlite3`, alongside the existing
execution ledger. When a persistent state directory is configured, mandate
state and its audit chain survive service reopen; a test asserts this and
revalidates the chain after reopening.

**Public Render deployments remain ephemeral.** The container filesystem does
not persist across restarts, so a revocation performed on the public demo is
durable only for the life of that instance. This is a deployment property, not
a design claim.

## Demo authority boundary

The revoke and execute endpoints are labelled `DEMO USER REVOCATION` because
that is exactly what they are. The Commerce Lab implements **no production
authentication or authorization**: there is no account, no session, and no
credential behind these routes.

What the design does provide is ownership *scoping*. Mandate identity is derived
from the server-issued `run_id`, so a caller can only affect the consent state
of a run whose `run_id` they already hold; knowing one run does not let you
reach another run's mandate through an identity collision. `run_id` is a 122-bit
random value and is not enumerable, so it serves as the demo's ownership handle.

That is a scoping property, not authentication. A caller who obtains someone
else's `run_id` can act on that run, and nothing here verifies who they are.
Do not read these endpoints as evidence of verified user identity.

## TOCTOU limitation

The honest statement of the ordering guarantee, and its boundary.

`execute_razorpay_order` wraps validation, nonce reservation, and the provider
call in `mandate_state_registry.execution_guard()`. For the SQLite registry
that guard is a `BEGIN IMMEDIATE` transaction, so the write lock is held from
current-state validation through provider return.

This is a **single SQLite state-file ordering guarantee across cooperating
processes using the same database and locking protocol.** It is not limited to
one process: `BEGIN IMMEDIATE` takes the file's write reservation, so a second
process opening the same database blocks on it exactly as a second thread does.

Within that boundary a concurrent revocation orders strictly *before* the
validation (and refuses the execution) or strictly *after* the provider call
returns. It cannot interleave between the state read and the provider call. A
test proves this empirically: a revocation attempted from a second connection
while a provider call is blocked in flight does not complete until that call
returns.

It is explicitly **not** distributed consensus, not cross-host
linearizability, and not provider-side cancellation.

### Lock-timeout policy

The guard holds the write reservation across provider I/O, so a concurrent
transition has to wait for the guarded section. The wait budget is therefore
set *above* the longest guarded provider interval rather than below it:
`SQLiteMandateStateRegistry.DEFAULT_LOCK_WAIT_SECONDS` is 15s against the
Razorpay adapter's 10s default request timeout. A shorter budget would make an
ordinary in-flight payment fail an unrelated revocation.

When the budget is genuinely exhausted the transition raises
`MandateStateBusyError` and the HTTP layer answers `409 MANDATE_STATE_BUSY`:

> Execution had already entered its guarded provider section. Revocation could
> not be committed before that operation completed.

Nothing is committed, the in-flight provider operation is neither cancelled nor
retried, and no claim is made that the payment was stopped. The same typed
error covers the execution side, where failing to acquire the guard means no
provider call happens at all. **Lock failure never fails open.**

### Rollback resistance: not provided

The local SQLite registry is **not rollback-resistant** against an attacker who
can restore an older filesystem snapshot of the state directory. Restoring a
snapshot taken before a revocation returns the mandate to `ACTIVE`, and the
per-mandate hash chain does not detect it: the chain detects mutation of
*visible* history, but a truncated tail simply looks like a shorter valid
history, because there is no external checkpoint to compare it against.

The current threat model trusts the state directory against rollback-capable
filesystem attackers. This is a stated limitation, not a hidden guarantee, and
no remote consensus, monotonic counter, transparency log, or distributed
database is used or planned here to close it.

One backstop does survive rollback of mandate state alone: a capability that
was already refused has had its nonce permanently consumed in the separate
execution ledger, so resurrecting `ACTIVE` state yields `NONCE_ALREADY_USED`
with zero provider calls. That is defence in depth, not rollback resistance.

What remains true, and is not claimed away:

- The provider call itself is not transactional. Once the request reaches
  Razorpay, a revocation arriving afterwards cannot un-send it. The guarantee
  is about ordering, not about reversal.
- Holding a write lock across network I/O trades concurrency for ordering.
  That is an acceptable trade at demo scale and would need revisiting under
  real load.
- This is a single-process, single-file guarantee. There is **no** distributed
  consensus here, and none is claimed. Multiple independent deployments
  against separate state files would each enforce only their own state.

## Demo journey

Preset `REVOKED AFTER ALLOW` defers execution rather than performing it
automatically:

1. A safe purchase reaches a fresh `ALLOW`.
2. A capability is issued. The panel shows `AUTHORIZED`, `CAPABILITY ISSUED`,
   `RAZORPAY CALLS 0`.
3. The user clicks `REVOKE MANDATE`. The server applies the transition
   `ACTIVE -> REVOKED` under the label `DEMO USER REVOCATION` — precise
   language, because there is no real user-identity infrastructure behind it.
4. The user clicks `ATTEMPT EXECUTION`.
5. The result is `REJECTED BEFORE NETWORK`, reason `MANDATE REVOKED`,
   `RAZORPAY CALLS 0`.

Throughout, the capability remains signed, unexpired, and correctly bound —
and the UI keeps showing that it is. The teaching moment is the gap:

> The capability is still signed and unexpired. Current consent no longer
> permits execution.

Version supersession (v7 capability against a server that has moved to v8) is
covered by tests rather than the UI, to keep the demo surface focused.
