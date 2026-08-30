# INT-2 retrieval and cache engineering experiments

## Status and scope

INT-2 is reproducible, non-benchmark engineering experimentation. Its query
corpus, scoring annotations, outputs, and lifecycle are separate from
`benchmark/`. Retrieval records contain no MandateGuard verdict, expected
action, benchmark label, or ground-truth field.

The infrastructure does not alter the INT-1 authorization, semantic prompt,
buyer prompt, model defaults, or catalog behavior. Stage B calls the existing
`SemanticVerifier` and `authorize_transaction` controller. The cache harness
uses the same interfaces and has no payment-execution or Razorpay dependency.

## Hypotheses

H1: Hybrid retrieval improves relevant-evidence Recall@k over lexical-only.

H2: Increasing k decreases insufficient-evidence REVIEW decisions but may
increase latency and irrelevant-evidence noise.

H3: Content-addressed semantic caching substantially reduces repeat
authorization latency without reusing results across changed authorization
inputs.

These are hypotheses, not findings. No hypothesis is supported merely because
the harness or an engineering artifact exists. Interpretation requires a
reviewed experiment run and must remain separate from benchmark claims.

## Stage A: retrieval-only sweep

The fixed matrix is:

- strategy: `no_retrieval`, `lexical_only`, `semantic_only`, `hybrid`
- hybrid alpha: `0.0`, `0.25`, `0.5`, `0.75`, `1.0`
- top_k: `1`, `2`, `3`, `5`

Alpha is not trained or tuned. Non-hybrid configurations have no alpha. This
produces 32 configurations per query: 4 each for no-retrieval, lexical-only,
and semantic-only, plus 20 hybrid configurations.

Run the offline sweep with Python 3.12:

```powershell
$env:PYTHONPATH = "src"
python scripts/run_int2_retrieval_experiments.py
```

The default embedding provider is deterministic token hashing. It performs no
network or semantic-model calls. Unit tests use exact fake vectors.

For every query/configuration, the harness records Recall@k, Precision@k,
reciprocal rank when applicable, whether all required evidence was retrieved,
the first required-evidence rank, retrieval latency, embedding latency,
embedding calls, and embedding token usage when available.

Duplicate retrieved evidence IDs are scored once at their first rank. If a
query has no relevant evidence, recall and the all-required flag are vacuously
1/true, precision is 0, and reciprocal rank/first-required rank are null.
If fewer unique documents exist than k, precision uses the number actually
returned.

## Separate relevance annotations

The retriever input corpus is
`fixtures/engineering/int2/retrieval_queries.json`. The scoring-only manifest
is `fixtures/engineering/int2/relevance_manifest.json`. Every manifest record
has:

```json
{
  "query_id": "INT2-Q-STUDYGLOW",
  "relevant_evidence_ids": ["..."],
  "required_evidence_ids": ["..."]
}
```

Required evidence must be a subset of relevant evidence. Query construction
does not accept a relevance-manifest parameter. The sweep joins annotations
only after retrieval returns; annotation data is never embedded or added to a
semantic evidence bundle.

## Stage B: selected downstream authorization

Semantic execution is not part of the Stage-A grid. A caller must first create
a timestamped `DownstreamSelection` containing exact query/configuration pairs
and rationale. `execute_selected_downstream` also requires
`allow_semantic_execution=True`; its default is false.

Only configurations in that recorded selection are executed. Retrieved,
trusted evidence is converted to the existing `SemanticEvidence` type and sent
through the existing MandateGuard semantic verifier and authorization
controller. No experiment classifier is implemented.

The result records engineering expectation, semantic verdict, final action,
retrieved evidence IDs, authorization latency, and one of these engineering
authorization transitions:

- `EXPECTED_VIOLATION_TO_PASS`
- `EXPECTED_PASS_TO_VIOLATION`
- `EXPECTED_TO_REVIEW`
- `NONE`

These are not precision, recall, accuracy, or generalization metrics.
`no_retrieval` remains a Stage-A condition because an empty evidence bundle is
not sent to a semantic provider.

## Exact-input cache experiment

The cache harness compares a cold semantic MISS with an immediate exact-input
HIT using a fresh or caller-supplied cache. It records semantic provider calls,
semantic latency, authorization latency, total latency, raw token usage, cache
status, semantic verdict, and final action. The default runner uses the
existing deterministic offline semantic fake:

```powershell
$env:PYTHONPATH = "src"
python scripts/run_int2_cache_experiment.py
```

This runner is separate from Stage A and makes zero live calls. Mutation probes
perform cache lookups only and require MISS for changes to evidence, mandate,
transaction, model, and prompt. The harness cannot execute Razorpay.

## Cost accounting

`CostRates` accepts optional per-token rates for buyer input/output, semantic
input/output, and embeddings. There are no vendor prices in product
authorization logic. `TokenUsage` remains available independently of rates.
Estimation prices only categories for which both a raw count and experiment
rate are supplied, and separately lists unpriced categories.

## Artifacts and plot data

Generated files live under `artifacts/engineering/int2/`, never under
`benchmark/`:

- `retrieval_sweep.jsonl`
- `retrieval_summary.json`
- `cache_experiment.json`
- `recall_at_k_vs_k.csv`
- `recall_at_k_vs_alpha.csv`
- `mrr_vs_strategy.csv`
- `latency_vs_strategy.csv`
- `review_rate_vs_retrieval_configuration.csv` after Stage B
- `unsafe_direction_transitions_vs_configuration.csv` after Stage B
- `cache_miss_vs_hit_latency_cost.csv`
- `visualization_data.json`

Latency values are measurements of the local run and are not deterministic
fixtures. Rankings, fake embeddings, matrix membership, metrics, and raw token
counts are reproducible for identical inputs.
