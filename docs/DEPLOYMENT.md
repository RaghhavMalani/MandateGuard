# Commerce Lab Deployment

Public deployment of the judge-facing Commerce Lab.

| Item | Value |
| --- | --- |
| Public URL | <https://mandateguard-commerce-lab.onrender.com> |
| Platform | Render (Docker web service, `render.yaml` blueprint) |
| Region | Singapore |
| Deployed branch | `feat/judge-facing-product` |
| Verified deployment commit | `81243f5` |
| Startup command | `python scripts/run_commerce_lab.py` |
| Health endpoint | `GET /api/health` |
| Default mode | OFFLINE DEMO |
| Live Test Mode | Intentionally disabled in public deployment |

`81243f5` is the commit whose running image was verified end to end against the
public URL. Later documentation-only commits trigger a rebuild but do not change
any runtime file, so the served application stays byte-identical to that commit.

## Verified against the public URL

All four judge journeys were confirmed on the deployment, and the only host
contacted during the whole verification was the deployment itself. Zero OpenAI
and zero Razorpay requests were made.

| Journey | Result | Razorpay calls | External network calls |
| --- | --- | --- | --- |
| SAFE PURCHASE | `ALLOW`, simulated offline receipt | 1 adapter call to the offline double | 0 |
| POLICY VIOLATION | `BLOCK` before execution | 0 | 0 |
| AMBIGUOUS EVIDENCE | `REVIEW`, refused to guess | 0 | 0 |
| CAPABILITY REPLAY | `REJECTED_BEFORE_NETWORK`, `NONCE_ALREADY_USED` | 0 additional | 0 additional |

Deployed screenshots are in [screenshots/deployed](screenshots/deployed).

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

That remains true after the discovery catalog was added. The 17,702-listing
search, its BM25 and embedding indexes, and the category classifier all run on
the standard library, because the models are trained offline and frozen into
binary artifacts the runtime only reads.

## Discovery catalog impact on the image

| | Before | After |
| --- | ---: | ---: |
| Packages installed in the runtime image | 0 | **0** |
| Build context | 1.52 MB | 13.46 MB |
| Added by frozen artifacts | — | 11.94 MB |
| Server cold start | ~0 s | **+0.26 s** |
| External calls on page load | 0 | **0** |

The added bytes are two files under `data/processed/` (4.71 MB) and three under
`data/models/` (7.23 MB, with the two human-readable reports excluded from the
image by `.dockerignore`).

scikit-learn and NumPy are needed only to *produce* those artifacts
(`requirements-train.txt`) and are deliberately absent from the image. That is
enforced rather than documented:
`tests/test_runtime_has_no_third_party_dependencies.py` parses every served
module and fails if one imports a training dependency, and imports each served
module in a subprocess to confirm nothing pulls one in transitively.

If the artifacts are absent from a build, the server still starts and every
authorization journey still works. `/api/config` then reports
`discovery.available: false` with the reason, and the interface routes the
intent straight to the authorization controller over the registered merchant
catalog.

**Image size was not measured directly.** The Docker daemon was unavailable in
the environment where this change was built, so the figures above are build
context bytes rather than a `docker images` reading. The layer count and the
installed-package count are unchanged.

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

The semantic cache, execution ledger, mandate state
(`mandate-state.sqlite3`), and recovery audit are SQLite files. Set
`MANDATEGUARD_STATE_DIR` to a writable directory to keep all four together;
reopening the service with the same directory preserves them on the same
filesystem. If the variable is absent, the service creates a temporary state
directory under `TMPDIR` (`/var/tmp/mandateguard` in the image).

The current public Render free-service blueprint does not attach a persistent
disk or set `MANDATEGUARD_STATE_DIR`, so its state is intentionally ephemeral
and no restart-durability claim is made. A future Render configuration may claim
restart persistence only if it attaches suitable persistent storage and points
`MANDATEGUARD_STATE_DIR` at that mounted path. Losing current demo state clears
cache, nonce-ledger, mandate-state, and audit history; it does not widen the authorization
controller, but old in-memory run IDs and their recovery history are unavailable.

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
curl -s https://mandateguard-commerce-lab.onrender.com/api/health
```

Then run SAFE PURCHASE, POLICY VIOLATION, AMBIGUOUS EVIDENCE, and
TEST CAPABILITY REPLAY from the page and confirm the reported Razorpay call
counts.
