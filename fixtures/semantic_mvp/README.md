# IMPORTANT — NON-BENCHMARK ENGINEERING DATA

These cases are AI-assisted engineering fixtures used for development,
debugging, integration testing, and demonstration.

They are not part of MandateGuard's registered benchmark.

They must not be included in benchmark precision, recall, false-positive-rate,
held-out generalization, or registered Tier C counts.

They may be inspected, modified, regenerated, or used during detector
development.

## Purpose and boundaries

The 72 records in `semantic_cases.jsonl` are an engineering corpus only. They
exercise recurrence, exclusion, and purpose semantics while the deterministic
payment envelope remains clean. Their `engineering_expectation` values are
diagnostic expectations, not human-adjudicated labels or evidence of
generalization.

The corpus contains 24 fixtures per family and, within every family, eight each
of `PASS`, `VIOLATION`, and `ABSTAIN`. Exactly nine fixtures have
`demo_priority=true`: one of each expectation per family.

No fixture contains prompt injection, a jailbreak, credentials, personal data,
transaction secrets, exploit instructions, or offensive fraud material.
Difficulty comes from ordinary commercial language: paraphrase, implication,
qualification, composition, exceptions, scope, suitability, and ambiguity.

## Demo story

- **ALLOW:** semantic evidence satisfies the mandate, so semantic `PASS`
  reduces locally to final action `ALLOW`.
- **BLOCK:** semantic evidence violates the mandate, so semantic `VIOLATION`
  reduces locally to final action `BLOCK`.
- **REVIEW:** evidence is genuinely insufficient or ambiguous, so semantic
  `ABSTAIN` reduces locally to final action `REVIEW`.

The model returns only semantic status and reason. MandateGuard's frozen
controller, not the model, determines the final action.

The nine prioritized fixtures are:

- `SMVP-REC-PASS-001`
- `SMVP-REC-VIOLATION-001`
- `SMVP-REC-ABSTAIN-001`
- `SMVP-EXC-PASS-001`
- `SMVP-EXC-VIOLATION-001`
- `SMVP-EXC-ABSTAIN-001`
- `SMVP-PUR-PASS-001`
- `SMVP-PUR-VIOLATION-001`
- `SMVP-PUR-ABSTAIN-001`

## Validation and selection

Default operation validates only. It imports no provider client and makes no
model call:

```bash
python scripts/run_semantic_mvp_fixtures.py
python scripts/run_semantic_mvp_fixtures.py --validate-only
```

Selection flags compose after the full corpus is validated:

```bash
python scripts/run_semantic_mvp_fixtures.py --case-id SMVP-REC-PASS-001
python scripts/run_semantic_mvp_fixtures.py --demo-only
python scripts/run_semantic_mvp_fixtures.py --demo-only --limit 3
```

## Explicit live diagnostics

Live mode is opt-in and reuses the frozen D5 `SemanticVerifier`, OpenAI
adapter, and full `authorize_transaction` path. It does not implement another
detector.

```bash
python scripts/run_semantic_mvp_fixtures.py \
  --live \
  --demo-only \
  --model-id <configured-model-id>
```

Live JSONL is written under `artifacts/engineering/semantic_mvp/`, never under
`benchmark/results/`. These files are titled engineering diagnostics. They do
not update `first_run_at`, the benchmark manifest, or any benchmark lifecycle
metadata, and they must never be reported as accuracy, precision, recall, FPR,
or held-out performance.

See `SCHEMA.md` for the strict fixture and result fields.
