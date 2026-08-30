# LIVE CACHE ENGINEERING EXPERIMENT

NON-BENCHMARK ENGINEERING DATA. Latencies are local engineering measurements,
not production latency or benchmark throughput.

## Evidence

- Run ID: `stage-c-cache-live-20260830T131136Z-3946aa5`
- Execution SHA: `3946aa50c477881b1b085e35b60c9a411b6c8d64`
- Started: `2026-08-30T13:11:36.479792Z`
- Live execution started: `2026-08-30T13:19:10.181699Z`
- Ended: `2026-08-30T13:19:16.365143Z`
- Status: `COMPLETED`
- Provider/model: `openai_responses` / `gpt-5.6-terra`

## Cold/warm observations

| Case | Verdict | Action | Cold | Calls | Cold total ms | Warm | Calls | Warm total ms | Speedup |
| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| STUDYGLOW | PASS | ALLOW | MISS | 1 | 2497.3265 | HIT | 0 | 4.1814 | 597.2465x |
| MARKET-EDGE | VIOLATION | BLOCK | MISS | 1 | 1715.4465 | HIT | 0 | 3.0711 | 558.5772x |
| FLEXI | ABSTAIN | REVIEW | MISS | 1 | 1904.5394 | HIT | 0 | 3.0818 | 617.9958x |

## Token savings

- Cold input/output tokens: 1684 / 221
- Warm input/output tokens: 0 / 0
- Saved input/output tokens: 1684 / 221
- Experiment cost rates configured: false; no cost estimate was made.

## Mutation invalidation

- Passes: 15/15
- Evidence, mandate, transaction, model, and prompt mutations were lookup-only.
- Semantic calls during mutation checks: 0

## Cache integrity attack

- Tamper attempted: true
- Direction: normalized VIOLATION result toward PASS
- Tampered ALLOW returned: false
- Safe rejection: true
- Integrity checker status/failure: MISS / True
- Provider calls: 0

## Aggregate

- Cold provider calls: 3
- Warm provider calls: 0
- Cache MISS/HIT cases: 3 / 3
- p50 cold total latency: 1904.5394 ms
- p50 warm total latency: 3.0818 ms
- Median latency reduction: 1901.4576 ms
- Median speedup ratio: 597.2464963887693x

## Integrity

- Buyer calls: 0
- Razorpay calls: 0
- Semantic retries: 0
- Tuning changes: 0
- Fresh run-specific SQLite cache began at 0 rows and ended at 3 rows.
- Stage-B and INT-1 caches were not reused.
- Systemic error: `null`
