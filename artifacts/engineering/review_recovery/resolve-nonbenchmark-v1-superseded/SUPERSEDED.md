# Superseded Resolve evaluation record

These outcomes are kept as an unaltered historical record. **They do not
describe the current MandateGuard Resolve configuration and must not be cited
as current results.**

They were produced under a configuration that has since been replaced:

- The runner injected an evaluator-only `top_k=2`, so the initial `REVIEW` in
  every case came from suppressed retrieval rather than a genuine evidence gap.
  The replacement evaluator is now configured to run the product default evidence policy
  (`MANDATEGUARD_PRODUCT_EVIDENCE_POLICY_V1`) and refuses to score a case whose
  trust configuration differs from the product's.
- The cases were `RR-ALLOW-STUDYGLOW`, `RR-BLOCK-MARKET-EDGE`, and
  `RR-REVIEW-FLEXI`. Two of those merchants reach `ALLOW` and `BLOCK` on their
  first evaluation under the product default policy, so they cannot begin at
  `REVIEW` without an override. The current draft scaffold uses `RR-ALLOW-AURORA`,
  `RR-BLOCK-SIGNAL-EDGE`, and `RR-REVIEW-FLEXI`, whose registered evidence is
  legitimately insufficient at the product default policy.
- The summary reported `planner_direct_unsafe_allow_count`, a name the runner
  never emitted. Metric names are now fixed by the versioned schema
  `RESOLVE_METRIC_SCHEMA_V2`, and an unknown or missing name refuses the run.
- External-call totals were derived partly from run mode. They are now observed
  from the adapters themselves.

The replacement run has not been executed. Its three-case scaffold is explicitly
`DRAFT_PRE_EVALUATION`; it is neither the expanded 20-case manifest nor Commit A.
After expansion and review, a later commit may freeze the manifest and become
Commit A. Only a subsequent offline execution may produce a separately committed
results artifact.
