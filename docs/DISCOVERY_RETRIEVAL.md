# Semantic retrieval over the discovery catalog

What was built, what it measures, and what the measurement said — including
where it said the learned component was not worth its rank.

---

## Architecture

```
arbitrary intent text
   │
   ├─ deterministic constraint extraction ─────────► price ceiling, currency,
   │  (rules, never a model)                         quantity, recurrence stance,
   │                                                 exclusions, brand hints
   ├─ structured filters ──────────────────────────► hard. A listing above the
   │                                                 stated ceiling is not a
   │                                                 candidate, not a lower rank
   ├─ lexical candidate generation ────────────────► BM25 over a frozen inverted
   │                                                 index, field-weighted
   ├─ dense scoring (evaluated, NOT shipped for ranking) ► cosine in a frozen LSA
   │                                                 space, candidates only
   ├─ hybrid score ────────────────────────────────► α·lexical + (1−α)·dense
   │                                                 both min-max normalized
   └─ near-duplicate suppression ──────────────────► document-to-document
                                                      similarity + title agreement
```

Everything after "arbitrary intent text" runs on the Python standard library.

---

## Why there is no transformer at request time

The brief preferred a small local sentence-transformer. That preference collides
with a hard constraint of this product, and the collision is worth stating
precisely rather than hand-waving past.

**Arbitrary buyer intent means the *query* must be encoded at request time.**
Document vectors can be precomputed offline; a query someone types cannot.
Serving a sentence-transformer therefore means PyTorch in the public image — on
the free deployment tier, a multi-gigabyte image and a cold start measured in
tens of seconds, for a page whose entire promise is "zero external calls, starts
instantly, installs nothing".

So the embedding model is trained offline and frozen into two tables:

* `document_vectors` — one L2-normalized int8 vector per listing;
* `projection` — for each vocabulary term, the int8 row `idf[t] · V[t]`.

Encoding a query is then a sparse-by-dense product over the query's own terms:
**0.082 ms** in pure Python, measured. This is a real learned embedding fitted on
the catalog (TF-IDF → truncated SVD, i.e. latent semantic analysis), not a
hashing trick — and it is a **linear** model, not a contextual encoder. That
distinction turns out to matter a great deal, as the evaluation below shows.

---

## The evaluation set

44 queries, authored **before** any measurement, in
[`data/eval/retrieval_queries.json`](../data/eval/retrieval_queries.json).

Relevance is defined per query by a **human-authored predicate** over the
listing's own structured fields, evaluated across all 17,702 listings.

*Why not pooled annotation?* Pooling judgements from the systems under test
biases the result toward whichever system contributed the pool. A predicate
applied to every listing has no pooling bias and can be re-checked by anyone
reading it.

*Known bias, stated up front.* A predicate written over title, category, and
price terms correlates with lexical evidence, so the `literal` family
understates what a dense retriever adds. The `paraphrase` family exists to
measure that separately: those 14 queries deliberately avoid the vocabulary
their own predicate uses ("something that keeps my phone steady while I am
driving" → car mounts).

Relevant-set sizes range from 2 to 793 (median 50). **Recall@k is capped**: with
793 relevant listings no ranking of length 10 could retrieve more than 10 of
them, so the denominator is `min(|relevant|, k)`. Precision@5 and MRR are
reported alongside because they carry no such caveat.

---

## Results

Candidate depth 300, top-k 10, 44 queries, 17,702 listings.

Query set digest `d369cf66c0504e995414210e88c8d364f554d5b217bfe5382b49fb5b2f5d9763`. The set and these results were committed together, so this is a **fixed evaluation set committed with this engineering milestone**, not an independently preregistered evaluation, and it is not described as one. Future retrieval work freezes the query set in one commit and executes it in a later one.

| Configuration | R@5 | R@10 | P@5 | MRR | literal R@10 | paraphrase R@10 | DistinctTitle@8 | p50 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `lexical_only_alpha_1.00__deduplicated` **(shipped)** | 0.6205 | 0.6205 | 0.6136 | 0.6851 | 0.8633 | 0.1000 | 0.8523 | 13.9 |
| `lexical_only_alpha_1.00__raw` | 0.6250 | 0.6227 | 0.6182 | 0.6837 | 0.8667 | 0.1000 | 0.8239 | 13.3 |
| `hybrid_alpha_0.90__raw` | 0.6250 | 0.6136 | 0.6182 | 0.6807 | 0.8567 | 0.0929 | 0.8239 | 34.2 |
| `hybrid_alpha_0.70__raw` | 0.6250 | 0.6045 | 0.6182 | 0.6746 | 0.8533 | 0.0714 | 0.8153 | 38.1 |
| `hybrid_alpha_0.50__raw` | 0.6023 | 0.5818 | 0.5955 | 0.6468 | 0.8500 | 0.0071 | 0.7898 | 36.9 |
| `hybrid_alpha_0.30__raw` | 0.5841 | 0.5604 | 0.5773 | 0.6326 | 0.8219 | 0.0000 | 0.7699 | 33.3 |
| `dense_only_alpha_0.00__raw` | 0.5318 | 0.5260 | 0.5318 | 0.5663 | 0.7548 | 0.0357 | 0.7699 | 35.3 |

Full report, including every per-query result:
[`artifacts/engineering/discovery/retrieval_evaluation.json`](../artifacts/engineering/discovery/retrieval_evaluation.json).

---

## What the measurement said

### 1. The learned dense retriever did not beat BM25

The alpha sweep is **monotone in the wrong direction**. Every increment of dense
contribution lowered R@10 and MRR. There is no blend at which the embedding
improves ranking on this catalog.

So `DEFAULT_ALPHA = 1.0`. That value was selected by the evaluation, not by
preference, and the shipped configuration is the argmax of MRR across the whole
grid.

### 2. Latent semantic analysis does no paraphrase matching here

The paraphrase family scores **0.10 at best and 0.00 at worst**, for every
configuration including exhaustive dense scan over the full corpus (measured
separately during development). LSA cannot bridge "keeps my phone steady while
driving" → "car mount", because those phrases do not co-occur in 17k short
product listings. The failure mode is visible and consistent: when a query has
few in-vocabulary content terms, the projected vector collapses toward a
dominant direction and the same generic listings win for unrelated queries.

Eight principled variants were tried before accepting this — dropping the
leading singular components, embedding title+category only, truncating the
description, and raising the dimensionality to 320. Literal-family R@10 moved
between 0.740 and 0.790; **paraphrase R@10 stayed at exactly 0.000 for all
eight.** The conclusion is about the model class, not about tuning.

A contextual encoder would very likely help. It could not be served in a
dependency-free image, and that trade-off is the finding, not an excuse.

### 3. Where the embedding does earn its place

**Near-duplicate suppression.** A catalog crawl repeats the same product across
sizes, colours, and seller listings — four identical "925 Silver Silver
Bracelet" rows at different prices, three "President School Waterproof
Backpack". Showing eight of them is a worse answer than showing eight different
products.

Document-to-document similarity is the direction the frozen LSA space is
actually good at: both sides are in-vocabulary product text of similar length.
Enabling suppression moves **DistinctTitle@8** from
0.8239 to **0.8523**, moves MRR from
0.6837 to **0.6851**, and costs
0.0023 of capped R@10 — because the relevance
predicates count each duplicate listing as separately relevant, so collapsing four
copies of one bracelet costs recall while making the answer more useful. Both
halves of that trade are in the report.

`DistinctTitle@8` is named for what it counts: **unique display titles** among the
eight results the interface shows. It is not a count of distinct *products*. Two
listings can share a title and be different offers, which is exactly why the
suppression rule below is what it is.

Suppression requires **four things to agree**:

1. the structured identity fields — same source, brand, top category, currency,
   **and price**;
2. embedding similarity ≥ 0.985;
3. title-token Jaccard ≥ 0.6; and
4. neither listing is a registered product.

An earlier revision suppressed on an **exact title match alone**, and reported
DistinctTitle@8 of 1.00 as a result. That number was bought by hiding listings
that were not duplicates: the crawl carries the same title at different prices,
in different sizes and colours, from different sellers, and collapsing on the
display string discarded the cheaper offer roughly half the time. The shortcut
is gone and the honest figure is 0.8523.

Requiring the embedding *and* the fields also means a degenerate or badly fitted
embedding cannot hide a genuinely different product on its own, which would be a
worse failure than showing a duplicate. A registered product is never suppressed
at all: it is the only kind of listing that can reach authorization.

### 4. A dense fallback was built and then deleted

A full-scan dense fallback for queries BM25 cannot match looked useful until it
was checked: the lexical vocabulary (`min_df=1`) is a **strict superset** of the
embedding vocabulary (`min_df=3`) over the same corpus and the same analyzer.
Any query the dense index can encode is a query BM25 has postings for, so the
fallback could never execute. Unreachable code that looks like a safety net is
worse than no safety net, so it was removed rather than shipped.

---

## Frozen artifacts

| Artifact | Bytes | Contents |
| --- | ---: | --- |
| `lexical_index.mgdx` | 2,012,379 | 25,539 terms, delta-varint postings, uint8 term frequencies, doc lengths |
| `embedding_index.mgdx` | 5,305,240 | 9,243-term projection + 17,702 × 192 int8 document vectors |
| `category_classifier.mgdx` | 262,838 | 22-class linear model, int8 per-class quantization |

Each is a self-describing binary container: a JSON header followed by named byte
sections, readable with nothing but the standard library. Every artifact records
the analyzer version and the catalog SHA-256 it was built against, and **refuses
to load** against a different one — so a rebuilt catalog cannot silently be
served through a stale index.

---

## One analyzer, shared

The offline trainer and the runtime query path call the same
[`analyzer`](../src/mandateguard/discovery/index/analyzer.py) module. If they
analyzed text differently, the frozen vocabulary would silently stop matching
the queries it was trained for. `ANALYZER_VERSION` is written into every
artifact and checked on load.

The stop list is deliberately small. An aggressive one removes `no` and `not` —
words that carry mandate meaning. "No subscriptions" must not become
"subscriptions".

---

## Reproducing

```bash
pip install -r requirements-train.txt
python scripts/import_discovery_catalog.py
python scripts/train_discovery_models.py
python scripts/evaluate_discovery_retrieval.py
```
