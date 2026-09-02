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

The product and future evaluator import the same immutable server-owned policy,
`MANDATEGUARD_PRODUCT_EVIDENCE_POLICY_V1`: hybrid retrieval with the product
defaults (`top_k=5`, `alpha=0.4`), at most two acquisition rounds, and at most
four new evidence items. The evaluator passes no trust-sensitive override and
compares policy ID, retrieval fields, budgets, registry hash, override marker,
and semantic mode with the product before it can score a case.

The judge-facing recoverable scenario is not created by suppressing retrieval.
It is the dedicated `merchant-lumen` / `aurora-focus-lamp` fixture. At the
product default policy, `lumen-terms-v1` and `aurora-listing-v1` do not establish
individual-study suitability or billing, so the controller returns `REVIEW`.
The explicit recovery action acquires the complete registered scope, including
`aurora-sku-terms-v2`, which establishes individual-study use and a one-time,
non-renewing purchase. A fresh controller invocation can then return `ALLOW`.

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

Registry construction rejects two different sources with overlapping active
windows for the same merchant and authoritative scope (`MERCHANT_GLOBAL`, or
the same `SKU_SPECIFIC` SKU), unless their evidence kinds and record metadata
are identical aliases or one manifest explicitly supersedes the other. Merely
using disjoint evidence-kind labels does not make such sources a safe partition:
the provider API partitions returned records by merchant and scope/SKU, not by
evidence kind, so both sources would otherwise see each other's records and
remain permanently `SOURCE_INCOMPLETE`.

## Scope, freshness, and conflicts

A SKU-specific source must return the exact registered SKU; `sku=None` is
invalid. A merchant-global source must explicitly declare global scope and
returns only records with `sku=None`. Merchant and SKU identities are checked
before acquisition, and every distinct transaction-line SKU must have source
coverage. Merchant-global and SKU-specific evidence are combined; neither
silently overrides the other.

Manifest and record effective/expiry times are evaluated at the recovery time.
Supersession is permanent from the superseding version's effective time: expiry
of v2 never resurrects v1. Manifest replacement must name an existing older
manifest for the identical merchant/scope/SKU and the supersession graph must
be acyclic.

`RECURRENCE` records require normalized `billing.*` claim metadata and
`EXCLUSION` records require normalized `content.*` claim metadata whenever two
authorities overlap. `UNESTABLISHED` is an explicit non-assertion; an absent
namespace is not. If overlapping active records disagree, if one or both omit
required metadata so non-conflict cannot be proven, or if the same evidence ID
has different expected hashes, acquisition returns `REVIEW_ON_CONFLICT` with a
deterministic conflict code. The semantic verifier never receives that
conflicted bundle and cannot choose which authority is true. `PURPOSE` does not
require a normalized claim because its records add support for a declared use
rather than assert a conflicting billing/content classification; explicit
purpose claims are still compared when present.

## Constraint-family provenance

`constraint_family` originates at mandate construction, before recovery. The
validated `InterpretedPurchaseIntent.purpose` field deterministically creates a
`PURPOSE` constraint. Each structured exclusion creates `EXCLUSION`, except the
closed recurrence terms `subscription`, `subscriptions`, `recurrence`, and
`renewal`, which deterministically create `RECURRENCE`. Recovery maps only that
enum to evidence kinds; changing the human-readable constraint text cannot
change source selection. Legacy mandates may retain `constraint_family=None`
in their canonical payload, but such an unclassified constraint does not gain a
recovery source through prose inference.

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
Every event also carries `mandate_payload_sha256` and
`transaction_body_sha256`. After a recovered `ALLOW`, `EXECUTION_LINKED` adds
the decision nonce, execution-request hash, and offline/Razorpay order receipt
identifier. Those are join keys into the execution ledger; the recovery audit
does not duplicate that ledger. A reviewer can therefore follow `REVIEW` to
fresh authorization, capability, and execution from persistent stores without
the in-memory CommerceRun.

Set `MANDATEGUARD_STATE_DIR` (or pass `state_dir` when embedding the service) to
place the semantic cache, execution ledger, and recovery audit in one configured
directory. Reopening the service with the same directory and filesystem
preserves those stores. With no configuration, the service deliberately uses a
temporary directory suitable for local development and the offline demo. A
public Render instance is restart-durable only if its service is configured
with persistent filesystem/storage and `MANDATEGUARD_STATE_DIR` points there;
the current free blueprint does neither and must be treated as ephemeral.

Audit persistence is an execution prerequisite. If the round-reservation append
fails, the in-memory round remains consumed and in flight, the review is marked
`AUDIT_PERSISTENCE_FAILED`, and later recovery requests return
`RECOVERY_AUDIT_UNAVAILABLE`. No trusted-evidence provider or payment adapter is
called and no capability is issued. Reconciliation is an explicit operator
action; the service does not silently retry or refund the round.

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

The checked-in three-case file is a smoke scaffold with status
`DRAFT_PRE_EVALUATION`; it is not a frozen evaluation and execution is disabled.
It must be expanded to the planned 20 independent cases and reviewed in a later
task. The executable runner refuses any status other than
`FROZEN_BEFORE_OUTCOMES`, so this hardening pass cannot accidentally produce
outcomes. A later evaluation uses two commits:

1. Commit A contains the expanded manifest, fixtures, expected safety posture,
   and evaluator code. Record Commit A's SHA and the raw/canonical manifest
   hashes.
2. Only after Commit A exists, execute the offline outcomes. Commit B contains
   results only.

After the expanded manifest is deliberately frozen, the runner refuses a dirty
worktree and records the current HEAD as `preregistered_commit_sha`, plus
separate `plan_canonical_sha256` and `plan_raw_file_sha256` fields. This gives
the later results commit verifiable chronology instead of relying on a
same-commit "frozen" assertion.

Counters use `RESOLVE_METRIC_SCHEMA_V2` and are observed at real
resources/adapters: `openai_calls`, `razorpay_http_calls`,
`offline_adapter_calls`, `trusted_evidence_provider_calls`,
`acquisition_rounds`, `new_evidence_items`, and
`planner_direct_allow_count`. Missing or unknown planned/emitted names refuse
execution. The run remains offline; any observed OpenAI, Razorpay HTTP, or
aggregate network call fails it.
