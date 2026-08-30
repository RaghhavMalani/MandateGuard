# Third INT-1 Razorpay Test Mode execution diagnostic

- Run ID: `int1-razorpay-exec-20260830T074115Z-507323be`
- Execution SHA: `dc588074fec152c63f7d11013aa2af4577b1fcf9`
- Status: `EXECUTED`
- Start: `2026-08-30T07:41:15.623673Z`
- End: `2026-08-30T07:41:31.205012Z`
- Top-level checkout attempts: `1`
- Razorpay Test Mode execution calls: `1`

The production live-AI and explicit-execution path ran with its unchanged
intent, models, prompts, catalog, evidence, retrieval defaults, MandateGuard
policy, and tool-round budget. SDK retries were fixed to zero for this
diagnostic.

Final action: `ALLOW`.
Capability signature valid: `True`.
Transaction binding matched: `True`.
Request binding matched: `True`.
Order created: `True`.
Capability consumed: `True`.
Replay rejected by the local nonce ledger before network:
`True`.

The production `--execute` path resolves the live buyer before entering the
timed `run_agentic_checkout` call. The trace's near-zero buyer timing therefore
measures only fixed typed-output consumption; live buyer latency is recorded as
unavailable rather than being mislabeled. The total reported engineering wall
time includes diagnostic harness and artifact-finalization overhead.

No API key, Razorpay secret, HMAC key, Authorization header, capability
signature, hidden provider reasoning, or chain-of-thought is recorded.
