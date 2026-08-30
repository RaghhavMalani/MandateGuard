# INT-2 Stage A Live Retrieval Engineering Experiment

- Run ID: `stage-a-live-20260830T113054Z-1a94a4a`
- Status: `COMPLETE_STAGE_A_STOP`
- Execution SHA: `1a94a4a77dd6cf05ffccd92f83d313d447ae3cbb`
- Run start (UTC): `2026-08-30T11:30:54.141Z`
- Run end (UTC): `2026-08-30T11:36:59.349Z`
- Embedding provider: `OpenAIEmbeddingProvider`
- Embedding model: `text-embedding-3-small`
- Vector-precompute design: exact-text deduplication, one batch for all 15 unique texts, and one immutable `EmbeddingSnapshot` reused across semantic-only and hybrid configurations.
- Matrix: 6 queries × 32 configurations = 192 observations.

This is a LIVE RETRIEVAL ENGINEERING EXPERIMENT. The results below are retrieval metrics and retrieval-risk proxies only. No semantic authorization was performed.

## Embedding precompute

- Unique document texts: 9
- Unique query texts: 6
- Unique texts total: 15
- Embedding API calls: 1
- Vector dimension: 1536
- Input tokens: 289
- Precompute latency: 5550.828 ms
- Manual retries: 0

## Top-line retrieval

- No retrieval (k=1): all-required 0/6; mean Recall@k 0.000000; mean Precision@k 0.000000; MRR 0.000000; required-evidence miss count 12
- Best lexical (k=5): all-required 6/6; mean Recall@k 1.000000; mean Precision@k 0.638889; MRR 1.000000; required-evidence miss count 0
- Best semantic (k=3): all-required 6/6; mean Recall@k 1.000000; mean Precision@k 0.722222; MRR 1.000000; required-evidence miss count 0
- Best hybrid (alpha=0.0, k=3): all-required 6/6; mean Recall@k 1.000000; mean Precision@k 0.722222; MRR 1.000000; required-evidence miss count 0
- Worst retrieval condition (`no_retrieval.alpha-na.k-1`): required-evidence miss count 12

## Pareto-nondominated configurations

No single configuration is automatically declared optimal.

- `hybrid.alpha-0.00.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `hybrid.alpha-0.00.k-2` — all-required 3/6; mean Recall@k 0.750000; mean Precision@k 0.750000; MRR 1.000000; required-evidence miss count 3; top_k 2
- `hybrid.alpha-0.00.k-3` — all-required 6/6; mean Recall@k 1.000000; mean Precision@k 0.722222; MRR 1.000000; required-evidence miss count 0; top_k 3
- `hybrid.alpha-0.25.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `hybrid.alpha-0.50.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `hybrid.alpha-0.75.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `hybrid.alpha-1.00.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `lexical_only.alpha-na.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `semantic_only.alpha-na.k-1` — all-required 0/6; mean Recall@k 0.500000; mean Precision@k 1.000000; MRR 1.000000; required-evidence miss count 6; top_k 1
- `semantic_only.alpha-na.k-2` — all-required 3/6; mean Recall@k 0.750000; mean Precision@k 0.750000; MRR 1.000000; required-evidence miss count 3; top_k 2
- `semantic_only.alpha-na.k-3` — all-required 6/6; mean Recall@k 1.000000; mean Precision@k 0.722222; MRR 1.000000; required-evidence miss count 0; top_k 3

## Frozen Stage-B selection

The manifest was frozen after Stage-A scoring and before any semantic Stage-B call.

- A. NO_RETRIEVAL CONTROL: `no_retrieval` / alpha `None` / k `1`
- B. LEXICAL BASELINE: `lexical_only` / alpha `None` / k `5`
- C. SEMANTIC BASELINE: `semantic_only` / alpha `None` / k `3`
- D. BEST HYBRID: `hybrid` / alpha `0.0` / k `3`
- E. PRODUCTION DEFAULT: `hybrid` / alpha `0.4` / k `5`
- F. LOW-EVIDENCE STRESS CONDITION: `lexical_only` / alpha `None` / k `1`

## Integrity

- Embedding provider calls: 1
- Buyer calls: 0
- Semantic-verifier calls: 0
- Razorpay calls: 0
- Source changes: 0
- Relevance annotation changes: 0
- Catalog or merchant-evidence changes: 0
- Benchmark changes: 0
- TAXONOMY.md changes: 0
- INT-1 evidence changes: 0
