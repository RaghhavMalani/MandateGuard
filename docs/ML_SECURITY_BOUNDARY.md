# The ML / authorization boundary

> **ML understands the commerce universe.
> MandateGuard's deterministic gate controls money.**

---

## The rule

Everything reachable from `mandateguard.discovery` is **advisory**.

**ML may:**

| Capability | Where |
| --- | --- |
| `RETRIEVE` | BM25 + frozen LSA over 17,702 listings |
| `RANK` | hybrid scoring, near-duplicate suppression |
| `CLASSIFY` | 22-class product category classifier |
| `DETECT_ANOMALY` | 11-feature proposal analytics |
| `SUGGEST_EVIDENCE_GAP` | transactability diagnostic |
| `EXPLAIN` | plain-English match and signal rationales |

**ML may not, at any confidence:**

| Forbidden | Why it would be catastrophic |
| --- | --- |
| `ISSUE_EXECUTION_CAPABILITY` | A capability is the only thing that reaches Razorpay |
| `OVERRIDE_DETERMINISTIC_BLOCK` | `BLOCK` means a mandate constraint was violated |
| `SATISFY_MISSING_TRUSTED_EVIDENCE` | A model's opinion is not a merchant's statement |
| `OVERRIDE_REVOCATION` | Withdrawn consent must stay withdrawn |
| `OVERRIDE_REQUEST_BINDING` | Breaks the guarantee that the executed amount is the authorized one |
| `AUTHORIZE_PAYMENT` | The whole point |

---

## How the rule is enforced, not just stated

### 1. The lists are code

[`src/mandateguard/discovery/trust.py`](../src/mandateguard/discovery/trust.py)
exists only to refuse. `assert_advisory_only()` raises on anything in the
forbidden list *and* on anything unregistered — an unknown capability is refused
rather than assumed safe.

### 2. Advisory outputs raise rather than return

```python
signal = prediction.as_signal()
signal.authorize()          # TrustBoundaryViolation
```

`AdvisorySignal.authorize()` raises instead of returning a falsy value, so a
caller who routes a model score into an authorization decision fails loudly at
the call site rather than quietly reading zero. Every advisory payload —
classifier prediction, mismatch signal, anomaly assessment, transactability
report — carries `authorization_authority: "NONE"`.

### 3. The import graph is asserted

`tests/test_discovery_trust_boundary.py` parses every module under
`mandateguard/discovery/` and fails if any of them imports
`mandateguard.execution`, `.policy`, `.semantic`, `.recovery`, or `.replay`. The
discovery layer cannot reach the money path because it cannot *name* the money
path.

### 4. One narrow channel, carrying no evidence text

Discovery needs to know whether a listing has merchant evidence. It gets that
through `TrustedListingFacts`, which carries **counts and identities only**:

```python
evidence_count: int
merchant_of_record: str | None
recurrence_evidenced: bool
category_declared_by_merchant: bool
```

A test asserts exactly those four fields and no others. Merchant evidence text
never crosses into the discovery layer, because a surface that can quote it is a
surface someone will eventually treat as having produced it.

### 5. A perfect diagnostic score is still not an authorization

`assess_listing()` can return `EVIDENCE READY` with all six checks resolved. Its
own `next_action` then reads:

> Everything a payment needs is known. **The authorization controller decides
> separately** whether it permits this transaction.

There is a test named
`test_a_perfect_transactability_score_is_still_not_an_authorization`.

---

## What a discovery-only listing can reach

```
DISCOVERED  →  MATCHED  →  EVIDENCE_INCOMPLETE  →  REVIEW_REQUIRED
```

`REVIEW_REQUIRED` is terminal, and it is a **product feature**. Search the
catalog for headphones, pick a crawled listing, and the product says:

> This listing was discovered and matched, and no merchant has published
> authoritative terms for it. MandateGuard will not manufacture an `ALLOW` for a
> product nobody has vouched for, so the journey ends here with zero
> payment-provider calls.

The alternative — inventing an `ALLOW` because a model was confident — is the
failure this system exists to prevent.

A listing **with** registered merchant evidence stops at `MATCHED` and can be
handed to the controller. Handing it over is not approval: the controller still
returns `ALLOW`, `BLOCK`, or `REVIEW` on its own terms, and does not know or
care that discovery was involved.

---

## What did not change

The money-moving controller is untouched by this work:

* Tier A/B deterministic checks — unchanged
* Semantic verifier and its evidence binding — unchanged
* Capability issuance, signing, and the single-use nonce ledger — unchanged
* Mandate state registry and revocation — unchanged
* Bounded trusted-evidence recovery — unchanged

712 pre-existing tests covering that controller pass unmodified. The discovery
layer is additive, and it is additive on the *outside* of the boundary.

---

## Where each ML component is allowed to matter

| Component | Effect it is allowed to have | Effect it can never have |
| --- | --- | --- |
| BM25 + LSA retrieval | which listings a user sees | which listing may be paid for |
| Category classifier | routing, and disagreement detection | a category claim the controller trusts |
| Mismatch signal | investigation priority, `REVIEW` | overturning `BLOCK`, supplying evidence |
| Anomaly analytics | ordering what a reviewer looks at first | a threshold that permits a payment |
| Transactability | naming what is missing | declaring a purchase safe |

The one place a trained model does something the deterministic layer
demonstrably cannot is category laundering — ROC AUC 0.4519 without the
classifier, 0.9647 with it, on the frozen non-circular evaluation. Even there,
the effect is `SURFACE_REVIEW`. It moves a human's attention, not money.
