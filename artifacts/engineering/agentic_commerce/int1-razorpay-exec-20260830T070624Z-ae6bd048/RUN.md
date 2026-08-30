# INT-1 Razorpay Test Mode execution diagnostic

- Run ID: `int1-razorpay-exec-20260830T070624Z-ae6bd048`
- Execution SHA: `f684b15eedfa422b6f43fd8e1ab071c32ce07644`
- Status: `STOPPED_PRE_EXECUTION`
- Evidence window: `2026-08-30T07:06:24.732763Z` to `2026-08-30T07:06:51.906643Z`

The exact branch, SHA, clean-worktree, Python 3.12, OpenAI key, and complete
test-suite gates passed. The suite result was exactly `452 passed`.

The diagnostic stopped before creating a cache, invoking the buyer, issuing a
capability, or calling Razorpay because the configured environment did not
contain `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, or a sufficiently long
`MANDATEGUARD_EXECUTION_HMAC_KEY`. Test Mode therefore could not be established
with certainty.

No credential was changed, no fallback was used, and no retry or external call
was attempted. This evidence contains no secret values, authorization headers,
capability signature material, or chain-of-thought.
