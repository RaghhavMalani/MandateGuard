# INT-2 retrieval and cache engineering experiments

## Status and scope

> **NON-BENCHMARK ENGINEERING EXPERIMENTATION.**

INT-2 is reproducible engineering experimentation, not a benchmark. Its query
corpus, scoring annotations, outputs, and lifecycle are separate from
`benchmark/`. Retrieval records contain no MandateGuard verdict, expected
action, benchmark label, or ground-truth field. INT-2 does not demonstrate
generalization.

The infrastructure does not alter the INT-1 authorization, semantic prompt,
buyer prompt, model defaults, or catalog behavior. Stage B calls the existing
`SemanticVerifier` and `authorize_transaction` controller. The cache harness
uses the same interfaces and has no payment-execution or Razorpay dependency.

## Pre-run hypotheses

H1: Hybrid retrieval improves relevant-evidence Recall@k over lexical-only.

H2: Increasing k decreases insufficient-evidence REVIEW decisions but may
increase latency and irrelevant-evidence noise.

H3: Content-addressed semantic caching substantially reduces repeat
authorization latency without reusing results across changed authorization
inputs.

These were hypotheses, not findings or benchmark claims. The completed runs
are interpreted below. In particular, the results do not support a causal
claim that better retrieval caused better authorization.

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

Let `D_k` be the first `k` unique retrieved evidence IDs, with duplicates
scored once at their first rank. The per-query metrics are:

```text
Recall@k                = |D_k ∩ Relevant| / |Relevant|
Precision@k             = |D_k ∩ Relevant| / |D_k|
all_required_retrieved  = Required ⊆ D_k
```

Precision uses documents actually returned, not nominal `k`. If a query has no
relevant evidence, recall and the all-required flag are vacuously 1/true,
precision is 0, and reciprocal rank/first-required rank are null. Reported
aggregate Recall@k and Precision@k values are macro-averages across the six
queries. For this frozen corpus, `Relevant = Required`, and each query has
exactly two annotated items.

## Stage-A result

On six synthetic engineering queries, semantic retrieval recovered both
annotated required evidence items for every query by k=3, while lexical
retrieval required k=5.

The corresponding macro-average Precision@k values were 0.722222 for semantic
at k=3 and 0.638889 for lexical at k=5. They are measured at different `k` and
do not establish that semantic ranking is generally better. Retrieval quality
varied substantially across conditions while downstream engineering outcomes
remained stable on the six evidence-bearing cases.

Stage-B condition C (`semantic_only`, k=3) and condition D (`hybrid`,
alpha=0, k=3) were operationally identical, with identical semantic-input
hashes for 6/6 cases. They are not independent evidence and must not be counted
as separate support for a finding.

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

## Live embedding execution

Live embeddings remain opt-in and off by default:

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

The frozen Stage-A live run used one batched embedding request for all 15
unique texts, made no buyer, semantic-verifier, or Razorpay calls, and stopped
after the retrieval sweep. No experiment was rerun for this documentation
review.

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

### Stage-B result and interpretation

Complete absence of trusted evidence produced NOT_EVALUATED -> REVIEW.

Among the evidence-bearing conditions, retrieval quality varied substantially
while downstream engineering outcomes remained aligned with all six frozen
expectations. Most notably:

> Lexical k=1 retrieved only one of two annotated required evidence items per
> query on average (Recall@k=0.5), yet downstream authorization outcomes
> remained aligned with all six frozen engineering expectations.

This motivates studying decision-sufficient evidence sets, but does not prove
which evidence was actually necessary. It does not show that retrieval failure
always degrades to `REVIEW`, that RAG improved authorization quality, or that
any one retrieval strategy generally outperformed another.

The Stage-B table contains 36 nominal observations: six zero-evidence control
observations and 30 evidence-bearing observations. Those observations contain
only 15 unique semantic inputs, each executed live once; exact-input reuse did
not create independent model observations. Conditions C and D shared identical
semantic-input hashes in all 6/6 cases and are operationally identical rather
than independent evidence.

## Exact-input cache experiment

Across three frozen engineering cases, exact-input cache hits eliminated
repeat semantic API calls and 1,905 semantic tokens.

Observed median total latency was approximately 1.9 s cold -> approximately
3 ms warm. This is `n=3`, with one cold and one warm observation per case. The
cold path includes a live API round-trip. These are engineering latency
measurements, not production throughput.

The cache harness compares a cold semantic MISS with an immediate exact-input
HIT using a fresh or caller-supplied cache. It records semantic provider calls,
semantic latency, authorization latency, total latency, raw token usage, cache
status, semantic verdict, and final action. The repository's default runner
uses the existing deterministic offline semantic fake:

```powershell
$env:PYTHONPATH = "src"
python scripts/run_int2_cache_experiment.py
```

That default runner is separate from Stage A and makes zero live calls. The
preserved Stage-C artifact records the separately authorized live cold calls
and zero warm calls. Mutation probes perform cache lookups only and require
MISS for changes to evidence, mandate, transaction, model, and prompt. The
harness cannot execute Razorpay.

Across 15 mutations spanning evidence, mandate, transaction, model and prompt,
all 15 invalidated the cache. Stage C also tampered one cached `VIOLATION`
toward `PASS`, and the integrity checker rejected it without a provider or
Razorpay call. Prior INT-1 engineering tests exercised additional corruption
variants; those tests are broader integrity evidence and are distinct from the
single Stage-C tamper.

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

## Limitations

INT-2 has six synthetic semantic cases and a nine-document evidence corpus.
The relevance and required-evidence annotations were manually authored. The
Stage-B cases were frozen after Stage-A condition selection but before any
Stage-B semantic execution. Stage B contains 15 unique semantic inputs, not 36
independent model observations, and used one live semantic execution per unique
input. Stage C has three cache cases.

There is no held-out corpus, no repeated stochastic trials, and no
distribution-shift or adversarial evaluation. These constraints preclude
benchmark, generalization, and broad comparative-ranking claims.

## What INT-2 taught us

Annotated retrieval recall was not predictive of downstream decision changes
on this small corpus. This negative result motivates studying evidence
sufficiency and value-of-information rather than optimizing retrieval depth
alone.
