# Semantic MVP engineering fixture schema

This schema describes mutable, non-benchmark engineering data. A record is not
compatible with a registered benchmark case.

## Fixture JSONL record

Each line of `semantic_cases.jsonl` is one strict JSON object with exactly:

| Field | Type | Rule |
| --- | --- | --- |
| `fixture_id` | string | Unique `SMVP-<family>-<expectation>-00N` identifier |
| `fixture_schema_version` | string | Exactly `1.0` |
| `family` | enum | `RECURRENCE`, `EXCLUSION`, or `PURPOSE` |
| `difficulty` | enum | `clear`, `hard`, or `ambiguous` |
| `engineering_expectation` | enum | `PASS`, `VIOLATION`, or `ABSTAIN` |
| `semantic_constraint_text` | string | Non-empty semantic mandate statement |
| `semantic_evidence` | object | Frozen D5 bundle wire shape |
| `developer_rationale` | string | Non-empty engineering explanation; never sent to the verifier |
| `demo_priority` | boolean | Exactly nine records are `true` |

`semantic_evidence` contains exactly `merchant_id` and `entries`. Each of
one to three entries contains exactly `evidence_id`, `merchant_id`, `sku`,
`source_kind`, and `text`, matching the frozen D5
`SemanticEvidenceBundle` and `SemanticEvidenceEntry` shapes.

The corpus validator rejects benchmark-only fields, including `ground_truth`,
`first_run_at`, `case_content_sha256`, `split`, `family_id`,
`provenance`, `label_source`, and `expected_action`.

## Engineering live result

A live diagnostic line contains:

- `fixture_id`
- `engineering_expectation`
- `semantic_status`
- `final_action`
- `reason`
- `semantic_input_sha256`
- integer `latency_ms`
- `provider`
- `model_id`
- `run_at`
- `engineering_expectation_match`

These are engineering diagnostics, not benchmark output. The result has no
benchmark label, score, lifecycle timestamp, or manifest field.
