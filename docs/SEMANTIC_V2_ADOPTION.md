# Semantic v2 — adoption decision

The frozen rule in [`data/eval/semantic-v2/FREEZE.json`](../data/eval/semantic-v2/FREEZE.json)
was committed in `36b49c2` and completed in `9c1fdc7`, both before any encoder was
run. This document applies that rule to the measurement in
[`artifacts/engineering/semantic-v2/evaluation.json`](../artifacts/engineering/semantic-v2/evaluation.json)
and records what it does and does not decide.

## What the rule says

| Field | Value |
| --- | --- |
| Primary | highest paraphrase `recall_at_10` on the frozen query set |
| Tie-breakers | `all` nDCG@10, full-discovery P95, resident memory, artifact bytes |
| Materiality | paraphrase `recall_at_10` must improve by **≥ 0.10 absolute** over BM25 |
| Runtime gate | warm full-discovery P95 ≤ 250 ms · incremental RSS ≤ 500 MB · external model calls = 0 |
| Discipline | `no_repeated_test_tuning: true` |

## The measurement

BM25 baseline, paraphrase Recall@10: **0.3705** (62 paraphrase queries of 124).

| Model | Config | paraphrase R@10 | Δ vs BM25 | literal R@10 | all R@10 | all nDCG@10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| all-MiniLM-L6-v2 | dense | **0.5642** | **+0.1937** | 0.8544 | 0.7093 | 0.6903 |
| bge-small-en-v1.5 | dense | 0.5192 | +0.1487 | 0.8102 | 0.6647 | 0.6616 |
| bge-small-en-v1.5 | weighted | 0.5179 | +0.1474 | 0.9045 | 0.7112 | 0.6859 |
| all-MiniLM-L6-v2 | weighted | 0.5103 | +0.1398 | **0.9403** | **0.7253** | **0.7125** |
| e5-small-v2 | weighted | 0.4858 | +0.1153 | 0.8962 | 0.6910 | 0.6773 |
| e5-small-v2 | dense | 0.4786 | +0.1081 | 0.7194 | 0.5990 | 0.6011 |
| — | BM25 | 0.3705 | — | 0.9075 | 0.6390 | 0.6357 |

Every configuration of every model clears the +0.10 materiality threshold.

## Runtime gate, for the primary model

| Criterion | Frozen limit | Measured | |
| --- | ---: | ---: | --- |
| Warm full-discovery P95 | ≤ 250 ms | **58.5 ms** | PASS |
| Incremental resident memory | ≤ 500 MB | **168.4 MB** | PASS |
| External model calls at inference | 0 | **0** | PASS |

Memory was measured separately because the evaluator recorded `0` — it did not
implement RSS capture. Baseline 28.6 MB → 62.2 MB after importing onnxruntime and
tokenizers → 166.3 MB after the ONNX session loads → 171.0 MB after one warm
query → 197.0 MB with the 17,702 × 384 float32 document matrix resident.

Artifact bytes: ONNX model 90,405,214 · tokenizer 466,247 · document embeddings
27,190,400 · **total 118,061,861 (112.6 MB)**.

`e5-small-v2` fails the runtime gate outright: query-embedding P50 is 205.3 ms
against MiniLM's 2.3 ms, and full-discovery P50 is 282 ms, over the 250 ms P95
limit. It is rejected on latency regardless of quality.

## What the rule decides

**Model: `sentence-transformers/all-MiniLM-L6-v2`** (Apache-2.0, revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`). It has the highest paraphrase
Recall@10 of any candidate, clears materiality by +0.1937, and clears every
measured runtime threshold.

**Dense retrieval is adopted in principle.** The adoption gate — "a material
frozen-evaluation improvement without unacceptable measured runtime cost" — is
met.

## What the rule does not decide, and why nothing ships yet

The frozen rule selects a **model**. It does not state a **configuration**
selection rule, and the two candidate configurations disagree about what is
better:

* **dense-only** wins the frozen primary metric (paraphrase 0.5642) and *loses*
  literal recall against BM25 (0.8544 vs 0.9075). Literal is half the query set
  and, on the live product, almost certainly more than half of real traffic.
* **weighted fusion** improves *both* slices at once (literal 0.9403, paraphrase
  0.5103) and has the best `all` nDCG@10 of anything measured (0.7125), but it is
  second on the frozen primary.

Choosing weighted fusion now would mean overriding the frozen primary metric
*after* seeing the numbers, which is exactly what `no_repeated_test_tuning`
exists to prevent. Choosing dense-only would mean shipping a known regression on
the larger slice because a single-slice primary metric did not look at it.

The honest reading is that the freeze has a gap: its primary metric is
single-slice, and a single-slice primary can select a configuration that harms
another slice. That gap was invisible before the measurement and is visible now.

**Decision: no ranker change ships in this commit.** The configuration choice
requires its own freeze — a stated multi-slice rule, committed before the numbers
are re-read — and that freeze is the next piece of work, not a footnote to this
one. The measurement stands; the shipping decision is deferred rather than
quietly resolved in favour of whichever number looks best.

## Consequences that follow from not shipping yet

* The public image is unchanged. No ONNX runtime, no model bytes, no embedding
  matrix, and the runtime dependency set is untouched.
* `DEFAULT_ALPHA` remains 1.0. BM25 still ranks, and the LSA embedding still does
  only near-duplicate suppression.
* The 112.6 MB of artifacts and the ~168 MB of resident memory are costs that
  have been measured but not yet incurred.

## Standing correction to an earlier claim

The previous milestone reported that "a learned dense ranker was evaluated and did
not improve retrieval over BM25 on this corpus". That finding was about **latent
semantic analysis fitted on this catalog**, and it remains true of that model. It
is not true of dense retrieval in general: a pretrained sentence encoder improves
paraphrase Recall@10 here by more than 50% relative. Any surface still carrying
the general form of that claim is now wrong and is corrected when the ranker
question is settled.
