# Semantic discovery v2 evaluation protocol

Status: **frozen before pretrained-model evaluation**.

This protocol fixes the semantic-retrieval test before any candidate model is
run. The freeze contains 124 human-authored queries: 62 literal queries and 62
meaning-preserving paraphrases. Query pairs share a relevance predicate, but no
ranked result, score, model output, or measured outcome appears in the freeze.

## Relevance

Relevance is binary and is evaluated against every one of the 17,702 frozen
catalog documents. Predicates use only normalized listing fields:

- `require_title_any`: one case-folded substring must occur in the title;
- `require_any_terms`: one substring must occur in title, category, brand, or description;
- `require_all_terms`: every substring must occur in those searchable fields;
- `exclude_terms`: none may occur in those searchable fields;
- `categories`: exact top-level category allowlist;
- `brands`: exact case-insensitive brand allowlist;
- `max_price_minor`: inclusive price ceiling in paise; missing prices fail.

Predicate evaluation across the entire catalog avoids system-contributed pools.
It still has a known lexical bias because annotations are explainable field
rules. Paired paraphrases deliberately avoid the product noun where practical,
and paraphrase Recall@10 is therefore the primary selection metric.

## Retrieval configurations

Each candidate generator searches the full hard-filtered catalog independently:

1. BM25 top 100;
2. pretrained dense top 100;
3. Reciprocal Rank Fusion over their independent lists;
4. one preregistered weighted fusion, reported but adopted only if justified.

The final cutoff is 10. Structured category, brand, price, and exclusion filters
are deterministic and apply before scoring. Near-duplicate handling applies only
after fusion and may not introduce a candidate absent from both generators.
RRF uses rank constant 60. Weighted fusion is fixed at 0.5 normalized BM25 plus
0.5 normalized dense score over the union of their top-100 lists; a missing
candidate score is zero and catalog document ID breaks ties.

Every dense candidate uses the same served document representation: title,
brand, and full category path separated by newlines, truncated to 128 wordpiece
tokens. Historical descriptions are excluded because they are long, noisy crawl
text; this decision is frozen before a candidate completes evaluation.

## Metrics and slices

Report Recall@1, Recall@5, Recall@10, MRR, and binary nDCG@10 for all queries and
for these overlapping slices: literal, paraphrase, category, brand-constrained,
and budget-constrained. A query with no relevant catalog document is a protocol
error, not a zero-scored query.

For Recall@K the denominator is `min(K, relevant document count)`, matching the
existing catalog protocol for broad predicate sets. MRR is the reciprocal rank
of the first relevant document. Binary nDCG@10 divides DCG by the ideal DCG for
`min(10, relevant document count)`. Slice results are unweighted query means.

Latency is measured separately for query tokenization, query embedding, BM25,
dense exact search, fusion, and the full discovery request. Report P50/P95/P99,
cold model load, index load, resident memory, and artifact byte counts.

## Model adoption rule

Select by highest paraphrase Recall@10. Tie-break in order with all-query
nDCG@10, full-discovery P95, resident memory, then artifact bytes. Dense retrieval
is enabled in the shipped ranker only when the frozen evaluation shows a material
improvement without unacceptable measured latency or runtime cost. A losing
pretrained model is recorded and rejected rather than shipped.

"Material" is preregistered as at least +0.10 absolute paraphrase Recall@10 over
BM25. The selected runtime must also keep warm full-discovery P95 at or below
250 ms, incremental resident memory at or below 500 MiB, and request-time model
network calls at exactly zero.

Every evaluated model requires a provenance record taken from its authoritative
model source before evaluation. The record must include the exact revision,
license, model card, embedding dimension, tokenizer identity, maximum sequence
length, intended retrieval usage, and artifact hashes. The served runtime makes
no model-host API call.

The immutable machine-readable record is
`data/eval/semantic-v2/FREEZE.json`; it binds the query file, catalog, candidate
cutoffs, metrics, slices, and model-selection rule. Generated outcomes belong
only to later commits.
