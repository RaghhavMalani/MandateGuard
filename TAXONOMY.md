# MandateGuard — V1 Threat & Evaluation Taxonomy

**Status:** D1 pre-registration artifact  
**Freeze rule:** this document is committed before detector implementation. Any later change must be a new commit that explicitly explains why the pre-registered taxonomy changed.

## 1. V1 claim

MandateGuard is a **PSP-side consistency enforcement layer for agentic payments**.

It verifies that the **declared mandate**, independently retrievable merchant/payment evidence, proposed transaction state, authorization decision, and eventual Razorpay execution remain mutually consistent before money-moving APIs are called.

MandateGuard V1 does **not** claim to prove that the declared mandate equals the human's private original intent. Full intent-origin assurance requires a trusted upstream intent-capture/attestation layer that is outside V1.

## 2. Protected invariant

> No request may reach the Razorpay executor unless MandateGuard has produced a valid, signed, unexpired `ALLOW` decision whose committed transaction hash matches the exact transaction about to execute.

The detector may use probabilistic judgment only for constraints that cannot be reduced to deterministic comparisons. Model confidence is never treated as authorization evidence.

## 3. Threat model

### Adversary / failure capabilities in scope

A compromised or malfunctioning buyer-side agent may:

- lie consistently in self-reported cart/order fields;
- mutate price, quantity, SKU, merchant, recurrence, or free-text product/fulfillment semantics;
- replay or retry previously observed requests;
- omit or paraphrase information in self-reported fields;
- construct internally consistent but externally false transaction state.

### Trusted V1 components

The attacker is assumed unable to modify:

- the PSP-side nonce ledger;
- the PSP/server clock;
- MandateGuard signing keys;
- committed PSP-side snapshot hashes after commitment;
- the independent merchant catalog fixture/source queried by MandateGuard;
- the Razorpay executor's capability check.

### Explicit non-goals

V1 does not claim to solve:

- authenticity of human intent before a mandate reaches the PSP boundary;
- collusion between a malicious merchant and a compromised buyer agent;
- truthfulness of merchant-authored catalog descriptions themselves;
- account takeover, credential theft, card/UPI fraud, or generic payment fraud;
- long-horizon merchant behavioural anomaly detection;
- autonomous self-modification of authorization policy.

## 4. Evidence-independence tiers

The key question for every check is:

> **Could a compromised agent satisfy this check simply by lying consistently?**

### Tier A — independently verifiable

Checked against state the buyer agent does not control. A consistent lie can still be contradicted by the independent source.

| ID | Check | Independent source | V1 guarantee |
|---|---|---|---|
| A1 | Declared line price vs catalog price | Merchant catalog source/fixture | Detects agent-side price divergence from catalog state |
| A2 | SKU existence and merchant ownership | Merchant catalog source/fixture | Detects nonexistent or merchant-mismatched SKU declarations |
| A3 | Merchant identity consistency | Merchant/catalog mapping | Detects merchant substitution relative to independently retrieved ownership |
| A4 | Nonce replay | PSP nonce ledger | Detects reuse of a consumed authorization nonce |
| A5 | Mandate expiry | PSP/server clock | Detects execution after the mandate validity window |
| A6 | Snapshot mutation | PSP-side committed hash | Detects mutation after a transaction/evidence snapshot was committed |

**Interpretation:** Tier A is the strongest V1 evidence surface. It does not require the buyer agent to be truthful.

### Tier B — self-reported, internally checkable

These checks validate consistency of fields supplied within the transaction state. A compromised agent that lies coherently may pass them.

| ID | Check | Example |
|---|---|---|
| B1 | Line-sum consistency | sum(line totals) == declared order total |
| B2 | Quantity consistency | declared quantity matches line-item arithmetic |
| B3 | Currency consistency | mandate/cart/order use the same declared currency |
| B4 | Recurrence-field consistency | recurring flags agree across self-reported structures |
| B5 | Canonical transaction hash recomputation | serialized transaction matches its own declared commitment |

**Interpretation:** Tier B proves internal consistency, not external truth.

### Tier C — semantic judgment

Invoked only when **all Tier A/B checks pass** and the mandate contains at least one constraint that cannot be reduced to a deterministic field comparison.

Examples include:

- a free-text product obligation that semantically implies recurrence despite a `recurring=false` field;
- a product description that violates an exclusion expressed via paraphrase/synonym;
- suitability or compatibility constraints not represented in structured catalog fields;
- free-text fulfillment terms that conflict with a semantic requirement.

Tier C returns a categorical verdict with an explicit abstention path. Raw model confidence is recorded for diagnostics only and is not used as authorization evidence in V1.

## 5. Enforcement precedence

1. **Tier A violation → BLOCK**
2. **Tier A passes; Tier B violation → BLOCK**
3. **Tier A/B pass; no semantic constraints → ALLOW**
4. **Tier A/B pass; semantic constraints exist → Tier C invoked**
5. **Tier C PASS → ALLOW**
6. **Tier C VIOLATION → BLOCK**
7. **Tier C ABSTAIN → REVIEW**

No LLM call occurs before deterministic checks have passed.

## 6. Pre-registered Tier C family split

Generalization claims apply **only to Tier C**. Holding out deterministic checks such as expiry or replay would not demonstrate learning/generalization because those checks are schema-specified invariants.

### Development families — detector may be developed against these

- `C-DEV-RECURRENCE`: free-text terms imply a recurring economic obligation contrary to a one-time mandate.
- `C-DEV-EXCLUSION`: product/terms semantically violate an explicit exclusion through paraphrase, synonym, or indirect wording.
- `C-DEV-PURPOSE`: product is superficially category-compatible but semantically unsuitable for the stated purpose/use.

### Held-out families — detector implementation must not be developed against these

These families are executed only after detector freeze.

- `C-HOLD-BUNDLE`: a composite bundle contains a semantically disallowed component that is not exposed by structured fields.
- `C-HOLD-COMPATIBILITY`: an item matches structured category/price constraints but is semantically incompatible with the requested device/context.
- `C-HOLD-FULFILLMENT`: free-text fulfillment/service terms conflict with a semantic requirement when no structured field independently exposes the conflict.

If any held-out family is inspected or used for detector tuning before the freeze, it loses held-out status and must be reported as such.

## 7. Benchmark provenance and scale

### Tier A/B

Target: approximately **1,000 auto-labelled cases**. Labels are mechanically derived from deterministic invariants and reported separately from Tier C.

### Tier C

Target: **240 violation cases + 200 benign cases**. Every Tier C label is human-adjudicated and recorded before the case is first run through the frozen detector.

Allowed provenance labels:

- `developer_authored`
- `external_defensive_corpus_adapted`
- `separate_model_adversarial`

`separate_model_adversarial` means authored by a separate model that has no access to detector implementation or development cases. It is **not** described as blind evaluation.

No reusable offensive prompt-injection payload collection is committed to the public repository. Defensive fixtures may store redacted identifiers/hashes and structured mutation metadata.

## 8. Evaluation claims and metrics

### Tier A/B

Report invariant-level pass/fail coverage and correctness. Near-100% correctness is expected and is not presented as an ML result.

### Tier C

Report at minimum:

- precision;
- recall;
- benign false-positive rate;
- per-family results;
- development-family vs held-out-family results;
- abstention/review rate;
- model-touch fraction;
- p95 latency for deterministic-only path;
- p95 latency for semantic path.

Raw model confidence receives a reliability diagram/ECE diagnostic on development data only. V1 does not calibrate or operationalize model confidence.

### Economic reporting

Separately report:

- false-positive blocked-GMV cost;
- false-negative unauthorized-value cost;
- REVIEW friction/delay cost;
- break-even risk thresholds implied by the chosen cost assumptions.

The economic model may analyze or justify action policy, but V1 must not pretend an uncalibrated LLM confidence is a valid posterior probability.

## 9. Detector freeze and post-freeze robustness probe

**Detector freeze:** end of D9. No detector or decision-rule changes after this point.

D10 executes held-out Tier C families for the first time.

After the main evaluation, a structured minimal-mutation robustness probe may search for the smallest semantic/economic transaction mutation that causes an unauthorized case to remain `ALLOW`.

Any evasions found after freeze are **reported, not patched**. The point of the probe is to expose residual failure modes without contaminating the frozen evaluation.

## 10. Replay requirement

Every deterministic scenario must be replayable from a seed with a byte-identical decision/event log, excluding explicitly documented non-deterministic external values. Model calls are isolated from the deterministic replay guarantee and are recorded by immutable input/output hashes for audit.

## 11. AI-use principle

> Evidence gathering is deterministic because the evidence sources expose fixed typed interfaces. MandateGuard uses a model only where semantic judgment cannot be reduced to an invariant.

This is intentional architecture, not a missing agent framework.
