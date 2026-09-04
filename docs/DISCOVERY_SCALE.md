# Scale benchmark

What one process on one machine actually does with a 17,702-listing catalog.

Nothing here is extrapolated, and nothing here is a distributed-throughput
claim.

---

## Reproducing

```bash
python scripts/run_discovery_scale_benchmark.py
```

Needs **no training dependencies**. It exercises the same standard-library
runtime the public demo serves.

---

## Workload

500 frozen queries, generated deterministically (`seed=20260903`) from the
catalog's own vocabulary — terms are taken from real listing titles, so the
workload exercises terms the index actually contains rather than words chosen to
look fast. Eight templates of deliberately varied selectivity:

```
{term} under Rs {price}
buy {term} below {price}
{brand} {term} under Rs {price}
{term} for {audience} under {price}
{term}
{term} and {second} under Rs {price}, one-time payment only
cheap {term} no subscriptions
{adjective} {term} under Rs {price}
```

A benchmark made only of narrow queries measures the fast path and calls it the
system. `workload_digest` pins the exact 500 so a later run is comparable.

Executed: **1,525** queries (25 warm-up + 500 retrieval-only + 500 throughput +
500 individually timed).

---

## Corpus and index

| | |
| --- | ---: |
| Catalog listings | **17,702** |
| Of those, registered products with merchant evidence | 8 |
| Top-level categories | 26 |
| Distinct category paths | 6,338 |
| Distinct brands | 3,370 |
| Lexical index terms | 25,539 |
| Embedding dimensions | 192 |
| Embedding vocabulary | 9,243 |
| Catalog on disk (gzip JSONL) | 4,933,678 B (4.71 MB) |
| Index on disk (lexical + embedding) | 7,317,619 B (6.98 MB) |

---

## Startup and memory

| | |
| --- | ---: |
| Cold load (catalog + both indexes + classifier) | **0.264 s** |
| Resident memory before load | 22.5 MB |
| Resident memory after load | 70.3 MB |
| **Attributable to the engine** | **47.8 MB** |

Cold load is the number that made the frozen-artifact design worth building.
Constructing the BM25 index from the catalog at startup takes ~2 s; reading the
precomputed binary takes 15 ms.

---

## Latency

Retrieval only (BM25 + structured filters + dense rerank + dedup):

| Percentile | ms |
| --- | ---: |
| P50 | **9.065** |
| P95 | 18.845 |
| P99 | 22.308 |
| Max | 27.356 |

Full request (intent parsing + retrieval + classification + mismatch + anomaly
analytics + transactability, for 8 candidates):

| Percentile | ms |
| --- | ---: |
| P50 | **11.183** |
| P95 | 21.011 |
| P99 | 24.640 |
| Max | 27.485 |

Throughput, single process, no concurrency:

| | |
| --- | ---: |
| Full requests / second | **87.6** |
| Retrieval-only / second | 108.6 |

Component costs, measured separately:

| Operation | ms |
| --- | ---: |
| Query encode into the frozen LSA space | 0.082 |
| Dense rerank of 300 candidates | 5.44 |

7 of the 500 queries returned no results — every candidate was removed by a
stated price ceiling. That is the filter working, and it is reported rather than
excluded from the sample.

---

## Environment

| | |
| --- | --- |
| Python | 3.12.13 |
| Platform | Windows-11-10.0.26200-SP0 |
| Processor | Intel64 Family 6 Model 183 Stepping 1 |
| Processes | 1 |
| Concurrency | none |
| Network | none |

---

## What this does not show

* **Not distributed throughput.** One process, one machine, sequential.
* **Not extrapolated.** There is no "and therefore N million listings" claim here,
  because nothing was measured at that size.
* **Not production traffic.** A generated workload over a 2016 crawl.
* **Not authorization performance.** These are retrieval numbers. The
  authorization controller's own evidence is separate and is reported
  separately, in
  [`RESOLVE_EVALUATION_RESULTS.md`](RESOLVE_EVALUATION_RESULTS.md).

The honest ceiling of this measurement: one Python process on the machine named
above served this catalog at a 73.9 ms retrieval P99 and a
86.2 ms full-request P99, with
48.2 MB of resident state attributable to the engine.

No container was measured. An earlier revision of this document claimed a
free-tier container figure that no benchmark here produced; the sentence has been
removed rather than re-derived. What happens at 10× or 1,000× the corpus, on
other hardware, or under concurrency was not measured and is not claimed.

Full report:
[`artifacts/engineering/discovery/scale_benchmark.json`](../artifacts/engineering/discovery/scale_benchmark.json).

---

## Deployment impact

| | Before | After |
| --- | ---: | ---: |
| Packages installed in the runtime image | 0 | **0** |
| Build context | 1.52 MB | 13.46 MB |
| Added by frozen artifacts | — | 11.94 MB |
| Server cold start | ~0 s | **+0.26 s** |
| External calls on page load | 0 | **0** |

The runtime stays standard-library only. scikit-learn and NumPy build the
artifacts (`requirements-train.txt`) and never enter the image — enforced by
[`tests/test_runtime_has_no_third_party_dependencies.py`](../tests/test_runtime_has_no_third_party_dependencies.py),
which parses every served module and fails if one imports a training dependency,
and which imports each served module in a subprocess to confirm nothing pulls
one in transitively.

If the artifacts are absent, the server still starts and every authorization
journey still works; the discovery surface reports why it is unavailable.
