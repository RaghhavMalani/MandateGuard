# LIVE INT-3 SUBSET-ABLATION ENGINEERING EXPERIMENT

- Run ID: `subset-live-recovery-20260831T135210Z-737beff7`
- Execution SHA-256: `e725661f523ea81dd4439aae7c49c826cf68d0a112a30b4d606de3eb09b06ec2`
- Plan SHA-256: `ed6f5c57cbea9ca0399b021c3516e829fa5cb51f7e025a1f003e9b0b1cfd284d`
- Feature-manifest SHA-256: `b5201911ac47dd1f17059431d88f4a2c4287875a1025821ebabcf8330a811f20`
- Start: `2026-08-31T13:52:10.160362Z`
- End: `2026-08-31T13:54:12.621181Z`
- Provider/model: `openai_responses` / `gpt-5.6-terra`
- Provider storage: `false`
- Retries: `0`
- Buyer calls: `0`
- Razorpay calls: `0`
- Recovery from run: `subset-live-20260831T130829Z-48fa600c`
- Failure type: `LOCAL_ARTIFACT_SERIALIZATION`

## Single-execution action stability

Observed stable subsets: **35/62**; observed unstable subsets: **27/62**.
The 62 subset observations are correlated within six frozen queries and are not independent commerce cases.

## Live call accounting

- INT-2 prior exact results reused: `15`
- Prior partial-run results recovered: `1`
- Recovery semantic executions attempted: `46`
- Recovery semantic executions completed: `46`
- Total new INT-3 provider requests across both runs: `47`
- Provider errors: `0`
- Total recovery input tokens: `22393`
- Total recovery output tokens: `4223`
- p50 recovery semantic latency (ms): `2440.759750024881`
- p95 recovery semantic latency (ms): `4442.384249938186`
- Partial-run token/latency telemetry: `unavailable after local serialization failure`

## Failure recovery

The first provider response succeeded, then local artifact serialization failed. The response remained preserved in the exact-input semantic cache; the deterministic float serializer was fixed at commit `24bb068fc4af9d979ed57e0826fd0ed9687ae344`; recovery reused the exact input result and continued without retrying that model call.

## Safety-relevant action transitions

- FULL BLOCK -> SUBSET ALLOW: `0`
- FULL ALLOW -> SUBSET BLOCK: `0`
- FULL ALLOW -> SUBSET REVIEW: `17`
- FULL BLOCK -> SUBSET REVIEW: `10`
- FULL REVIEW -> SUBSET ALLOW: `0`
- FULL REVIEW -> SUBSET BLOCK: `0`

Detailed per-query, subset-size, minimum observed stable subset, and empirical monotonicity records are in `stability_summary.json`.

No sufficiency model was fitted or evaluated in this milestone.
