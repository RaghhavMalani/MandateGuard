# MandateGuard Benchmark Pre-registration Protocol

**Status:** Pre-registered before benchmark case authoring and detector evaluation

**Repository baseline:** `012a31e5bf1d63fe1fe9b74b9337a3a24f72a4ff`

**Amendment in force:** A1, amending protocol commit `af6ba1bc8b65fb7d47508aff06fc2ecb421a3550`

**Scope:** V1 benchmark sampling, adjudication, execution, analysis, and reporting

## 0. Protocol amendment record

### 0.1 Amendment A1

**This amendment was made before any benchmark case content of any kind exists, and before any detector benchmark execution of any kind.**

At the moment of this amendment:

- `benchmark/MANIFEST.yaml` still contained `cases: []`;
- no Tier A or Tier B benchmark case had been generated, and no generator or fixture for them existed;
- no Tier C benchmark content existed;
- no held-out content existed, and no held-out source material had been selected, inspected, ranked, or drafted from;
- the detector had never been executed against any benchmark case, in any tier, in any split; and
- no benchmark metric, score, count, or partial result of any kind had been observed.

This is therefore still pre-case-generation. Nothing in this amendment can have been motivated by a benchmark result, because no benchmark result exists.

**Why the amendment was made.** The initial protocol commit was subjected to a hostile review whose goal was to find ways to satisfy the letter of the preregistration while still producing a flattering benchmark. The initial protocol was not flawless. The review identified real loopholes: procedures stated as requirements that nonetheless left the ordering, the sampling, or the interpretation open enough for a motivated evaluator to move them after seeing data. Those loopholes are closed here, before any data exists that could make a convenient closure attractive.

The changes are:

- **Close the held-out batch adaptation loophole.** The original ordering admitted a per-case reading under which held-out cases could be authored and executed one at a time, letting later held-out cases be shaped by earlier held-out results. All 220 held-out cases must now be complete before the first held-out execution, and the set closes at first execution (§7.1, §7.2).
- **Fix Tier C split encoding.** `split` is now a pure function of the `family_id` prefix, so a benign held-out case cannot be moved out of the held-out split by labelling it `benign_control` (§2.4).
- **Remove second-review selection discretion.** Discretionary "stratified 25%" sampling is replaced by a deterministic, hash-ordered, per-stratum selection that an auditor can recompute and an evaluator cannot re-draw (§5.1).
- **Extend held-out isolation to source material.** Isolation now covers the specific external source passages themselves, not only the finished held-out case text, since reading the future held-out sources is contamination even when no case file is ever written (§3.1).
- **Make the model-selection tie-break deterministic.** The ambiguous fifth criterion "lower semantic-path latency/cost" is replaced by an exact five-level lexicographic ordering over development-only quantities, plus a deterministic identifier tie-break (§8).
- **Prevent quota filling by duplicates.** Registered case counts are a floor on distinct scenarios, not a licence to restate one scenario 40 times; a duplicate and near-duplicate review is now required before first execution (§4.1).
- **Make provenance origin immutable.** Provenance records where a case came from, not who last edited it, so the registered 40/30/30 provenance mix cannot be satisfied by relabelling edited cases `developer_authored` (§3.1.1).

Supporting clarifications adopted at the same time: the held-out batch-finalization checkpoint (§7.2), the ambiguity/exclusion/replacement audit trail (§5.2), the development model-selection ledger (§8.1), slice-local metric denominators (§11.1), Tier A `NOT_EVALUABLE` reporting (§11.2), economic-analysis containment (§13.1), and fixed latency measurement boundaries (§15.1).

**Scope of the amendment.** Amendment A1 modifies `benchmark/PROTOCOL.md` only. It creates no benchmark case, authors no case content, generates no fixture, implements no machinery, and runs no evaluation. `TAXONOMY.md`, `benchmark/MANIFEST.yaml`, detector code, and the frozen D1-D6 work are unchanged by it. Several requirements below are deliberately preregistered without implementation; each says so where it applies.

**Amendment discipline.** A protocol amendment that tightens preregistration is permitted only while no benchmark result for the affected split is observable. After first detector execution on a split, that split's rules are closed, and any subsequent change is reported as a post-hoc deviation rather than as preregistration.

## 1. Purpose and authority

This protocol fixes the following before benchmark execution:

- benchmark size;
- family allocation;
- provenance allocation;
- human adjudication rules;
- held-out handling;
- metric definitions;
- confidence interval method;
- abstention accounting;
- model-selection rules;
- detector-freeze rules;
- case hashing and immutability; and
- post-freeze mutation-probe rules.

The objective is to prevent benchmark metrics, splits, case definitions, or success criteria from moving after detector results are observed. This document pre-registers procedure; it contains no benchmark cases and does not authorize case generation or Tier C model execution as part of this change.

Three repository artifacts have distinct authority:

- `TAXONOMY.md` defines the threat model, benchmark families, evidence tiers, and pre-registered held-out difficulty prediction.
- `benchmark/MANIFEST.yaml` defines the case record schema, canonical content-hash policy, and freeze fields.
- `benchmark/PROTOCOL.md` defines sampling, adjudication, statistical analysis, execution, and reporting procedure.

If these artifacts appear inconsistent, execution stops and the inconsistency is documented; a benchmark result must not be used to choose a convenient interpretation. This protocol does not change the Tier C family names or definitions fixed in `TAXONOMY.md`, and it does not change the schema or hash policy fixed in `benchmark/MANIFEST.yaml`.

## 2. Registered benchmark inventory

### 2.1 Tier A and Tier B total

Tier A and Tier B contain exactly **1,008 deterministic cases** across 18 families, with 56 cases per family. Their labels use `label_source=deterministic_invariant`. No model is called for these cases.

| Tier | Families | Cases per family | Tier total |
| --- | ---: | ---: | ---: |
| Tier A | 8 (`A1`-`A8`) | 56 | 448 |
| Tier B | 10 (`B1`-`B10`) | 56 | 560 |
| **Total** | **18** | **56** | **1,008** |

Each Tier A family contains exactly:

| Case class | Count | Ground truth | Expected action / result |
| --- | ---: | --- | --- |
| Target-invariant violation | 24 | `violation` | `BLOCK` |
| Benign and evaluable | 24 | `benign` | Determined by the invariant; ordinarily `ALLOW` when no other finding exists |
| Benign with required evidence unavailable | 8 | `benign` | `REVIEW`; target Tier A result is `NOT_EVALUABLE` |
| **Per-family total** | **56** |  |  |

The eight evidence-unavailable cases in each Tier A family are benign, not violations. `NOT_EVALUABLE` is a deterministic check result, not a `Finding`: only an established `FAIL` may produce a `Finding`.

Each Tier B family contains exactly:

| Case class | Count | Ground truth | Expected action |
| --- | ---: | --- | --- |
| Target-invariant violation | 28 | `violation` | `BLOCK` |
| Benign | 28 | `benign` | `ALLOW`, assuming all Tier A evidence is evaluable and passes |
| **Per-family total** | **56** |  |  |

### 2.2 Tier A and Tier B generation

Tier A and Tier B cases will be generated mechanically from typed fixtures. Labels will be derived from deterministic invariants; no LLM-generated label is permitted.

Generation should prefer single-fault isolation: a violation case mutates the target invariant while unrelated fields remain valid wherever possible. If one mutation necessarily causes multiple deterministic findings, the case record and result must retain every actual finding rather than suppressing findings to make the case appear isolated. The case retains its intended target `family_id`.

Every generated case must be reproducible from its recorded generator seed and parameters. Determinism applies to case content as well as the expected invariant result. Tier A and Tier B results are reported as invariant correctness and coverage; they are not presented as ML generalization.

### 2.3 Tier C total and family allocation

Tier C contains exactly **440 human-adjudicated cases**: **240 violation** and **200 benign**.

| Context | Family | Violation | Benign | Total |
| --- | --- | ---: | ---: | ---: |
| Development | `C-DEV-RECURRENCE` | 40 | 34 | 74 |
| Development | `C-DEV-EXCLUSION` | 40 | 33 | 73 |
| Development | `C-DEV-PURPOSE` | 40 | 33 | 73 |
| **Development subtotal** |  | **120** | **100** | **220** |
| Held-out | `C-HOLD-BUNDLE` | 40 | 34 | 74 |
| Held-out | `C-HOLD-COMPATIBILITY` | 40 | 33 | 73 |
| Held-out | `C-HOLD-FULFILLMENT` | 40 | 33 | 73 |
| **Held-out subtotal** |  | **120** | **100** | **220** |
| **Tier C total** |  | **240** | **200** | **440** |

The family names and definitions remain those in `TAXONOMY.md`; this protocol does not amend them.

### 2.4 Tier C split encoding

For a Tier C case, `split` is a pure function of the `family_id` prefix and of nothing else:

| `family_id` prefix | Required `split` |
| --- | --- |
| `C-DEV-` | `dev` |
| `C-HOLD-` | `held_out` |

This mapping holds regardless of `ground_truth`:

- `C-DEV-*` with `ground_truth=violation` &rarr; `split=dev`;
- `C-DEV-*` with `ground_truth=benign` &rarr; `split=dev`;
- `C-HOLD-*` with `ground_truth=violation` &rarr; `split=held_out`; and
- `C-HOLD-*` with `ground_truth=benign` &rarr; `split=held_out`.

A benign held-out case remains fully held out. Every held-out isolation, freeze, batch-finalization, and execution-ordering rule in this protocol applies to the 100 benign held-out cases exactly as it applies to the 120 violation held-out cases. Benign held-out content may not be authored, inspected, or executed early on the theory that benign cases are not the real test; the benign held-out false-positive rate is itself a headline held-out result.

`split=benign_control` **must not** be assigned to a Tier C case merely because `ground_truth=benign`. **`benign_control` is not used for Tier C in V1.** It remains a reserved manifest enum value for possible future benchmark classes outside the current Tier C development/held-out design. `benchmark/MANIFEST.yaml` must **not** be modified merely to remove it; the manifest enum is unchanged by this amendment.

Any Tier C record whose `split` does not match the table above fails the execution gates of §17.

## 3. Tier C provenance allocation

The provenance quotas below are fixed before case authoring.

Every 40-case violation family contains:

- 16 `developer_authored` cases;
- 12 `external_defensive_corpus_adapted` cases; and
- 12 `separate_model_adversarial` cases.

For benign cases, each 34-case family contains 14 `developer_authored`, 10 `external_defensive_corpus_adapted`, and 10 `separate_model_adversarial` cases. Each 33-case family contains 13, 10, and 10 cases from those provenances, respectively.

| Context | Family | Ground truth | Developer authored | External defensive adapted | Separate model adversarial | Total |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Development | `C-DEV-RECURRENCE` | Violation | 16 | 12 | 12 | 40 |
| Development | `C-DEV-RECURRENCE` | Benign | 14 | 10 | 10 | 34 |
| Development | `C-DEV-EXCLUSION` | Violation | 16 | 12 | 12 | 40 |
| Development | `C-DEV-EXCLUSION` | Benign | 13 | 10 | 10 | 33 |
| Development | `C-DEV-PURPOSE` | Violation | 16 | 12 | 12 | 40 |
| Development | `C-DEV-PURPOSE` | Benign | 13 | 10 | 10 | 33 |
| Held-out | `C-HOLD-BUNDLE` | Violation | 16 | 12 | 12 | 40 |
| Held-out | `C-HOLD-BUNDLE` | Benign | 14 | 10 | 10 | 34 |
| Held-out | `C-HOLD-COMPATIBILITY` | Violation | 16 | 12 | 12 | 40 |
| Held-out | `C-HOLD-COMPATIBILITY` | Benign | 13 | 10 | 10 | 33 |
| Held-out | `C-HOLD-FULFILLMENT` | Violation | 16 | 12 | 12 | 40 |
| Held-out | `C-HOLD-FULFILLMENT` | Benign | 13 | 10 | 10 | 33 |

The resulting registered totals are:

| Scope | Developer authored | External defensive adapted | Separate model adversarial | Total |
| --- | ---: | ---: | ---: | ---: |
| All violations | 96 | 72 | 72 | 240 |
| Development benign | 40 | 30 | 30 | 100 |
| Held-out benign | 40 | 30 | 30 | 100 |
| All benign | 80 | 60 | 60 | 200 |
| **All Tier C** | **176 (40%)** | **132 (30%)** | **132 (30%)** | **440** |

### 3.1 Provenance definitions and metadata

`developer_authored` means a case written directly by the benchmark author. Developer-authored held-out case content may be created only after detector freeze. The author may know the detector architecture, but the detector may not be modified after the author sees held-out content or results.

`external_defensive_corpus_adapted` means a case derived from an independently published defensive, safety, or evaluation corpus, or from a public defensive example source. Each adapted case must record the following provenance metadata:

- source name;
- source URL or reference;
- source version or date, if available; and
- a description of the adaptation.

Adaptation must preserve useful semantic structure without reproducing long copyrighted passages verbatim. This benchmark must not create a reusable offensive prompt-injection corpus.

**Held-out source isolation.** For held-out `external_defensive_corpus_adapted` cases, held-out isolation applies to the **source material itself**, not only to the finished case text. Before detector freeze, the benchmark and detector developers must **not**:

- select specific source passages or examples for held-out use;
- inspect candidate held-out passages or examples;
- prewrite held-out adaptations;
- store candidate held-out examples, whether in the repository, in notes, or in any working file;
- rank, score, or shortlist source examples for held-out inclusion; or
- prepare drafts, outlines, or paraphrases derived from them.

General knowledge that a public defensive corpus exists, and that it will be a provenance source for this benchmark, is allowed; this protocol already discloses it. What is prohibited is specific-item contact: which passages, which examples, which scenarios.

Specific source selection and inspection for held-out cases begins **only after detector freeze**. The specific source metadata and the adaptation description are then recorded exactly as required by the provenance rules above. Selecting and inspecting source material for **development** cases before freeze remains permitted; a source item already used for a development case may not later be reused for a held-out case.

Rationale: a developer who has read the specific passages destined to become held-out cases can tune the detector against them without ever writing a held-out case file. The held-out split would be contaminated while every case-level rule still appeared satisfied.

`separate_model_adversarial` means a case generated by a separate authoring model. For held-out authoring, the separate model may receive only:

- the high-level family definition;
- the case schema; and
- allowed domain constraints.

It must not receive:

- MandateGuard source code;
- detector implementation details;
- the D5 developer prompt;
- detector outputs;
- development benchmark cases;
- model failure examples or known detector failures discovered during detector tuning; or
- results from any previous held-out detector execution.

Each such case must record the authoring model identifier and the SHA-256 hash of the authoring prompt. This provenance is not described as a "blind evaluation."

**Held-out batching for separate-model authoring.** All `separate_model_adversarial` held-out case content must be generated and finalized **before any held-out detector result is available**. Held-out generation may not be sequentially adapted based on results from earlier held-out cases: the authoring model may not be re-prompted, re-seeded, steered, or reselected on the basis of held-out outcomes, and its candidate outputs may not be accepted or rejected on that basis. This is the §7.1 batch rule applied to the authoring model; an adversarial generator steered by held-out results is a development generator.

### 3.1.1 Provenance is an origin label and is immutable

Each Tier C case receives **exactly one** `provenance` value. The provenance reflects the **origin** of the case, not its final editor and not the amount of developer effort later spent on it.

- `developer_authored` &mdash; the content originated directly from the benchmark author, without an external source passage or a separate authoring model generating the initial case.
- `external_defensive_corpus_adapted` &mdash; the case originated from adaptation of external defensive material, and retains this provenance even after substantial developer rewriting.
- `separate_model_adversarial` &mdash; the initial case content or scenario was generated by the separate authoring model, and retains this provenance even after developer editing.

A case may **not** be relabelled `developer_authored` merely because a developer later edits it, tightens it, or largely rewrites it. Provenance laundering of that kind would allow the registered 40/30/30 provenance mix of §3 to be satisfied on paper while the real case population drifted toward whichever origin proved convenient.

Recorded at authoring time:

- for all Tier C cases: `provenance` and the authoring timestamp;
- for `external_defensive_corpus_adapted` cases: the source metadata already required above; and
- for `separate_model_adversarial` cases: the authoring model identifier and the SHA-256 authoring prompt hash already required above.

**Provenance is fixed before adjudication.** Changing provenance after adjudication changes hashed case content, and therefore creates a **new case record with a new `case_content_sha256`** under §6, retained alongside the original in audit history; it may never silently edit the existing record. After a case's first detector execution, its provenance may not change at all (§7.1 for held-out cases; §6 and §17 generally).

## 4. Tier C benign controls

Benign Tier C cases must be hard negatives rather than trivial controls. Within each family, benign cases should resemble violation cases in lexical complexity, semantic density, number of entities and conditions, transaction structure, and evidence quantity, while genuinely satisfying the mandate.

Conceptually, a recurrence hard negative may refer to a future time without creating an economic recurrence; an exclusion hard negative may mention an excluded concept while explicitly establishing its absence; and a compatibility hard negative may include multiple device identifiers while remaining compatible. These are authoring principles, not benchmark cases, and must not be encoded as detector-specific exceptions or rules.

Every benign Tier C label remains subject to the human-adjudication procedure below.

### 4.1 Benchmark breadth: anti-duplication and diversity

The registered quotas of §2.3 and §3 are a floor on the number of **distinct** scenarios, not a licence to fill a stratum with restatements of one scenario.

Within every Tier C `family_id` &times; `ground_truth` stratum, cases must show meaningful variation across the following dimensions, where applicable to that family:

- merchant, SKU, and other entity identities;
- mandate wording;
- semantic constraint phrasing;
- transaction structure;
- evidence structure;
- the number and order of relevant conditions; and
- lexical realization.

**Exact duplicates are forbidden.** **Near-duplicate cases intended only to fill quotas are forbidden**: a 40-case violation stratum must contain 40 substantively distinct scenarios, not a handful of scenarios with substituted names, amounts, or dates.

Before the first detector execution on a split, a duplicate and near-duplicate review must be performed and recorded for that split.

**V1 does not require an embedding-based deduplication system.** A documented combination of:

- exact canonical-content duplicate detection over canonical case content and `case_content_sha256`;
- normalized-text comparison, under a documented normalization and similarity criterion; and
- human or manual near-duplicate review within each stratum

is sufficient. The chosen method and its findings must be reported, including any case removed or rewritten as a result. A case rewritten because of this review re-enters adjudication and hashing as an ordinary pre-execution correction.

For held-out cases this review is part of batch finalization (§7.1, step 7) and must complete before the first held-out execution.

This machinery is not implemented by this amendment; the requirement is preregistered here.

## 5. Tier C human adjudication

Every Tier C `ground_truth` label must be assigned by a human before that case's first detector execution. The adjudicator must not be shown detector output. Ground truth is binary: `violation` or `benign`. `REVIEW` is a detector action and abstention state, not ground truth.

Before `first_run_at` becomes non-null, every case record must contain:

- `ground_truth`;
- `label_source=human_adjudication`;
- `label_recorded_at`; and
- `case_content_sha256`.

The pre-execution ordering is mandatory: canonicalize and finalize the case, adjudicate it without detector output, record its label and label time, compute and record its content hash, and only then permit its first detector run.

### 5.1 Second independent review: deterministic stratified selection

Second-review selection is deterministic. Discretionary second-review sampling is not permitted, before or after labels exist.

**Stratified minimum.** Every Tier C `family_id` &times; `ground_truth` &times; `provenance` stratum must **independently** select at least 25% of its own cases for second independent review. For a stratum of size `stratum_size`, the required count is `ceil(0.25 × stratum_size)`.

**Selection rule.** For each case compute:

```text
second_review_rank =
SHA256(
    UTF8("mandateguard-second-review-v1")
    ||
    UTF8(case_content_sha256)
)
```

where `||` is byte concatenation and `case_content_sha256` is the case's recorded lowercase hexadecimal digest string. `second_review_rank` is ordered as an unsigned big-endian 256-bit integer, equivalently as the ascending lexicographic order of its lowercase hexadecimal encoding.

Within each stratum:

- sort ascending by `second_review_rank`; and
- select the lowest `ceil(0.25 × stratum_size)` cases.

This deterministic selection is computed **only after** the primary label and `case_content_sha256` exist for the cases of that stratum. Because the rank depends on the content hash alone, any auditor can recompute the selection exactly, and no evaluator can re-draw a sample that produced an inconvenient disagreement.

**Ambiguous cases.** Every case marked ambiguous by the primary adjudicator receives a second independent review **in addition to** the deterministic sample. If an ambiguous case is already selected, it is not counted twice.

**Resulting totals.** The deterministic stratified minimum is at least 25% per stratum, yielding **at least 110 total cases** second-reviewed for the fixed 440-case benchmark, with additional ambiguous cases possibly increasing the count. The final number second-reviewed may therefore exceed 110. This is a lower bound and never a target: no required second review is skipped because some total has already been reached.

**Reporting.** The benchmark report must state:

- the number of cases that received two independent pre-detector labels;
- raw agreement; and
- Cohen's kappa.

Raw agreement and Cohen's kappa are reported over **all cases that received two independent pre-detector labels** &mdash; the deterministic sample and the ambiguous additions together &mdash; not over the deterministic sample alone.

**Prohibition.** No case may be selected for second review based on detector behavior, detector output, or any benchmark result, and none may be added to or removed from second review on that basis. All second reviews are complete before the affected case is first executed.

Disagreement handling, exclusion, and replacement follow §5.2.

### 5.2 Ambiguity, exclusion, and replacement audit

- **Ambiguity is recorded before detector execution.** The primary adjudicator's ambiguity marking is part of the case adjudication record and is made without detector output.
- **Disagreement resolution happens without detector output.** Two disagreeing adjudicators resolve the label from the case content and the mandate alone; the discussion, rationale, and final label are recorded.
- **Unresolved cases are excluded before execution.** A case whose disagreement cannot be resolved is excluded before its first execution. It is never executed and never enters any benchmark metric.
- **Exclusion remains in an audit/exclusion record.** Every exclusion is retained permanently with its `case_id`, stratum, reason, and timestamp, and exclusions are reported alongside the benchmark results.
- **Replacement preserves the exact quota.** A replacement comes from the identical `family_id` &times; `ground_truth` &times; `provenance` stratum as the excluded case.
- **A replacement receives a new `case_id`.** Excluded case IDs are retired permanently and are **never reused**, so audit history can always distinguish an excluded case from its replacement.
- **A replacement is independently adjudicated and hashed** like any other case, and is subject to the deterministic second-review selection of §5.1 under its own `case_content_sha256`.
- **The final executable benchmark remains exactly 440 Tier C cases**, with exactly the family, ground-truth, and provenance quotas registered in §2.3 and §3.
- **All held-out replacements must happen before held-out batch finalization** (§7.1, step 6; §7.2). After the first held-out execution the held-out set is closed and replacement is not available at all.

No detector output may influence which cases are excluded or which replacements are selected. A case may never be silently relabelled after detector output has been observed.

## 6. Case hashing, immutability, and audit history

The existing `benchmark/MANIFEST.yaml` hash policy is controlling. Before first detector execution, `case_content_sha256` must contain the SHA-256 digest of the complete canonical case content required by the manifest. Canonicalization uses MandateGuard canonical JSON: UTF-8, sorted keys, no floats, and no insignificant whitespace. The hashed content includes evaluation inputs, `family_id`, `evidence_tier`, `provenance`, `split`, `ground_truth`, and `label_source`; it excludes the audit-only `first_run_at` value.

After label recording, any change to hashed content creates a new case digest and a distinct audit record. The original labelled case and hash remain in audit history. Detector results may never cause an old benchmark case to be silently edited, overwritten, or relabelled.

## 7. Development, freeze, and held-out handling

Development-family content may be authored, inspected, and used before detector freeze for development-only model selection and diagnostics. Held-out family names and definitions are already pre-registered and are not secret, but held-out case content must not be authored, revealed, inspected, or used before detector freeze.

Detector freeze occurs at the end of D9. At freeze, record at minimum:

- Git commit SHA;
- `detector_version`;
- `prompt_version`;
- selected `model_id`;
- semantic output schema version; and
- benchmark protocol commit SHA.

No detector, prompt, model, threshold, output-schema, or decision-rule change is permitted after held-out reveal. If detector code changes after held-out content is revealed, the affected held-out evaluation is contaminated and must be reported as such; it cannot be presented as an uncontaminated held-out result.

### 7.1 Held-out batch finalization: all cases before any execution

The held-out split is authored, adjudicated, and finalized as a **single closed batch**. After detector freeze at the end of D9, **all 220 held-out Tier C cases must be completed before the detector executes on any held-out case.**

The 220 are the full held-out inventory of §2.3: 120 violation and 100 benign cases across `C-HOLD-BUNDLE`, `C-HOLD-COMPATIBILITY`, and `C-HOLD-FULFILLMENT`, under the provenance quotas of §3.

The required order is:

1. Detector, model, and prompt freeze recorded (end of D9, fields as listed above).
2. Author or reveal **all 220** held-out cases.
3. Human-adjudicate **all 220**, without detector output.
4. Perform the second reviews required by §5.1.
5. Resolve all adjudicator disagreements, without detector output.
6. Exclude and replace any unresolved case under §5.2, preserving stratum quotas.
7. Complete the duplicate and near-duplicate diversity review of §4.1 for the held-out split.
8. Record `ground_truth`, `label_source`, `label_recorded_at`, and provenance metadata for **all 220**.
9. Canonicalize and compute `case_content_sha256` for **all 220**.
10. Verify `first_run_at == null` for **all 220**.
11. Record the held-out batch-finalization checkpoint of §7.2.
12. Only then execute the frozen detector on any held-out case.

**Explicitly prohibited.** The following pattern, and every sequential or adaptive equivalent of it, is forbidden:

```text
author case 1
run detector
inspect result
author case 2
```

Equivalent prohibited patterns include: authoring held-out cases in waves with detector execution between waves; executing one held-out family before another held-out family has been authored; running a "pilot", "smoke", "sanity", or "calibration" subset of held-out cases before the batch is finalized; and adjusting held-out authoring difficulty, wording, scenario choice, or provenance mix in light of anything learned from a held-out run. Held-out authoring must be informationally independent of held-out results, because a held-out set assembled with knowledge of held-out results is a development set.

**Batch closure.** Once the first held-out detector execution begins, the held-out case set is **closed**. Based on detector results, no case may be:

- added;
- replaced;
- removed;
- edited in hashed content;
- relabelled; or
- changed in provenance.

If a genuine defect in a held-out case is discovered after the first execution &mdash; a mislabel, a malformed fixture, a duplicate that §4.1 missed &mdash; the discovery, its evidence, and any correction remain part of the audit history under §6 as a new case record and a new digest. Any correction after first execution **may not silently alter the frozen primary benchmark**. The frozen primary held-out result is reported as it was executed, and any corrected recomputation is reported separately and explicitly as a post-hoc sensitivity analysis, never as the headline held-out result.

### 7.2 Held-out batch-finalization checkpoint

Immediately before the first held-out execution, and only once steps 1-10 of §7.1 are complete, a held-out batch-finalization checkpoint must be recorded containing at minimum:

- the detector freeze Git commit SHA;
- the benchmark protocol Git commit SHA, at the amendment then in force;
- `detector_version`;
- `prompt_version`;
- the selected `model_id`;
- total held-out case count = 220;
- count with `ground_truth` recorded = 220;
- count with `case_content_sha256` recorded = 220;
- count with `first_run_at == null` = 220; and
- the timestamp of batch finalization.

If any of those four counts is not exactly 220, held-out execution does not begin. The checkpoint is written once, is immutable thereafter, and is reproduced in the benchmark report.

**The checkpoint machinery is not implemented yet.** The requirement is preregistered here and is to be implemented at or before D10, without weakening any field above.

## 8. Model and detector selection

Held-out data may never participate in model, prompt, threshold, rule, or output-schema selection. Development families may be used before the end-of-D9 freeze for model selection and diagnostics. MandateGuard does not use LLM confidence for authorization.

If multiple model configurations are evaluated on development data, the final configuration is selected by the following exact lexicographic ordering. A later criterion is consulted only when every earlier criterion is exactly tied.

1. lowest development `violation → ALLOW` rate;
2. lowest development `benign → BLOCK` rate;
3. lowest development `REVIEW` rate;
4. lowest development semantic-path p95 latency; and
5. lowest estimated development semantic provider cost per evaluated semantic case.

**Every quantity in this ordering is defined over the development benchmark only.** Rates are computed over `split=dev` Tier C cases using the accounting of §9, the definitions of §10, and development-only denominators (§11.1). Semantic-path p95 latency uses the boundaries fixed in §15.1, measured on development cases. Estimated provider cost is the estimated provider charge divided by the number of development cases that actually invoked the semantic path, with the pricing basis and its date recorded. Held-out data contributes to none of these five quantities.

If candidates remain tied after all five criteria, the winner is decided by a deterministic final tie-break: the lexicographically smallest tuple

```text
(model_id, detector_version, prompt_version)
```

compared component by component as UTF-8 strings. Any equally simple deterministic identifier ordering may be substituted, provided it is recorded before selection. This tie-break carries no quality claim; it exists solely to leave no residual choice.

**No evaluator discretion is permitted after results are known.** The rule above is applied mechanically. A candidate may not be preferred for an unregistered reason, such as a subjective impression of its behavior on hard cases or a judgement that a cheaper configuration is close enough.

All candidate configurations and their complete development result summaries are retained (§8.1), whether selected or not, together with every tie encountered. The selected `model_id` and `prompt_version` are frozen before held-out content is revealed. No held-out winner may be cherry-picked.

### 8.1 Development model-selection ledger

A development model-selection ledger must be retained, with one entry per evaluated candidate configuration, containing at minimum:

- candidate `model_id`;
- `detector_version`;
- `prompt_version`;
- evaluation timestamp;
- development result summary, at minimum the counts, rates, and denominators entering criteria 1-3 above;
- semantic p95 latency on development data;
- estimated provider cost per evaluated semantic development case;
- whether the candidate was selected; and
- the selection rationale, expressed strictly in terms of the pre-registered lexicographic rule: which criterion decided the outcome, and the compared values.

This is **audit metadata only**. It is not a benchmark result and is not reported as one. Development iteration, including evaluating and discarding many candidates, remains allowed before the end-of-D9 freeze; the ledger exists to make the extent of that iteration visible, so a reader can judge multiple-comparison risk in the development numbers, rather than to restrict it.

**The ledger is not implemented yet.** The requirement is preregistered here.

## 9. Tier C outcome accounting

Tier C ground truth is binary (`violation`, `benign`) and detector action has three values (`BLOCK`, `ALLOW`, `REVIEW`). The complete accounting is:

| Ground truth | `BLOCK` | `ALLOW` | `REVIEW` |
| --- | --- | --- | --- |
| Violation | `TP` | `FN` | `RV` |
| Benign | `FP` | `TN` | `RB` |

Thus:

- `TP` = violation mapped to `BLOCK`;
- `FN` = violation mapped to `ALLOW`;
- `RV` = violation mapped to `REVIEW`;
- `FP` = benign mapped to `BLOCK`;
- `TN` = benign mapped to `ALLOW`; and
- `RB` = benign mapped to `REVIEW`.

`REVIEW` is an action/abstention, not a ground-truth label.

## 10. Pre-registered metrics

The primary Tier C metrics are:

| Metric | Definition |
| --- | --- |
| Precision | `TP / (TP + FP)` |
| Detection recall | `TP / (TP + FN + RV)` |
| Unsafe-allow rate | `FN / (TP + FN + RV)` |
| Benign false-positive rate | `FP / (FP + TN + RB)` |
| Overall REVIEW rate | `(RV + RB) / N` |
| Violation REVIEW rate | `RV / (TP + FN + RV)` |
| Benign REVIEW rate | `RB / (FP + TN + RB)` |
| Coverage | `(TP + FN + FP + TN) / N` |

A `REVIEW` action on a violation remains in the detection-recall denominator. Abstaining on a difficult violation therefore cannot inflate primary recall. Likewise, benign reviews remain in the denominator for benign false-positive rate.

Metrics conditioned on non-`REVIEW` cases must also be reported where relevant, but they must be labelled **secondary/selective**. Examples include selective recall `TP / (TP + FN)`, selective benign false-positive rate `FP / (FP + TN)`, and selective accuracy `(TP + TN) / (TP + FN + FP + TN)`. Selective accuracy is not a headline metric. No headline "accuracy" metric is permitted.

### 10.1 Confidence intervals

Every reported binomial proportion uses a two-sided **95% Wilson score interval**, with `z = 1.959963984540054`. For a numerator `x`, denominator `n`, and point estimate `p̂ = x/n`, the interval is:

```text
center = (p̂ + z²/(2n)) / (1 + z²/n)
half_width = z × sqrt((p̂(1-p̂) + z²/(4n))/n) / (1 + z²/n)
interval = [center - half_width, center + half_width]
```

Every proportion must be reported with its numerator and denominator, point estimate, and 95% Wilson interval. A percentage without its sample count is not sufficient. When the denominator is zero, the metric is reported as undefined; it must not be assigned an invented 0% or 100%. Per-family intervals are expected to be wider and must still be shown.

## 11. Required Tier C reporting

The report must include at minimum:

- precision;
- detection recall;
- unsafe-allow rate;
- benign false-positive rate;
- overall `REVIEW` rate; and
- coverage.

The same relevant metrics must be reported for the following slices:

- each of the six families;
- development families versus held-out families;
- each provenance; and
- violation versus benign ground truth, where the metric is applicable.

For single-class slices, report the applicable action rates and mark inapplicable cross-class metrics as undefined rather than manufacturing a value.

The report must also include:

- model-touch fraction, reported as model-invoked cases divided by evaluated cases;
- deterministic-only path p50 and p95 latency;
- semantic path p50 and p95 latency;
- semantic provider failure count;
- malformed-output count; and
- abstention-reason counts.

The model-touch fraction is a binomial proportion and follows the same count and Wilson-interval rules. Latency percentiles, failure counts, malformed outputs, and reason counts are descriptive measurements rather than binomial classification metrics and must include their measurement population.

### 11.1 Slice-local denominators

Every reported slice metric uses the denominator belonging to **that slice**. A per-slice proportion never uses the global benchmark `N` unless the metric definition in §10 explicitly calls for a global denominator.

Examples:

- held-out recall denominator = **held-out violations only** (`TP + FN + RV` restricted to held-out violation cases), not all 440 Tier C cases and not all 220 held-out cases;
- `C-HOLD-BUNDLE` benign FPR denominator = **benign `C-HOLD-BUNDLE` cases only** (`FP + TN + RB` within that family and that ground truth);
- provenance-specific `REVIEW` rate denominator = **cases of that provenance only**, within whatever slice is being reported; and
- a development-versus-held-out comparison uses each side's own denominator on its own side.

Each reported proportion states its numerator, its denominator, the point estimate, and the 95% Wilson interval of §10.1. Diluting a slice rate with a larger population &mdash; for example reporting one held-out family's false-positive rate over all 200 benign Tier C cases &mdash; is prohibited, because it lets a slice failure be made arbitrarily small by choice of denominator.

### 11.2 Tier A `NOT_EVALUABLE` reporting

`TAXONOMY.md` requires that `NOT_EVALUABLE` be reported separately from `PASS` and `FAIL`, that it never be counted as successful detection, and that a `Finding` be emitted only for `FAIL`. This protocol cross-references and adopts that requirement for benchmark reporting; it does not amend it.

Tier A reporting must therefore separately account for three check outcomes:

- `PASS`;
- `FAIL`; and
- `NOT_EVALUABLE`.

**`NOT_EVALUABLE` must never be represented as a `Finding`**, folded into `FAIL`, or counted as a detected violation.

The 8 benign evidence-unavailable cases per Tier A family (§2.1) are the designed test of this behavior. A benign case whose required evidence is unavailable, and which correctly produces `NOT_EVALUABLE` on the target check and therefore `REVIEW` as the authorization action, **is not a detector error**. It is not a false positive and it is not a missed detection. Scoring such a case as an error would penalize the detector for correctly refusing to decide without evidence, which is the intended behavior.

Where the two can diverge, expected-action correctness (did the authorization action match the expected `ALLOW` / `REVIEW` / `BLOCK`?) and evidence-state correctness (did the target check reach the expected `PASS` / `FAIL` / `NOT_EVALUABLE` state?) are reported separately, so that a case cannot be scored correct on its action while having reached that action through the wrong evidence state.

## 12. Generalization claim

Generalization claims apply only to Tier C held-out families. Tier A and Tier B are deterministic invariant checks and must not be described as demonstrating generalization.

The primary generalization comparison is development-family Tier C performance versus held-out-family Tier C performance under the metrics and intervals above. The report must also reproduce the pre-registered held-out difficulty prediction from `TAXONOMY.md`:

1. `C-HOLD-BUNDLE` is easiest;
2. `C-HOLD-COMPATIBILITY` is in the middle; and
3. `C-HOLD-FULFILLMENT` is hardest.

Observed results must be described as supporting or refuting this prediction. The prediction must not be rewritten after evaluation.

## 13. Economic reporting

Economic analysis is separate from classification metrics. Every economic result must state its assumptions, units, valuation window, and aggregation rule.

At minimum, calculate and report:

- false-positive blocked-GMV cost;
- false-negative unauthorized-value cost; and
- `REVIEW` friction/delay cost.

If `C_FP` is the cost of incorrectly blocking a benign payment and `C_FN` is the cost of allowing an unauthorized payment, the simple binary break-even risk threshold may be shown as:

```text
p* = C_FP / (C_FP + C_FN)
```

This is an economic decision-threshold derivation under stated assumptions. Raw model confidence is not the probability `p`, and no uncalibrated LLM confidence may authorize execution.

### 13.1 Economic analysis is downstream interpretation only

Economic assumptions, cost parameters, valuation windows, and break-even thresholds are downstream interpretation of an already-fixed benchmark result. They may **not**:

- change ground-truth labels;
- change the primary confusion accounting of §9 or any primary metric of §10;
- remove benchmark cases, or reweight or exclude them; or
- retroactively change, restate, or reinterpret recorded detector results.

The classification result is computed and reported first, from the frozen cases and the recorded detector actions. Economic analysis is layered on top afterwards with its assumptions stated, and may be recomputed under alternative assumptions as sensitivity analysis. Because the classification numbers are fixed before any economic assumption is chosen, revising an economic assumption moves only the economic interpretation.

## 14. Confidence diagnostic

If raw model confidence is available diagnostically, analyze it on development data only and report a reliability diagram and expected calibration error (ECE), including the binning definition used. Do not fit a production calibrator in V1, and do not use confidence to select `ALLOW` or `BLOCK`.

If the D5 structured output does not expose confidence, report this diagnostic as not applicable. The frozen semantic output schema must not be modified solely to make the benchmark confidence diagnostic available.

## 15. Latency measurement

Measure deterministic-only paths and semantic paths separately. Report p50 and p95 for each; do not combine them into a single latency statistic.

The report must record enough environment detail for interpretation, including relevant hardware, operating system, runtime and dependency versions, execution mode, sample count, warm-up or caching policy, and model/provider configuration where applicable. External model latency is inherently non-deterministic and must not be described as deterministic.

### 15.1 Measurement boundaries

These boundaries are fixed before execution, so that a latency number cannot be improved afterwards by redefining what was measured.

**Deterministic-only authorization latency.**

- Start: entry into MandateGuard authorization evaluation.
- End: the final deterministic `ALLOW` / `REVIEW` / `BLOCK` action is available.
- Includes: Tier A, Tier B, and deterministic decision composition.
- Excludes: benchmark fixture generation, test-harness setup, and Razorpay execution.

**Semantic-path authorization latency.**

- Start: entry into MandateGuard authorization evaluation.
- End: the final Tier C-derived `ALLOW` / `REVIEW` / `BLOCK` action is available.
- Includes: Tier A/B gating, semantic request construction, the semantic provider call, structured output validation, local semantic reduction, and the cache write required by the normal live path.
- Excludes: benchmark authoring and Razorpay execution.

p50 and p95 are reported separately for each path, and the two paths are never combined into a single latency statistic (§15). Runtime and environment details are recorded as required by §15.

**Do not invent latency confidence intervals.** External provider latency is non-deterministic and the sample is a convenience sample from one environment. The measured percentiles are reported with their sample counts and environment, and no interval or distributional claim beyond that is asserted.

## 16. Post-freeze robustness probe

Only after the complete main frozen evaluation may a structured minimal-mutation robustness probe run. Its purpose is to identify the smallest semantic or economic change that converts a ground-truth violation to `ALLOW` or to a materially weaker action.

Permitted structured mutation classes include:

- paraphrase;
- clause-order change;
- irrelevant-text insertion;
- entity substitution within the same semantic family; and
- small economic-value changes that preserve ground truth.

The probe must not create or publish reusable offensive prompt-injection payloads. Every discovered failure must be reported; detector code must not be patched in response. Probe results are reported separately and do not alter, replace, or retroactively reinterpret the main frozen benchmark results.

## 17. Execution gates and exclusions

Before any Tier C case is first executed, verify that its label, label source, label time, and content hash are present, its adjudicator has not seen detector output, any required second review is complete, and any disagreement is resolved. A failure of any gate prevents execution.

Amendment A1 adds the following execution gates:

- the case's `split` matches the §2.4 `family_id` prefix mapping exactly, and is not `benign_control`;
- the case's `provenance` was recorded at authoring time before adjudication and is unchanged since (§3.1.1);
- the deterministic second-review selection of §5.1 has been computed for the case's stratum, and every selected case and every ambiguous case in that stratum carries its second independent label;
- the duplicate and near-duplicate review of §4.1 is complete and recorded for the case's split; and
- for any held-out case, the held-out batch-finalization checkpoint of §7.2 exists, records all four counts as exactly 220, and is timestamped before the execution.

A failure of any of these gates prevents execution &mdash; for the individual case, and in the case of the held-out batch gates for the entire held-out split.

Exclusions must occur before first execution and be logged with a reason. Required quota replacements must come from the identical family, ground-truth, and provenance stratum. Post-result removal, relabelling, or substitution of an inconvenient case is prohibited.

This preregistration change itself must not:

- create `benchmark/cases/*`;
- author or inspect held-out examples;
- create Tier C authoring prompts containing case examples;
- create generated fixtures;
- modify `benchmark/MANIFEST.yaml`;
- modify `TAXONOMY.md`;
- modify detector code or frozen D1-D6 work;
- change Tier C family definitions;
- implement the benchmark generator; or
- run a Tier C model evaluation.

Those constraints ensure that the protocol commit is timestamped before case authoring and before detector evaluation.

Amendment A1 (§0.1) is subject to the identical constraints and satisfies them: it modifies `benchmark/PROTOCOL.md` only, creates no benchmark case, authors and inspects no held-out content and no held-out source material, implements no machinery, and leaves `TAXONOMY.md`, `benchmark/MANIFEST.yaml`, detector code, and frozen D1-D6 work unchanged.
