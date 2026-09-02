# MandateGuard Resolve

## Security boundary

Resolve is a bounded evidence-acquisition layer for an existing `REVIEW`. It
does not authorize, block, mutate, or reinterpret a decision. A successful
acquisition creates a new canonical evidence set and invokes the complete
existing authorization controller again. Only that new controller result can
reach capability issuance and execution.

Candidate source IDs, identity bindings, evidence kinds, and pinned content
hashes are server configuration. The acquisition API accepts no URL, source
identifier, evidence text, or buyer-selected trust input.

The fixed operational limits are two acquisition rounds and four new evidence
items per review. A round always terminates.

## INT-3 boundary decision

The frozen INT-3 model is not integrated. Its target is single-execution action
stability over 62 correlated subsets from six synthetic queries; it is not an
evidence-correctness or safety model. Using it at runtime would support claims
beyond that evaluation. Resolve therefore uses deterministic constraint-family
gap mapping and the server-side trusted-source registry. Neither planner can
emit `ALLOW` or `BLOCK`.

## Engineering evaluation

The non-benchmark plan is frozen in
`fixtures/engineering/review_recovery/evaluation_plan.json` before recovery
outcomes are executed. It names three synthetic product cases across purpose,
recurrence, and exclusion constraints, and labels failure injections as
correlated robustness checks rather than independent commerce cases. The run is
offline and permits zero OpenAI, Razorpay, or network calls.
