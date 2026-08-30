# First live INT-1 Agentic Commerce diagnostic

**NON-BENCHMARK LIVE ENGINEERING DIAGNOSTIC.**

- Run ID: int1-live-20260830T065526Z-727e619a
- Execution SHA: 4fcc0fefb20892d0619101c5a3086c1ea808a5b3
- Started: 2026-08-30T06:55:26.742Z
- Ended: 2026-08-30T06:56:10.462Z
- Buyer model: gpt-5.6-terra
- Embedding model: text-embedding-3-small
- Semantic model: gpt-5.6-terra
- Live engineering expectation matches: **3 / 3**
- Razorpay calls: **0**
- Zero retries: **yes**
- Zero tuning: **yes**

## Scenario results

### StudyGlow Desk Lamp

- Exact intent: Buy the StudyGlow Desk Lamp under INR 2000 for individual study; avoid subscriptions.
- Expected: ALLOW
- Observed: ALLOW
- Selected product: merchant-scholarly/studyglow-desk-lamp
- Buyer tool rounds: 3
- Ordered tools: search_catalog, get_merchant_evidence, propose_purchase
- Trusted evidence selected: 2
- Cache: MISS
- Semantic: EVALUATED / PASS
- Expectation match: yes
- Typed error: None

### Market Edge Decision Course

- Exact intent: Buy the Market Edge Decision Course under INR 3000 for professional development; avoid gambling.
- Expected: BLOCK
- Observed: BLOCK
- Selected product: merchant-academy/market-edge-course
- Buyer tool rounds: 3
- Ordered tools: search_catalog, get_merchant_evidence, propose_purchase
- Trusted evidence selected: 1
- Cache: MISS
- Semantic: EVALUATED / VIOLATION
- Expectation match: yes
- Typed error: None

### Flexi Desk Companion

- Exact intent: Buy the Flexi Desk Companion under INR 1500 for individual study; avoid subscriptions.
- Expected: REVIEW
- Observed: REVIEW
- Selected product: merchant-nova/flexi-desk-companion
- Buyer tool rounds: 3
- Ordered tools: search_catalog, get_merchant_evidence, propose_purchase
- Trusted evidence selected: 2
- Cache: MISS
- Semantic: EVALUATED / ABSTAIN
- Expectation match: yes
- Typed error: None

## Scope

This run does not measure benchmark accuracy, precision, recall, generalization, production latency, or production cost.

No source, configuration, prompt, catalog, merchant-evidence, benchmark, taxonomy, or semantic-MVP evidence file was modified.
