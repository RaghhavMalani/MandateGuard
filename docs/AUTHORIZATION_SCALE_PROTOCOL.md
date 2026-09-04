# Synthetic authorization-scale protocol

Status: **frozen before controller execution**.

`SyntheticMerchantUniverse` is a deterministic, seed-bound benchmark namespace.
It is not a real merchant network and it never promotes a historical marketplace
listing into trusted evidence. The evidence-complete base world contains 25,000
unique synthetic SKUs across 500 synthetic merchants. Benchmark mutations are
constructed only inside the synthetic registry.

The scale ladder uses deterministic prefixes of the same world at 1,000, 10,000,
and 25,000 cases. Every descriptor binds its case ID, family, merchant, SKU,
price, currency, evidence version, construction key, and expected safe action.
The freeze records a streaming SHA-256 for every prefix, so the workload can be
regenerated without committing a large case file or using unsafe deserialization.

## Label independence

Expected safe actions come only from the recipes in `CASE_TAXONOMY.json`. The
freeze generator imports no MandateGuard controller, decision, policy, evidence,
or execution module. It is therefore mechanically impossible for the controller
under test to generate its own labels.

The first nine families exercise authorization and evidence resolution. The last
eight exercise binding and execution-gate safety after a capability has been
issued. `BENIGN_ALLOWED` is the only family permitted to reach a local recording
provider adapter. BLOCK and REVIEW cases permit exactly zero provider calls.

## Execution rules

The benchmark must call the existing deterministic authorization path and the
existing signed-capability execution gate. It may add adapters and fixture
construction, but not a second authorization controller. The benchmark clock is
fixed at `2026-09-04T00:00:00Z` and the provider is local and non-networked.

Report total cases, ALLOW/BLOCK/REVIEW distribution, target-invariant agreement,
authorization P50/P95/P99, single-process throughput, capabilities issued,
provider calls before ALLOW, provider calls on BLOCK/REVIEW, the named rejection
counters, and resident memory. Results must identify one process and one machine;
they must not claim distributed scale.

The immutable record is
`data/eval/authorization-scale/WORLD_FREEZE.json`. It contains no controller
outcome or measured performance value. Executed results belong only to a later
commit.
