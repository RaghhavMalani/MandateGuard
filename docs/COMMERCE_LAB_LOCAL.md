# MandateGuard Commerce Lab

The judge-facing Commerce Lab is a local, dependency-free HTTP application over the existing MandateGuard controller. Offline Demo Mode is the default and makes zero external network calls. Live Test Mode is opt-in, disabled until all server-side credentials are present, and accepts only Razorpay keys with the `rzp_test_` prefix.

## Start locally

From the repository root, install the package and run the server:

```powershell
python -m pip install -e .
python scripts/run_commerce_lab.py
```

Open `http://127.0.0.1:8080`. The host and port can be changed with `--host` and `--port`, or with `MANDATEGUARD_PRODUCT_HOST` and `MANDATEGUARD_PRODUCT_PORT`.

The initial page load reads only local static assets and `/api/config`. It never calls OpenAI or Razorpay. A purchase run starts only after an explicit click on `RUN AI BUYER`.

## Demo journeys

- `SAFE PURCHASE` exercises the normal buyer, retrieval, deterministic, semantic, capability, ledger, and execution path. It creates one order through the offline Test Mode-compatible double and reports one adapter call, zero external calls.
- `POLICY VIOLATION` reaches `BLOCK` through the normal frozen controller and reports zero Razorpay calls.
- `AMBIGUOUS EVIDENCE` reaches `REVIEW` through the normal frozen controller and reports zero Razorpay calls.

After an `ALLOW`, use `TEST CAPABILITY REPLAY` to submit the same signed capability again. The D6 nonce ledger rejects it as `NONCE_ALREADY_USED` before another provider call.

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

The product tests cover `ALLOW`, `BLOCK`, `REVIEW`, no trusted evidence, semantic cache `MISS` to `HIT`, replay rejection, duplicate submission idempotency, zero-call claims, secret non-disclosure, and the HTTP boundary.
