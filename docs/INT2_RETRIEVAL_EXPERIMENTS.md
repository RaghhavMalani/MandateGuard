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
network or semantic-model calls even when `OPENAI_API_KEY` is present. Unit
tests use exact fake vectors.

For every query/configuration, the harness records Recall@k, Precision@k,
reciprocal rank when applicable, whether all required evidence was retrieved,
the first required-evidence rank, retrieval latency, and whether the
observation used precomputed vectors.

Duplicate retrieved evidence IDs are scored once at their first rank. If a
query has no relevant evidence, recall and the all-required flag are vacuously
1/true, precision is 0, and reciprocal rank/first-required rank are null.
If fewer unique documents exist than k, precision uses the number actually
returned.

## One-time embedding precomputation

Embeddings are generated once per experiment run, before the configuration
matrix is evaluated. `precompute_embeddings` deduplicates every query and
document text by exact SHA-256 identity, submits the unique texts in a single
batched provider call, and returns an immutable `EmbeddingSnapshot` holding the
text-hash-to-vector mapping, the identifiers each text came from, the model ID,
the vector dimension, the provider call count, input tokens when the provider
reports them, and the precompute latency. `run_stage_a_sweep` performs the
precompute and then evaluates the matrix, so both modes share one algorithm.

`ExperimentRetriever` consumes that snapshot and never calls a provider itself.
Semantic and hybrid cells read vectors by exact text; a text absent from the
snapshot, or a hash whose stored text differs from the requested text, is an
error rather than a silent merge onto the wrong vector. The snapshot refuses a
provider call count above two, so a run whose embedding calls scale with the
matrix cannot be recorded as valid.

For the six frozen queries this is 15 unique texts (6 query texts and 9 document
texts) in 1 provider call, reused across all 192 observations. Embedding inside
each cell would instead issue one call per semantic or hybrid cell.

## Embedding and latency accounting

Embedding accounting is a property of the run, not of an observation, and is
reported exactly once in the `embedding` block of `retrieval_summary.json`:
`embedding_model`, `vector_dimension`, `unique_query_texts`,
`unique_document_texts`, `unique_texts_total`, `embedding_api_calls`,
`embedding_input_tokens`, and `embedding_precompute_latency_ms`. Optional
`CostRates` price that single token count once.

Per-observation `retrieval_latency_ms` measures ranking, scoring, and top-k
selection against already-available vectors. It excludes embedding generation,
so the one-time precompute cost is never attributed to each observation. Each
observation also records `embedding_source`: `precomputed` for semantic and
hybrid, `not_used` for lexical-only and no-retrieval.

## Live embedding opt-in

Live embeddings are opt-in and off by default:

```powershell
$env:PYTHONPATH = "src"
python scripts/run_int2_retrieval_experiments.py --live-embeddings
```

With `--live-embeddings` the runner loads a local `.env` without overriding
existing environment variables, requires `OPENAI_API_KEY`, resolves the model
from `MANDATEGUARD_EMBEDDING_MODEL` (default `text-embedding-3-small`), and
constructs the existing `OpenAIEmbeddingProvider`. There is no second OpenAI
embedding implementation.

The provider is resolved before any fixture is loaded, so a live run without
credentials fails as a configuration error before any observation is produced.
Live mode never falls back to the offline provider. The runner reports the
resolved provider class and model ID, and never prints a key. Only embeddings
are live: the Stage-A sweep makes no buyer, semantic-verifier, or Razorpay call.

No live Stage-A run has been performed or interpreted. The infrastructure
existing is not a result.

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

Stage-B semantic engineering cases were frozen after Stage-A retrieval
configuration selection but before any Stage-B semantic-model execution.
Stage A influenced which retrieval **conditions** are evaluated; it did not
determine the semantic expectations. Those expectations come only from the
pre-existing synthetic commerce catalog, merchant evidence, product semantics,
and MandateGuard policy semantics. Stage B remains non-benchmark engineering
experimentation, and its expectations are not held-out labels or benchmark
ground truth.

Preview the six frozen cases and their deterministic actions without buyer,
semantic-model, or payment calls:

```powershell
$env:PYTHONPATH = "src"
python scripts/preview_int2_stage_b_cases.py
```

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
For `no_retrieval`, the experiment helper records `NOT_EVALUATED`, a null
semantic verdict, `REVIEW`, and `NO_TRUSTED_EVIDENCE_RETRIEVED`; it makes zero
semantic-provider calls and never fabricates trusted evidence.

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
fixtures. Rankings, offline embeddings, matrix membership, metrics, and raw
token counts are reproducible for identical inputs.
