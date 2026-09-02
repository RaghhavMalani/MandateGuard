# MandateGuard Resolve

## Security boundary

Resolve is a bounded evidence-acquisition layer for an existing `REVIEW`. It
does not have an authorization shortcut. A successful acquisition creates a
new canonical evidence set and invokes the complete existing authorization
controller again. Deterministic violation still produces `BLOCK`, missing or
incomplete evidence and semantic abstention still produce `REVIEW`, semantic
violation produces `BLOCK`, and only a clean controller `PASS` produces
`ALLOW`.

Candidate source IDs, identity bindings, evidence kinds, and pinned content
hashes are server configuration. The acquisition API accepts no URL, source
identifier, evidence text, or buyer-selected trust input.

`preset_id` is presentation metadata only. It cannot change retrieval `top_k`,
source candidates, evidence budgets, scope, or authorization policy. Recovery
families come from the canonical mandate's structured `PURPOSE`, `EXCLUSION`,
or `RECURRENCE` metadata; recovery never classifies buyer/model prose.

## Manifest completeness

Every registered source has an immutable manifest declaring its merchant,
explicit `MERCHANT_GLOBAL` or `SKU_SPECIFIC` scope, optional SKU, evidence
kinds, version, effective/expiry interval, and the complete record ID/hash set.
Once a source is selected, its complete provider scope must match that manifest
exactly. Missing or additional IDs produce `SOURCE_INCOMPLETE`; a hash mismatch
produces `MANIFEST_HASH_MISMATCH`. Neither result exposes a partial semantic
bundle to authorization.

The fixed limits are two source-acquisition rounds and four new applicable
evidence items. Selecting a manifest with more new applicable records than the
remaining item budget produces `EVIDENCE_BUDGET_INSUFFICIENT` before the
provider is called. The bundle is never truncated to fit the item budget.

## Scope, freshness, and conflicts

A SKU-specific source must return the exact registered SKU; `sku=None` is
invalid. A merchant-global source must explicitly declare global scope and
returns only records with `sku=None`. Merchant and SKU identities are checked
before acquisition, and every distinct transaction-line SKU must have source
coverage. Merchant-global and SKU-specific evidence are combined; neither
silently overrides the other.

Manifest and record effective/expiry times are evaluated at the recovery time.
Explicit record and manifest supersession removes the superseded version. If
simultaneously authoritative active records give different server-normalized
values for the same claim, or the same evidence ID has different expected
hashes, recovery returns `REVIEW_ON_CONFLICT`. Resolve deliberately does not
invent an authority ranking.

## Time and round accounting

Initial evaluation records `t0`. A user-triggered recovery reads a fresh trusted
server time `t1`, rebuilds the scenario with the current catalog and nonce
state, and reruns Tier A/B before any provider call. Evidence validity and the
full controller are then evaluated at `t1`; an issued capability also starts at
`t1`, and execution performs its own fresh clock read. A mandate that expires
while awaiting review cannot be recovered or executed.

The next round is reserved and appended to the persistent audit chain before
provider acquisition starts. Provider, cache, or authorization failure clears
the in-flight marker but does not refund the round, bounding repeated provider
calls.

## Audit and cache trust boundary

Recovery provenance is appended to a local SQLite hash chain. Events preserve
the initial authorization/evidence commitments and constraint statuses, gap
diagnostic and registry commitments, selected source scopes and manifest
hashes, expected and actual evidence IDs/hashes, completeness, semantic
input/output hashes, final action, and distinct initial/recovery timestamps.
This is local product persistence, not a distributed audit service.

The SQLite semantic cache is part of the trusted computing base. Its unkeyed
SHA-256 commitments detect accidental corruption or inconsistent records; they
do not resist a malicious writer with filesystem access.

## INT-3 boundary decision

The frozen INT-3 model is not integrated. Its target is single-execution action
stability over 62 correlated subsets from six synthetic queries; it is not an
evidence-correctness or safety model. Using it at runtime would support claims
beyond that evaluation. Resolve therefore uses deterministic constraint-family
gap mapping over structured mandate metadata and the server-side trusted-source
registry. The gap planner cannot emit `ALLOW` or `BLOCK`.

## Next engineering evaluation chronology

Do not rerun the three-case evaluation as part of this safety fix. The next run
uses two commits:

1. Commit A contains the manifest, fixtures, expected safety posture, and
   evaluator code. Record Commit A's SHA and the raw/canonical manifest hashes.
2. Only after Commit A exists, execute the offline outcomes. Commit B contains
   results only.

The runner refuses a dirty worktree and records the current HEAD as
`preregistered_commit_sha`, plus separate `plan_canonical_sha256` and
`plan_raw_file_sha256` fields. This gives the later results commit verifiable
chronology instead of relying on a same-commit "frozen" assertion. Its counters
are observed from the service: OpenAI calls, Razorpay calls, offline adapter
calls, planner-direct ALLOW count, provider calls before ALLOW, acquisition
rounds, and new evidence items. The run remains offline and permits zero
OpenAI, Razorpay, or network calls.
