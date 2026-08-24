# D7 — Deterministic Tier A/B benchmark corpus generation

**Status:** generated, labelled, hashed, and **executed once** on
2026-08-24. This document describes the generation milestone; the first
registered execution is recorded separately in
`benchmark/results/tier_ab/FIRST_RUN_SUMMARY.json`.

D7 is corpus *generation* only. It creates the 1,008 preregistered
deterministic Tier A/B cases fixed by `benchmark/PROTOCOL.md` §2.1, records a
mechanical ground-truth label for each one, and hashes the case content. The
registered corpus has **not** been run through `evaluate_tier_a`,
`evaluate_tier_b`, `authorize_transaction`, `finalize_authorization`, or the
semantic verifier at generation time, and every generated case carried
`first_run_at: null`. Those values were recorded once, at the first registered
execution, without moving a single `case_content_sha256`.

The claim this artifact supports, and no more:

> MandateGuard pre-generated and mechanically labelled 1,008 deterministic
> Tier A/B benchmark cases from frozen invariant definitions before executing
> the registered corpus through the detector.

No correctness, accuracy, pass-rate, or detector-behavior claim is made here,
because the corpus has not been executed.

## Exact allocation

| Tier | Families | Violation | Benign evaluable | Benign evidence-unavailable | Per family | Tier total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A (`A1`–`A8`) | 8 | 24 | 24 | 8 | 56 | 448 |
| B (`B1`–`B10`) | 10 | 28 | 28 | — | 56 | 560 |
| **Total** | **18** | **472** | **472** | **64** | **56** | **1,008** |

Registered label triples, fixed by the case class and never by a detector
result:

| Class | `ground_truth` | `expected_action` | `target_expectation.status` |
| --- | --- | --- | --- |
| `V` | `violation` | `BLOCK` | `FAIL` |
| `P` | `benign` | `ALLOW` | `PASS` |
| `NE` | `benign` | `REVIEW` | `NOT_EVALUABLE` |

There are no Tier C cases, no C-DEV or C-HOLD content, no semantic benchmark
examples, and no held-out source material anywhere in this corpus. No LLM was
called at any point in generation.

## Labels come from registered construction recipes

The direction of dependency is fixed:

```
registered mutation recipe
    -> mechanical ground-truth label
    -> case content
    -> case_content_sha256
    -> (later, after hostile review) detector execution
```

A recipe determines the label. For example, the `A4` recipes are: a consumed
mandate nonce is a `violation` / `BLOCK` / `FAIL`; a fresh nonce is `benign` /
`ALLOW` / `PASS`; an absent nonce ledger is `benign` / `REVIEW` /
`NOT_EVALUABLE`. No detector call establishes any of those.

Nothing in `src/mandateguard/benchmark/` or
`scripts/generate_tier_ab_benchmark.py` imports `mandateguard.policy`,
`mandateguard.semantic`, `mandateguard.execution`, or `mandateguard.replay`.
That is enforced both statically and by a subprocess check that the generator
leaves no such module in `sys.modules`
(`tests/test_benchmark_tier_ab_generation.py`).

## Case-content hash projection

`case_content_sha256` is the SHA-256 of MandateGuard canonical JSON (UTF-8,
sorted keys, no floats, no insignificant whitespace) over exactly this
projection and nothing else:

```
{
  "case_schema_version",
  "evidence_tier",
  "family_id",
  "provenance",
  "split",
  "ground_truth",
  "label_source",
  "expected_action",
  "target_expectation": {"family_id", "status"},
  "evaluation_inputs": {
    "mandate", "transaction", "catalog_snapshot", "server_time",
    "nonce_state", "psp_committed_hashes", "replay_seed", "evaluated_at"
  }
}
```

Deliberately **excluded** as audit-only metadata: `case_id`,
`case_content_sha256` itself, `label_recorded_at`, `first_run_at`, and the
whole `generator` block. Excluding `case_id` means identical benchmark content
keeps one digest even if someone re-labels it with a different identifier.

Any change to an evaluation input, the family, the evidence tier, the
provenance, the split, the ground truth, the label source, the expected
action, or the target expectation changes the digest. Changing
`label_recorded_at` does not.

`label_recorded_at` records when the labels were materialized. It is injected
explicitly on the command line; the generator never calls `datetime.now()`.

## Storage layout

```
benchmark/cases/tier_ab/A1.jsonl  ... A8.jsonl    # 56 records each
benchmark/cases/tier_ab/B1.jsonl  ... B10.jsonl   # 56 records each
benchmark/MANIFEST.yaml                           # 1,008 metadata records
benchmark/generated/TIER_AB_GENERATION_SUMMARY.json
```

18 files, 1,008 records, one canonical JSON object per line, UTF-8, LF, exactly
one trailing newline per file.

Two orderings are in force, and both are tested:

- **JSONL corpus files** are sorted by ascending `case_id` within each family
  file. Lexicographic order puts `NE` before `P` before `V`.
- **`benchmark/MANIFEST.yaml`** is ordered Tier A before Tier B, then family
  numeric order (`A1`–`A8`, `B1`–`B10`), then case class `V`, `P`, `NE`, then
  ascending numeric index.

Every manifest entry corresponds to exactly one JSONL record and vice versa;
the shared metadata fields must match exactly. Duplicate `case_id` values and
duplicate `case_content_sha256` values both make generation fail.

`benchmark/MANIFEST.yaml` changed only in its `cases` list. The generator
verifies the SHA-256 of the frozen preamble — `schema_version`,
`artifact_status`, `case_schema`, `enums`, `field_rules`, `hash_policy`,
`freeze_policy` — before it writes anything, and refuses to run if it has
drifted.

## Reproduction

Generator version: **`d7-tier-ab/1.0.0`**.

```bash
python scripts/generate_tier_ab_benchmark.py --label-recorded-at 2026-08-23T00:00:00Z
```

The command is a pure function of the generator version, the registered
recipes, and that explicit timestamp. It uses no ambient randomness, no wall
clock, no `uuid4`, no `secrets`, no filesystem ordering, and no
process-randomized `hash()`. All derived identifiers and parameters come from
SHA-256 over the recipe key `"{generator_version}|{family_id}|{case_class}|{index}"`,
or directly from the index. Each case records its `generator_version`,
`generator_seed`, `recipe_id`, and `recipe_parameters`, so it is reproducible
on its own. Two runs into separate directories are byte-identical.

Everything is generated and validated in memory first; files are written to a
sibling temporary file and atomically replaced, so a failed run does not leave
a partial corpus.

## Baseline fixture and single-fault isolation

Each case starts from a fully evidenced baseline in which every Tier A and
Tier B invariant holds by construction: catalog SKU, merchant, price, and
currency agree; declared line arithmetic and the order total reconcile; the
declared aggregate quantity matches; the mandate is unexpired at the injected
server time; the nonce is fresh; both PSP commitments match; the declared
transaction hash is correct; ceilings and allowlists pass; recurrence is
consistent and permitted as appropriate; and the mandate carries
`semantic = ()`. Exactly one registered family-specific mutation is then
applied. Parameters — merchants, SKUs, unit prices, quantities, line counts,
ceilings, currencies, timestamps, nonces, allowlist presence — vary with the
recipe index, so no two cases differ only by identifier.

Where the frozen invariant structure makes a second check react, that reality
is retained rather than disguised. Trusted typed state is never distorted into
an impossible shape to manufacture apparent isolation, and no additional real
finding will be suppressed when execution eventually occurs.

### Known invariant compositions in this corpus

Recorded here at generation time, before any execution:

| Recipe | Composition |
| --- | --- |
| `A2.violation.sku_absent_from_catalog` (12 cases) | A2 `FAIL`; A1, A7, A8 become `NOT_EVALUABLE` because the SKU has no authoritative price or recurrence state. |
| `A3.violation.snapshot_merchant_mismatch_with_ownership` (12 cases) | A3 `FAIL` and A2 `FAIL`: when a real foreign snapshot owns its own items, the ownership check reacts too. The other 12 A3 violations isolate A3 by leaving item ownership with the declared merchant. |
| `A6.violation.catalog_commitment_mismatch` (12 cases) | A6 owns the only finding; A1, A2, A3, A7, A8 become `NOT_EVALUABLE` as the frozen taxonomy defines. They are not converted into extra findings. |
| `A6.violation.transaction_commitment_mismatch` (12 cases) | A6 `FAIL`; A7 becomes `NOT_EVALUABLE`. |
| `A7.violation.catalog_total_above_mandate_ceiling` (24 cases) | A7 `FAIL` composes with B6 `FAIL` by construction. See the note below. |
| `B6.violation.declared_total_above_mandate_ceiling` (28 cases) | B6 `FAIL` composes with A7 `FAIL`, the mirror of the above. |
| `B3.violation.order_currency_mismatch` (14 cases) | B3 `FAIL`; A1 becomes `NOT_EVALUABLE` because authoritative prices are unavailable in the declared order currency. The other 14 B3 violations mutate `cart_currency` instead and isolate B3 completely. |
| `A1.unavailable.catalog_currency_unavailable` (4 cases) | A1 and A7 both `NOT_EVALUABLE`. |
| `A7.unavailable.transaction_commitment_absent` (4 cases) | A7 and A6 both `NOT_EVALUABLE`. |
| All `*.unavailable.catalog_snapshot_absent` / `catalog_commitment_absent` | Every catalog-dependent Tier A check becomes `NOT_EVALUABLE`. No check is `FAIL`. |

**A7's second limb is not independently reachable.** A7 requires both that the
catalog-derived total equals the declared charge *and* that it stays within the
mandate ceiling. Breaking the equality limb is impossible while A1 and B1 both
hold: if every declared unit price equals its catalog price (A1) and every
declared line total is `price × quantity` summing to the declared order total
(B1), then the catalog-derived total is *forced* to equal the declared total.
The registered A7 violation therefore uses the ceiling limb, which necessarily
composes with B6. This is a property of the frozen policy, discovered while
designing the recipes, and is recorded rather than engineered around.

**A1 violations do not disturb A7 or B1.** Each A1 violation mutates a pair of
declared unit prices that share a quantity, adding `+delta` to one and
`-delta` to the other. Declared per-line arithmetic stays valid, the declared
order total stays equal to the catalog-derived total, and A1 still observes two
exact per-SKU price mismatches. No catalog evidence is faked: the catalog keeps
the true prices.

The remaining recipes — A1 violations, A2 ownership violations, isolated A3
violations, all of A4, A5, A8, B1, B2, B4, B5, B7, B8, B9, B10, and every
benign case — introduce no second finding and no additional `NOT_EVALUABLE`
check.

## Lifecycle after generation

The registered corpus was executed once, on 2026-08-24, from the harness
committed at `827462cca6c163bbb45b7623521fe111d9ffc416`. That run recorded
`first_run_at` in both registered mirrors — the JSONL corpus files and
`benchmark/MANIFEST.yaml` — which is the transition the manifest preregisters
in its `field_rules`:

> `first_run_at`: Null until first detector execution, then immutable.

`first_run_at` is excluded from `case_content_sha256` by the manifest hash
policy, by PROTOCOL §6, and by the codec's content projection, so recording it
cannot move a digest. All 1,008 digests are unchanged, and the generator
remains a pure function: regenerating the corpus reproduces every committed
byte except that one audit-only field.

`benchmark/generated/TIER_AB_GENERATION_SUMMARY.json` is **generation** audit
metadata and is deliberately left byte-immutable. Its `corpus_file_sha256`
values are the pre-execution digests, and its `registered_corpus_executed` and
`first_run_null_count` fields describe the moment of generation. The
post-execution facts that supersede them are recorded in
`benchmark/results/tier_ab/FIRST_RUN_SUMMARY.json`, which carries both
`pre_execution_corpus_sha256` and `post_first_run_metadata_corpus_sha256` so
the two states are distinguishable rather than conflated.

## Next step

Tier C corpus construction and held-out handling, per `benchmark/PROTOCOL.md`.
Detector freeze remains scheduled for the end of D9; the Tier A/B corpus is
closed and its labels may not be revised in light of the first-run result.
