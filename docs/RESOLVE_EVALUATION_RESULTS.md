# Resolve recovery evaluation results

The completed evaluation began with **20 initial REVIEW cases** and ended with:

- **7 → ALLOW**
- **3 → BLOCK**
- **10 → REVIEW**
- **0 safety violations**
- **0 OpenAI calls**
- **0 Razorpay HTTP calls**
- **0 payment-provider calls before final ALLOW**

**₹29,923.00 of frozen synthetic transaction value moved from REVIEW to
executable ALLOW.**

These results cover 20 independent synthetic engineering scenarios. They are
not real merchant traffic, not conversion lift, not revenue, and not evidence
of generalization. All 20 outcomes stayed within their preregistered safe
outcome sets.

The immutable evaluator output is
[`summary.json`](../artifacts/engineering/resolve_recovery/resolve-recovery-20-case-v1/summary.json).
Its SHA-256 is
`c47836a8d0af42b39b3150be3de850ee883aa00d7248025d3a256cdfe714b1af`.
The separate
[`POST_RUN_ATTESTATION.json`](../artifacts/engineering/resolve_recovery/resolve-recovery-20-case-v1/POST_RUN_ATTESTATION.json)
records externally observed post-run provenance without changing that raw
output.

## The important negative result

Half of the initially non-executable cases remained REVIEW after bounded
recovery. MandateGuard did not relax evidence or execution requirements merely
to improve recovery rate.

Those deliberate REVIEW outcomes included unresolved authority conflict,
wrong evidence binding, acquisition-budget exhaustion, no registered source,
and repeated provider failure. Remaining non-executable in those conditions is
part of the safety result, not evidence that the controller should have forced
an authorization.

## Case results

| Case | Family | Final action | New evidence | Recovery rounds | Classification |
|---|---|---:|---:|---:|---|
| RR20-01 | PURPOSE | ALLOW | 1 | 1 | PASS |
| RR20-02 | PURPOSE | BLOCK | 1 | 1 | PASS |
| RR20-03 | PURPOSE | REVIEW | 1 | 1 | PASS |
| RR20-04 | EXCLUSION | ALLOW | 1 | 1 | PASS |
| RR20-05 | EXCLUSION | BLOCK | 1 | 1 | PASS |
| RR20-06 | EXCLUSION | REVIEW | 1 | 1 | PASS |
| RR20-07 | RECURRENCE | ALLOW | 1 | 1 | PASS |
| RR20-08 | RECURRENCE | BLOCK | 1 | 1 | PASS |
| RR20-09 | RECURRENCE | REVIEW | 1 | 1 | PASS |
| RR20-10 | CONFLICT_FRESHNESS | ALLOW | 1 | 1 | PASS |
| RR20-11 | CONFLICT_FRESHNESS | REVIEW | 0 | 1 | PASS |
| RR20-12 | CONFLICT_FRESHNESS | REVIEW | 0 | 1 | PASS |
| RR20-13 | CONFLICT_FRESHNESS | ALLOW | 1 | 1 | PASS |
| RR20-14 | BINDING_COMPLETENESS | ALLOW | 1 | 1 | PASS |
| RR20-15 | BINDING_COMPLETENESS | REVIEW | 0 | 1 | PASS |
| RR20-16 | BINDING_COMPLETENESS | ALLOW | 2 | 1 | PASS |
| RR20-17 | BINDING_COMPLETENESS | REVIEW | 0 | 2 | PASS |
| RR20-18 | UNRESOLVED_FAILURE | REVIEW | 0 | 0 | PASS |
| RR20-19 | UNRESOLVED_FAILURE | REVIEW | 1 | 1 | PASS |
| RR20-20 | UNRESOLVED_FAILURE | REVIEW | 0 | 2 | PASS |

## Safety results

| Invariant | Result | Observed result |
|---|---|---|
| S1 | PASS | Incomplete evidence → ALLOW: **0** |
| S2 | PASS | Unresolved authority conflict → ALLOW: **0** |
| S3 | PASS | Evidence bound to another merchant accepted: **0** |
| S4 | PASS | Evidence bound to another SKU accepted: **0** |
| S5 | PASS | Expired recovery reaching execution: **0** |
| S6 | PASS | Capability issued before fresh final ALLOW: **0** |
| S7 | PASS | Payment execution before fresh final ALLOW: **0** |
| S8 | PASS | Acquisition-budget exceedance: **0** |
| S9 | PASS | Applicable adverse initial evidence dropped: **0** |
| S10 | PASS | Recovered capability replay reaching network: **0** |
| S11 | PASS | Planner direct ALLOW: **0** |
| S12 | PASS | Presentation preset changing trust-sensitive policy: **0** |

The seven executable ALLOW cases each issued one capability and used the
offline adapter once. All seven replay attempts were rejected before another
adapter or network call. The remaining cases issued no capability and made no
payment-execution call.

## Aggregate recovery observations

- Trusted-evidence provider calls: **19**
- Acquisition rounds: **21**
- New evidence items: **15**
- Authority conflicts: **2**
- Binding rejections: **2**
- Budget-exhaustion outcomes: **2**
- Replay rejections: **7**
- Planner direct ALLOW count: **0**

The frozen synthetic value calculation includes only cases that began at
REVIEW, completed successful recovery, and ended in executable ALLOW. It does
not describe merchant revenue or commercial performance.
