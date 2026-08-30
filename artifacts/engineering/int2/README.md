# INT-2 engineering artifacts

> **NON-BENCHMARK ENGINEERING EXPERIMENTATION.**

These files are engineering outputs, not a benchmark, held-out evaluation, or
evidence of generalization. The frozen corpus contains six synthetic semantic
cases, nine evidence documents, and manually authored relevance/required
annotations. No claim here establishes that RAG improved authorization quality
or that one retrieval strategy is generally better.

The preserved live runs are:

- `stage-a-live-20260830T113054Z-1a94a4a`: 192 retrieval observations over six
  queries and 32 configurations, using one embedding request for 15 unique
  texts.
- `stage-b-live-20260830T123856Z-0e4213c`: 36 nominal observations, comprising
  six zero-evidence observations and 30 evidence-bearing observations. These
  contain 15 unique semantic inputs, not 36 independent model observations,
  with one live semantic execution per unique input.
- `stage-c-cache-live-20260830T131136Z-3946aa5`: three cold/warm cache cases,
  with one cold and one warm observation per case.

On six synthetic engineering queries, semantic retrieval recovered both
annotated required evidence items for every query by k=3, while lexical
retrieval required k=5. The semantic Precision@3 and lexical Precision@5
values are evaluated at different depths and do not support a general ranking
claim. Stage-B condition C (semantic k=3) and condition D (hybrid alpha=0/k=3)
were operationally identical, with identical semantic-input hashes for 6/6
cases; they are not independent evidence.

Stage B recorded a prominent negative result: lexical k=1 retrieved only one
of two annotated required evidence items per query on average (Recall@k=0.5),
yet downstream authorization outcomes remained aligned with all six frozen
engineering expectations. This motivates studying decision-sufficient evidence
sets, but does not prove which evidence was actually necessary. Complete
absence of trusted evidence produced `NOT_EVALUATED -> REVIEW`.

Across three frozen engineering cases, exact-input cache hits eliminated repeat
semantic API calls and 1,905 semantic tokens. Median observed total latency was
approximately 1.9 s cold and approximately 3 ms warm. These are local
engineering latency measurements (`n=3`, one cold and one warm observation per
case); the cold path includes a live API round-trip, and the result is not a
production-throughput measurement. All 15 mutations across evidence, mandate,
transaction, model, and prompt invalidated the cache. Stage C tampered one
cached `VIOLATION` toward `PASS`, and the integrity checker rejected it.

The cases were frozen after Stage-A condition selection but before any Stage-B
semantic execution. The study has no held-out corpus, repeated stochastic
trials, or distribution-shift/adversarial evaluation. See
`docs/INT2_RETRIEVAL_EXPERIMENTS.md` for metric definitions, methodology,
interpretation, and full limitations.

Annotated retrieval recall was not predictive of downstream decision changes
on this small corpus. This negative result motivates studying evidence
sufficiency and value-of-information rather than optimizing retrieval depth
alone.
