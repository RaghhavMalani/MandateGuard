# Commerce Lab Deployment

Public deployment of the judge-facing Commerce Lab.

| Item | Value |
| --- | --- |
| Platform | Render (Docker web service, `render.yaml` blueprint) |
| Public URL | _pending first deploy_ |
| Region | Singapore |
| Branch | `feat/judge-facing-product` |
| Mode | OFFLINE DEMO only |
| Startup command | `python scripts/run_commerce_lab.py` |
| Health check | `GET /api/health` |

## Why this platform

The Commerce Lab is a long-lived `ThreadingHTTPServer`. A run is started by
`POST /api/runs`, advanced on a background thread, polled through
`GET /api/runs/{run_id}`, and replay-tested through `POST /api/runs/{run_id}/replay`
against the capability held in that process. A serverless target would freeze the
run thread after each response and route later requests to other instances, so
polling and capability replay would fail. Render runs the existing server directly
from the repository `Dockerfile` with no application rewrite.

## Offline and live behaviour

Public deployment defaults to OFFLINE DEMO and needs no credentials. The image
installs no third-party packages: the offline path is Python standard library
only, and the four judge journeys (SAFE PURCHASE, POLICY VIOLATION,
AMBIGUOUS EVIDENCE, CAPABILITY REPLAY) run with zero external network calls.

Live Test Mode stays opt-in and is unavailable in public deployment. With no
credentials present, `/api/config` reports `modes.live.available: false`, the page
shows `LIVE TEST UNAVAILABLE` with a concise reason, and `POST /api/runs` with
`mode: "live"` is refused with HTTP 503 `MODE_UNAVAILABLE`. Nothing crashes, and
no paid external API can be reached from the public URL.

Live Test Mode is therefore documented as local or controlled only. To run it
locally, install the `openai` package and provide these server-side values in a
local `.env`, never in the deployment image:

```text
OPENAI_API_KEY
MANDATEGUARD_SEMANTIC_MODEL
RAZORPAY_KEY_ID              # rzp_test_ prefix only
RAZORPAY_KEY_SECRET
MANDATEGUARD_EXECUTION_HMAC_KEY   # at least 32 bytes
```

The browser only ever receives availability and validation messages. Credential
values, signed capability material, and Authorization headers are never sent to
frontend JS, API responses, logs, or static files.

## Bind address

`MANDATEGUARD_PRODUCT_HOST` overrides the host when set; otherwise the server
binds `0.0.0.0`. Port precedence is `PORT`, then `MANDATEGUARD_PRODUCT_PORT`,
then `8080`. Render supplies `PORT`, so no port configuration is required.

## Filesystem

The deployment filesystem is ephemeral and no durable persistence is required.
The semantic cache and execution ledger are SQLite files created at startup under
`TMPDIR` (`/var/tmp/mandateguard` in the image), which the container creates and
owns as a non-root user. Losing that state between deployments only clears demo
run history; it does not affect any authorization decision.

## Deploy

The blueprint deploys from the public repository:

1. Open [render.com/deploy](https://render.com) and choose **New > Blueprint**.
2. Connect the `RaghhavMalani/MandateGuard` repository and select the
   `feat/judge-facing-product` branch.
3. Render reads `render.yaml` and creates the `mandateguard-commerce-lab`
   web service. Apply it. No environment variables need to be set.

Free instances sleep after roughly 15 minutes of inactivity, so the first request
after an idle period takes around 50 seconds to wake. For live judging, either
warm the URL shortly beforehand or move the service to a paid instance type.

## Verification

```bash
curl -s https://<public-url>/api/health
```

Then run SAFE PURCHASE, POLICY VIOLATION, AMBIGUOUS EVIDENCE, and
TEST CAPABILITY REPLAY from the page and confirm the reported Razorpay call
counts.
