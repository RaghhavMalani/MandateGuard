# MandateGuard Commerce Lab

The judge-facing Commerce Lab is a local, dependency-free HTTP application over the existing MandateGuard controller. Offline Demo Mode is the default and makes zero external network calls. Live Test Mode is opt-in, disabled until all server-side credentials are present, and accepts only Razorpay keys with the `rzp_test_` prefix.

## Start locally

From the repository root, install the package and run the server:

```powershell
python -m pip install -e .
python scripts/run_commerce_lab.py
```

Open `http://127.0.0.1:8080`. The server binds `0.0.0.0` by default so the same command works on common PaaS hosts; set `MANDATEGUARD_PRODUCT_HOST=127.0.0.1` to restrict a local run to loopback. The platform `PORT` variable takes precedence over `MANDATEGUARD_PRODUCT_PORT`, and explicit `--host` / `--port` arguments remain available.

The initial page load reads only local static assets and `/api/config`. It never calls OpenAI or Razorpay. A purchase run starts only after an explicit click on `RUN AI BUYER`.

## Demo journeys

- `SAFE PURCHASE` exercises the normal buyer, retrieval, deterministic, semantic, capability, ledger, and execution path. It creates one order through the offline Test Mode-compatible double and reports one adapter call, zero external calls.
- `POLICY VIOLATION` reaches `BLOCK` through the normal frozen controller and reports zero Razorpay calls.
- `AMBIGUOUS EVIDENCE` reaches `REVIEW` through the normal frozen controller and reports zero Razorpay calls.
- `RECOVERABLE REVIEW` selects the dedicated Lumen Studio `aurora-focus-lamp`. At the product default retrieval policy, its registered merchant terms and listing do not establish individual-study suitability or billing, so it starts at `REVIEW` with zero payment calls. `ACQUIRE TRUSTED EVIDENCE` accepts no request fields, verifies the complete server-registered Merchant SKU Terms scope including `aurora-sku-terms-v2`, creates a new canonical evidence set, and reruns the full controller. That fixture then reaches a fresh `ALLOW` and executes exactly once through the offline double.

After an `ALLOW`, use `TEST CAPABILITY REPLAY` to submit the same signed capability again. The D6 nonce ledger rejects it as `NONCE_ALREADY_USED` before another provider call.

The recovery endpoint is `POST /api/runs/{run_id}/recover` with an empty JSON object. It
does not accept a URL, evidence text, source ID, merchant, or SKU. The server registry owns
all candidate selection and enforces two acquisition rounds and four new evidence items.
An acquisition either verifies the complete manifest-defined source scope or remains
`REVIEW`; the server never authorizes a truncated response. Recovery reads a fresh trusted
time, reserves the round before provider work, and persists hash-linked provenance in
`recovery-audit.sqlite3` under the configured state directory.
See [MANDATEGUARD_RESOLVE.md](MANDATEGUARD_RESOLVE.md) for the security boundary and
non-benchmark engineering evaluation.

Set `MANDATEGUARD_STATE_DIR` to a directory the process can write when the
semantic cache, execution ledger, mandate state, and recovery audit should
survive service
reopen. Reusing the same directory on the same durable filesystem preserves all
three SQLite stores. If it is absent, the service uses a new temporary directory
and reports `EPHEMERAL_TEMPORARY_DIRECTORY` through config/health.

## Live Test Mode

Copy `.env.example` to a local `.env` and provide these server-only values:

```text
OPENAI_API_KEY
MANDATEGUARD_SEMANTIC_MODEL
RAZORPAY_KEY_ID
RAZORPAY_KEY_SECRET
MANDATEGUARD_EXECUTION_HMAC_KEY
```

`MANDATEGUARD_EXECUTION_HMAC_KEY` must be at least 32 bytes. The browser receives availability and validation messages only, never credential values or signed capability material. Live Test Mode can make real OpenAI requests and a real Razorpay Test Mode order request after explicit submission.

## Verification

```powershell
python -m pytest -q
node --test tests/ui/commerce-lab.test.mjs
```

The product tests cover `ALLOW`, `BLOCK`, `REVIEW`, bounded `REVIEW → ALLOW/BLOCK/REVIEW`
recovery, server-side source resolution, evidence hashes, acquisition failures, replay
rejection after recovery, duplicate submission idempotency, zero-call claims, secret
non-disclosure, and the HTTP boundary.
