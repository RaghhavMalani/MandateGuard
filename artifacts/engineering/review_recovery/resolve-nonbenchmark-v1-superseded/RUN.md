# MandateGuard Resolve evaluation

**Classification:** non-benchmark synthetic engineering evaluation

**Frozen plan SHA-256:** `e80f3999e69c7ca375093fa64a22be9b721a698ffe8c8f84957f7163c5db2659`

## Results

| Measure | Result |
| --- | ---: |
| Initial REVIEW | 3 |
| Resolved after bounded acquisition | 2 |
| REVIEW to ALLOW | 1 |
| REVIEW to BLOCK | 1 |
| Still REVIEW | 1 |
| Mean additional trusted evidence items | 1.000 |
| Max acquisition rounds | 1 |
| Payment-provider calls before final ALLOW | 0 |
| Planner-direct unsafe ALLOW | 0 |
| Synthetic transaction value released from REVIEW (minor units) | 129900 |

The three product cases cover purpose, recurrence, and exclusion behavior. The
separately tested failure injections are correlated robustness checks, not
independent commerce cases.

The planner emitted only evidence-gap diagnostics. Every outcome came from a
fresh invocation of the existing controller over the exact canonical evidence
set recorded in `summary.json`.

## External calls

OpenAI calls: 0. Razorpay calls: 0. Network calls: 0. One local offline
execution-double call occurred only after the recovered final controller result
was `ALLOW`.

These synthetic outcomes do not establish generalization. The reported
synthetic transaction value released from REVIEW is not revenue recovered.
