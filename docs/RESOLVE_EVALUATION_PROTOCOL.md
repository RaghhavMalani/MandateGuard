# Resolve recovery evaluation protocol

Preregistration for the 20-case MandateGuard Resolve evaluation. This document
describes the plan only. It contains no outcomes, and no outcomes existed when
it was written.

## Research question

When an autonomous purchase is initially non-executable because trusted
evidence is insufficient, can bounded acquisition of complete authoritative
evidence make the transaction evaluable without relaxing MandateGuard's
authorization invariants?

This is a non-benchmark engineering evaluation of recovery behaviour. It is not
a machine-learning benchmark, a conversion study, a merchant revenue study, a
generalization claim, or a precision/recall benchmark.

## Composition

Twenty independently authored synthetic worlds, three to four per family.

| Family | Cases |
| --- | --- |
| `PURPOSE` | `RR20-01-PURPOSE-SUPPORTED`, `RR20-02-PURPOSE-PROHIBITED`, `RR20-03-PURPOSE-UNRESOLVED` |
| `EXCLUSION` | `RR20-04-EXCLUSION-ABSENT`, `RR20-05-EXCLUSION-PRESENT`, `RR20-06-EXCLUSION-AMBIGUOUS` |
| `RECURRENCE` | `RR20-07-RECURRENCE-ONE-TIME`, `RR20-08-RECURRENCE-RECURRING`, `RR20-09-RECURRENCE-UNDOCUMENTED` |
| `CONFLICT_FRESHNESS` | `RR20-10-SUPERSEDED-RECORD`, `RR20-11-AUTHORITY-CONFLICT`, `RR20-12-SCOPE-PRECEDENCE-CONFLICT`, `RR20-13-EXPIRED-REPLACED` |
| `BINDING_COMPLETENESS` | `RR20-14-CROSS-MERCHANT-SKU`, `RR20-15-WRONG-SKU-BINDING`, `RR20-16-GLOBAL-PLUS-SKU`, `RR20-17-BUDGET-INSUFFICIENT` |
| `UNRESOLVED_FAILURE` | `RR20-18-NO-REGISTERED-SOURCE`, `RR20-19-COMPLETE-BUT-INSUFFICIENT`, `RR20-20-PROVIDER-FAILURE` |

Each case is its own merchant, SKU, catalogue entry, mandate, transaction
amount, initial evidence set, and source manifest set. No case is a mutation of
another, and no two cases share a merchant, a SKU, an evidence record, or a
trusted source. The only shared object is the single trusted source registry
every case is evaluated against, which is what makes `RR20-14` and `RR20-15`
meaningful: the foreign-merchant and wrong-SKU evidence is really registered.

The outcome distribution is not balanced by design. Each expected outcome
follows from what that world's frozen authoritative evidence states.

## Frozen policy

Every case runs `MANDATEGUARD_PRODUCT_EVIDENCE_POLICY_V1` from
`src/mandateguard/product/evidence_policy.py`: `top_k=5`, `alpha=0.4`, hybrid
retrieval, at most two acquisition rounds, at most four new evidence items.
Scope partitioning, supersession, expiry, claim-metadata completeness, and
conflict detection come from `mandateguard.recovery.registry` unchanged;
controller precedence comes from the frozen Tier A/B/C pipeline unchanged.

Case fixtures differ. Trust policy does not. A case declaring any
trust-sensitive key is refused before the freeze and again before execution.

## Global safety invariants

These are architecture invariants, not metrics to optimize.

| ID | Invariant |
| --- | --- |
| S1 | An incomplete authoritative evidence set must never produce `ALLOW`. |
| S2 | An unresolved authoritative conflict must never produce `ALLOW`. |
| S3 | Evidence bound to another merchant must never be accepted. |
| S4 | Evidence bound to another SKU must never be accepted. |
| S5 | An expired recovery must never reach execution. |
| S6 | No capability may be issued before a fresh final `ALLOW`. |
| S7 | No provider or payment execution may occur before a final `ALLOW`. |
| S8 | The acquisition budget must never be exceeded. |
| S9 | Recovery must never drop still-applicable adverse initial evidence. |
| S10 | A recovered capability replay must never reach a network. |
| S11 | The evidence-gap planner must never emit an authorization action. |
| S12 | A presentation preset must never change a trust-sensitive policy field. |

## Expected outcomes

Each case preregisters an expected initial action (`REVIEW` for all twenty), an
expected final action, an allowed final-action set, and a forbidden set. Where
semantic interpretation could legitimately produce more than one safe non-ALLOW
outcome, the allowed set holds more than one action rather than forcing a
single answer. Outcomes are classified three ways:

- **pass** — the final action is in the allowed set.
- **safety violation** — the final action is in the forbidden set, or a global
  invariant was violated.
- **unresolved miss** — neither: safe, but the recovery expectation was missed.

Success is never defined as whatever the implementation returns. Every allowed
set was derived from what the frozen authoritative evidence states.

## Observed metrics

`RESOLVE_METRIC_SCHEMA_V2`. The plan preregisters nineteen observed metric
names with frozen definitions: `initial_review_count`, `resolved_count`,
`review_to_allow_count`, `review_to_block_count`, `review_to_review_count`,
`trusted_evidence_provider_calls`, `provider_calls_before_final_allow`,
`offline_adapter_calls`, `razorpay_http_calls`, `openai_calls`,
`acquisition_rounds`, `new_evidence_items`, `planner_direct_allow_count`,
`budget_exhaustion_count`, `authority_conflict_count`,
`source_incomplete_count`, `binding_rejection_count`, `expired_recovery_count`,
`replay_rejection_count`.

All seven counters in the shared schema's `OBSERVED_COUNTER_NAMES` are inside
that set. Every counter is incremented by an instrumented call site; none is
inferred from a run-mode string. `openai_calls` and `razorpay_http_calls` must
be zero, and a single unexpected call fails the run.

After the run it will be permissible to report the **synthetic transaction
value moved from REVIEW to executable ALLOW**: the sum of `amount_minor` over
cases whose initial action was `REVIEW` and whose final action became `ALLOW`
through successful complete trusted-evidence recovery. Amounts were frozen
before any outcome existed. That figure is not revenue, not GMV, not conversion
lift, and not merchant revenue.

## Chronological preregistration

The plan is frozen before outcomes exist, and the chronology is provable from
git rather than asserted.

1. Author the twenty worlds and their evidence fixtures.
2. Author the plan; validate deterministic structure only.
3. Set the plan and freeze record to `FROZEN`.
4. Record `plan_canonical_sha256`, `plan_raw_file_sha256`, every fixture
   SHA-256, the trusted registry SHA-256, and the metric schema version in
   `preregistration_freeze.json`.
5. Commit A: `test: preregister resolve recovery evaluation`, then push it.
6. Commit B: write `preregistration_commit.json` naming Commit A. A plan cannot
   contain the SHA of the commit that introduces it, so the freeze is two-step.
   Commit B creates no outcome data.

Execution then requires Commit A to be `HEAD` or an ancestor of `HEAD` with
every bound path unchanged since it.

Artifacts:

- `fixtures/engineering/resolve_recovery/worlds/` — the twenty worlds.
- `fixtures/engineering/resolve_recovery/evidence/` — the trusted provider
  bundles, including the foreign merchant used by `RR20-14`.
- `fixtures/engineering/resolve_recovery/preregistration_plan.json`
- `fixtures/engineering/resolve_recovery/preregistration_freeze.json`
- `fixtures/engineering/resolve_recovery/preregistration_commit.json`
- `scripts/validate_resolve_preregistration.py` — structural validator; safe to
  run at any time and produces no outcomes.
- `scripts/run_resolve_recovery_evaluation.py` — the locked runner.

## Runner lock

The runner refuses unless every preregistered condition holds: exactly twenty
cases, plan and freeze status `FROZEN`, `RESOLVE_METRIC_SCHEMA_V2`, unique case
IDs matching the frozen worlds, every fixture present with a matching SHA-256,
every manifest record hash re-derived from its evidence fixture, the registry
hash matching the freeze, frozen transaction amounts, a stated safety posture
and non-empty allowed set per case, no trust-sensitive override, trust policy
equal to the product policy, plan hashes matching the freeze, a run inside the
frozen validity window with no expired mandate, a clean working tree, a valid
commit binding, and no pre-existing outcome artifact. Any failure stops the run
before a single case is evaluated.

## What this can and cannot claim

It can show whether bounded acquisition of complete authoritative evidence
makes a previously non-executable transaction evaluable, and whether the twelve
invariants hold across twenty independently authored adversarial worlds,
including a completeness attack, two authority conflicts, two binding attacks,
an uncovered gap, and a provider outage.

It cannot show generalization, real-merchant behaviour, or a rate of any kind.
Every world is synthetic. Twenty independent cases are twenty data points. The
initial evidence set of each case is a frozen fixture standing for a prior
retrieval result rather than a live retrieval output, so `top_k` and `alpha` are
asserted equal to the product policy rather than exercised. One offline
deterministic semantic model is used, so semantic abstention is a property of
that model rather than of the design. Payment execution runs against a
network-free double, so it evidences gate order and replay refusal rather than
provider behaviour.

## After the freeze

Fixtures, expected outcomes, case manifests, transaction amounts, the metric
schema, and the trust-sensitive policy are frozen. A defect discovered after the
freeze is documented, and the evaluation is explicitly invalidated or
re-preregistered. The plan is never silently edited.
