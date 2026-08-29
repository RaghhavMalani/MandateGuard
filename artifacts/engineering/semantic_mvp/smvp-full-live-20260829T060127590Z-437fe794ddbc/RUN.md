# First full semantic MVP engineering diagnostic

- Run ID: `smvp-full-live-20260829T060127590Z-437fe794ddbc`
- Started: `2026-08-29T06:01:27.590Z`
- Ended: `2026-08-29T06:05:17.921995Z`
- Execution Git SHA: `e4b4131d6b0c3f366bc1fdc58679ff245e702f59`
- Provider/model: `openai_responses` / `gpt-5.6-terra`
- Detector/prompt: `1.0` / `1.0`
- Scope: all 72 non-benchmark semantic MVP engineering fixtures

## Overall

- Attempted/completed/errors: 72 / 72 / 0
- Semantic expectation matches: 68 / 72
- Engineering action expectation matches: 68 / 72
- Controller action matches: 72 / 72
- Attempts: exactly one fresh attempt per fixture
- Retries: zero
- Tuning: zero

## Per family

| Family | Semantic matches |
| --- | ---: |
| RECURRENCE | 23 / 24 |
| EXCLUSION | 23 / 24 |
| PURPOSE | 22 / 24 |

## Per expectation

| Expectation | Semantic matches |
| --- | ---: |
| PASS | 23 / 24 |
| VIOLATION | 21 / 24 |
| ABSTAIN | 24 / 24 |

## Family × expectation

| Family | PASS | VIOLATION | ABSTAIN |
| --- | ---: | ---: | ---: |
| RECURRENCE | 8 / 8 | 7 / 8 | 8 / 8 |
| EXCLUSION | 8 / 8 | 7 / 8 | 8 / 8 |
| PURPOSE | 7 / 8 | 7 / 8 | 8 / 8 |

## Per difficulty

| Difficulty | Semantic matches |
| --- | ---: |
| clear | 20 / 21 |
| hard | 24 / 27 |
| ambiguous | 24 / 24 |

## Engineering expectation transition table

| Expected | Observed PASS | Observed VIOLATION | Observed ABSTAIN |
| --- | ---: | ---: | ---: |
| PASS | 23 | 0 | 1 |
| VIOLATION | 0 | 21 | 3 |
| ABSTAIN | 0 | 0 | 24 |

## Live semantic engineering latency

| Scope | Min | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| Overall | 1328 ms | 1685 ms | 2899 ms | 3986 ms |
| RECURRENCE | 1328 ms | 1647 ms | 2729 ms | 3255 ms |
| EXCLUSION | 1388 ms | 1818 ms | 2327 ms | 2548 ms |
| PURPOSE | 1362 ms | 1654 ms | 3096 ms | 3986 ms |

Nearest-rank percentile method. These are live semantic engineering latencies for development fixtures, not production PSP latency.

## Preserved mismatches

| Fixture | Family | Difficulty | Expected | Observed | Final action | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| SMVP-REC-VIOLATION-005 | RECURRENCE | hard | VIOLATION | ABSTAIN | REVIEW | Evidence establishes an annual platform fee for automated controls and scheduling, but does not establish whether the device remains usable in any capacity without the fee. |
| SMVP-EXC-VIOLATION-003 | EXCLUSION | clear | VIOLATION | ABSTAIN | REVIEW | Manufacturing is stated as Portugal, but the applicable domestic market is not identified. |
| SMVP-PUR-PASS-005 | PURPOSE | hard | PASS | ABSTAIN | REVIEW | Evidence verifies coatings and adhesives are free of animal-derived ingredients and identifies serving supplies, but only establishes catered meal service, not vegan catering. |
| SMVP-PUR-VIOLATION-004 | PURPOSE | hard | VIOLATION | ABSTAIN | REVIEW | Beeswax is animal-derived, but the evidence does not establish that these wraps are used for vegan catering. |

## Invariants and errors

- Semantic mismatch fixture IDs: SMVP-REC-VIOLATION-005, SMVP-EXC-VIOLATION-003, SMVP-PUR-PASS-005, SMVP-PUR-VIOLATION-004
- Engineering action expectation mismatch IDs: SMVP-REC-VIOLATION-005, SMVP-EXC-VIOLATION-003, SMVP-PUR-PASS-005, SMVP-PUR-VIOLATION-004
- Controller mapping failure IDs: none
- Typed/provider error IDs: none

This is non-benchmark engineering evidence. The raw runner output and fresh cache are preserved alongside this report.
