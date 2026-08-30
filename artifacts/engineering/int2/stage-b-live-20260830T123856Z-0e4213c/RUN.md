# LIVE STAGE-B SEMANTIC ENGINEERING EXPERIMENT

NON-BENCHMARK ENGINEERING DATA. These are engineering associations and
authorization transitions, not accuracy, classification precision/recall,
held-out performance, generalization, or a benchmark result.

## Evidence

- Run ID: `stage-b-live-20260830T123856Z-0e4213c`
- Execution SHA: `0e4213c9dedf1d883585bd76980c24bf1aa0608b`
- Started: `2026-08-30T12:38:56.653596Z`
- Live execution started: `2026-08-30T13:00:28.350430Z`
- Ended: `2026-08-30T13:01:01.750320Z`
- Status: `COMPLETED`
- Frozen case manifest SHA-256: `b2d4857750b98a1f3629f63c9d294f353fb734b67ab2bcbec8e8f3a057fc6454`
- Frozen selection SHA-256: `516db7c82e1c8a2eccb0061363d391249864df0245a1b4b9e647604fa511e4a3`

## Plan and execution

- Nominal observations: 36
- Zero-evidence observations: 6
- Evidence-bearing observations: 30
- Unique semantic hashes: 15
- Reused exact-input observations: 15
- Predicted semantic calls: 15
- Actual semantic calls: 15

## Condition table

| Cond | All req | Recall | Precision | MRR | Matches | V→P | P→V | V→R | P→R | Review | Not eval R | Semantic abstain R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | 0/6 | 0.000000 | 0.000000 | 0.000000 | 0/6 | 0 | 0 | 2 | 3 | 6 | 6 | 0 |
| B | 6/6 | 1.000000 | 0.638889 | 1.000000 | 6/6 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| C | 6/6 | 1.000000 | 0.722222 | 1.000000 | 6/6 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| D | 6/6 | 1.000000 | 0.722222 | 1.000000 | 6/6 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| E | 6/6 | 1.000000 | 0.638889 | 1.000000 | 6/6 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| F | 0/6 | 0.500000 | 1.000000 | 1.000000 | 6/6 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |

## Semantic/hybrid equivalence

Conditions C and D had identical semantic-input hashes for
`6/6` cases. Operational equivalence on this experiment:
`true`.

## Engineering authorization transition tables

### Condition A
```json
{
  "EXPECTED_ABSTAIN": {
    "ABSTAIN_TO_ABSTAIN": 0,
    "ABSTAIN_TO_NOT_EVALUATED": 1,
    "ABSTAIN_TO_PASS": 0,
    "ABSTAIN_TO_VIOLATION": 0
  },
  "EXPECTED_PASS": {
    "PASS_TO_ABSTAIN": 0,
    "PASS_TO_NOT_EVALUATED": 3,
    "PASS_TO_PASS": 0,
    "PASS_TO_VIOLATION": 0
  },
  "EXPECTED_VIOLATION": {
    "VIOLATION_TO_ABSTAIN": 0,
    "VIOLATION_TO_NOT_EVALUATED": 2,
    "VIOLATION_TO_PASS": 0,
    "VIOLATION_TO_VIOLATION": 0
  }
}
```
### Condition B
```json
{
  "EXPECTED_ABSTAIN": {
    "ABSTAIN_TO_ABSTAIN": 1,
    "ABSTAIN_TO_NOT_EVALUATED": 0,
    "ABSTAIN_TO_PASS": 0,
    "ABSTAIN_TO_VIOLATION": 0
  },
  "EXPECTED_PASS": {
    "PASS_TO_ABSTAIN": 0,
    "PASS_TO_NOT_EVALUATED": 0,
    "PASS_TO_PASS": 3,
    "PASS_TO_VIOLATION": 0
  },
  "EXPECTED_VIOLATION": {
    "VIOLATION_TO_ABSTAIN": 0,
    "VIOLATION_TO_NOT_EVALUATED": 0,
    "VIOLATION_TO_PASS": 0,
    "VIOLATION_TO_VIOLATION": 2
  }
}
```
### Condition C
```json
{
  "EXPECTED_ABSTAIN": {
    "ABSTAIN_TO_ABSTAIN": 1,
    "ABSTAIN_TO_NOT_EVALUATED": 0,
    "ABSTAIN_TO_PASS": 0,
    "ABSTAIN_TO_VIOLATION": 0
  },
  "EXPECTED_PASS": {
    "PASS_TO_ABSTAIN": 0,
    "PASS_TO_NOT_EVALUATED": 0,
    "PASS_TO_PASS": 3,
    "PASS_TO_VIOLATION": 0
  },
  "EXPECTED_VIOLATION": {
    "VIOLATION_TO_ABSTAIN": 0,
    "VIOLATION_TO_NOT_EVALUATED": 0,
    "VIOLATION_TO_PASS": 0,
    "VIOLATION_TO_VIOLATION": 2
  }
}
```
### Condition D
```json
{
  "EXPECTED_ABSTAIN": {
    "ABSTAIN_TO_ABSTAIN": 1,
    "ABSTAIN_TO_NOT_EVALUATED": 0,
    "ABSTAIN_TO_PASS": 0,
    "ABSTAIN_TO_VIOLATION": 0
  },
  "EXPECTED_PASS": {
    "PASS_TO_ABSTAIN": 0,
    "PASS_TO_NOT_EVALUATED": 0,
    "PASS_TO_PASS": 3,
    "PASS_TO_VIOLATION": 0
  },
  "EXPECTED_VIOLATION": {
    "VIOLATION_TO_ABSTAIN": 0,
    "VIOLATION_TO_NOT_EVALUATED": 0,
    "VIOLATION_TO_PASS": 0,
    "VIOLATION_TO_VIOLATION": 2
  }
}
```
### Condition E
```json
{
  "EXPECTED_ABSTAIN": {
    "ABSTAIN_TO_ABSTAIN": 1,
    "ABSTAIN_TO_NOT_EVALUATED": 0,
    "ABSTAIN_TO_PASS": 0,
    "ABSTAIN_TO_VIOLATION": 0
  },
  "EXPECTED_PASS": {
    "PASS_TO_ABSTAIN": 0,
    "PASS_TO_NOT_EVALUATED": 0,
    "PASS_TO_PASS": 3,
    "PASS_TO_VIOLATION": 0
  },
  "EXPECTED_VIOLATION": {
    "VIOLATION_TO_ABSTAIN": 0,
    "VIOLATION_TO_NOT_EVALUATED": 0,
    "VIOLATION_TO_PASS": 0,
    "VIOLATION_TO_VIOLATION": 2
  }
}
```
### Condition F
```json
{
  "EXPECTED_ABSTAIN": {
    "ABSTAIN_TO_ABSTAIN": 1,
    "ABSTAIN_TO_NOT_EVALUATED": 0,
    "ABSTAIN_TO_PASS": 0,
    "ABSTAIN_TO_VIOLATION": 0
  },
  "EXPECTED_PASS": {
    "PASS_TO_ABSTAIN": 0,
    "PASS_TO_NOT_EVALUATED": 0,
    "PASS_TO_PASS": 3,
    "PASS_TO_VIOLATION": 0
  },
  "EXPECTED_VIOLATION": {
    "VIOLATION_TO_ABSTAIN": 0,
    "VIOLATION_TO_NOT_EVALUATED": 0,
    "VIOLATION_TO_PASS": 0,
    "VIOLATION_TO_VIOLATION": 2
  }
}
```

## Stage-A to Stage-B associations

See `stage_a_stage_b_join.csv`. These are descriptive engineering associations
only; no causal claim is made.

## Production default comparison

```json
[
  {
    "against_condition": "A",
    "expectation_match_count_delta": 6,
    "mean_recall_at_k_delta": 1.0,
    "pass_to_violation_count_delta": 0,
    "review_count_delta": -5,
    "violation_to_pass_count_delta": 0
  },
  {
    "against_condition": "B",
    "expectation_match_count_delta": 0,
    "mean_recall_at_k_delta": 0.0,
    "pass_to_violation_count_delta": 0,
    "review_count_delta": 0,
    "violation_to_pass_count_delta": 0
  },
  {
    "against_condition": "C",
    "expectation_match_count_delta": 0,
    "mean_recall_at_k_delta": 0.0,
    "pass_to_violation_count_delta": 0,
    "review_count_delta": 0,
    "violation_to_pass_count_delta": 0
  },
  {
    "against_condition": "D",
    "expectation_match_count_delta": 0,
    "mean_recall_at_k_delta": 0.0,
    "pass_to_violation_count_delta": 0,
    "review_count_delta": 0,
    "violation_to_pass_count_delta": 0
  },
  {
    "against_condition": "F",
    "expectation_match_count_delta": 0,
    "mean_recall_at_k_delta": 0.5,
    "pass_to_violation_count_delta": 0,
    "review_count_delta": 0,
    "violation_to_pass_count_delta": 0
  }
]
```

## API, tokens, and latency

- Provider: `openai_responses`
- Model: `gpt-5.6-terra`
- Unique live executions: 15
- Semantic API calls: 15
- Input tokens: 7929
- Output tokens: 1046
- p50 semantic latency (nearest-rank): 1814.4113 ms
- p95 semantic latency (nearest-rank): 5327.5085 ms
- Total semantic provider latency: 33280.3644 ms
- Retries: 0

## Integrity

- Buyer calls: 0
- Razorpay calls: 0
- Retries: 0
- Tuning changes: 0
- The execution plan remained byte-identical during execution.
- Systemic error: `null`
