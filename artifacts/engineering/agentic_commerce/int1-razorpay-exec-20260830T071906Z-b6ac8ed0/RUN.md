# Second INT-1 Razorpay Test Mode execution diagnostic

- Run ID: `int1-razorpay-exec-20260830T071906Z-b6ac8ed0`
- Execution SHA: `8f22432ba913e23f9437543fc64905e61a445750`
- Status: `STOPPED_PRE_EXECUTION`
- Evidence window: `2026-08-30T07:19:06.478697Z` to `2026-08-30T07:19:22.099235Z`

The exact branch, SHA, clean-worktree, Python 3.12, Git-ignore, model,
OpenAI-key, Razorpay Test Mode key-prefix, Razorpay-secret, and full-suite gates
passed. The full suite result was exactly `452 passed`.

The configured MandateGuard execution HMAC key was present but contained fewer
than the required 32 UTF-8 bytes. The run therefore stopped before creating a
semantic cache, invoking the live buyer, issuing a capability, or calling
Razorpay.

The configuration was not changed. There was no retry, bypass, top-level
checkout attempt, OpenAI request, or Razorpay request. The previous run
`int1-razorpay-exec-20260830T070624Z-ae6bd048` remains untouched.

These artifacts contain no API keys, Razorpay secrets, Authorization headers,
capability signatures, HMAC material, or chain-of-thought.
