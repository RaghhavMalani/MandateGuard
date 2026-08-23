# MandateGuard Benchmark Pre-registration Protocol

**Status:** Pre-registered before benchmark case authoring and detector evaluation

**Repository baseline:** `012a31e5bf1d63fe1fe9b74b9337a3a24f72a4ff`

**Scope:** V1 benchmark sampling, adjudication, execution, analysis, and reporting

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

`separate_model_adversarial` means a case generated by a separate authoring model. For held-out authoring, the separate model may receive only:

- the high-level family definition;
- the case schema; and
- allowed domain constraints.

It must not receive:

- MandateGuard source code;
- detector implementation details;
- the D5 developer prompt;
- detector outputs;
- development benchmark cases; or
- model failure examples discovered during detector tuning.

Each such case must record the authoring model identifier and the SHA-256 hash of the authoring prompt. This provenance is not described as a "blind evaluation."

## 4. Tier C benign controls

Benign Tier C cases must be hard negatives rather than trivial controls. Within each family, benign cases should resemble violation cases in lexical complexity, semantic density, number of entities and conditions, transaction structure, and evidence quantity, while genuinely satisfying the mandate.

Conceptually, a recurrence hard negative may refer to a future time without creating an economic recurrence; an exclusion hard negative may mention an excluded concept while explicitly establishing its absence; and a compatibility hard negative may include multiple device identifiers while remaining compatible. These are authoring principles, not benchmark cases, and must not be encoded as detector-specific exceptions or rules.

Every benign Tier C label remains subject to the human-adjudication procedure below.

## 5. Tier C human adjudication

Every Tier C `ground_truth` label must be assigned by a human before that case's first detector execution. The adjudicator must not be shown detector output. Ground truth is binary: `violation` or `benign`. `REVIEW` is a detector action and abstention state, not ground truth.

Before `first_run_at` becomes non-null, every case record must contain:

- `ground_truth`;
- `label_source=human_adjudication`;
- `label_recorded_at`; and
- `case_content_sha256`.

The pre-execution ordering is mandatory: canonicalize and finalize the case, adjudicate it without detector output, record its label and label time, compute and record its content hash, and only then permit its first detector run.

### 5.1 Second independent review

At least 25% of Tier C cases—at least **110 of 440**—must receive a second independent human label before detector execution. The sample must be selected with stratification across family, ground truth, and provenance. The selection and completed second labels must not depend on detector behavior.

In addition to the stratified sample, every case that the primary adjudicator marks ambiguous must receive a second independent review before execution, even if this raises the double-labelled total above 25%.

The benchmark report must state:

- the number of cases double-labelled;
- raw agreement; and
- Cohen's kappa.

If the two adjudicators disagree, the case may not be executed until the disagreement is resolved without detector output. If agreement cannot be reached, the case is excluded before first execution and replaced by a new case from the same `family_id × ground_truth × provenance` stratum. Exclusions and their reasons must be recorded separately. A case may never be silently relabelled after detector output has been observed.

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

After the freeze, the required order is:

1. Author or reveal held-out case content.
2. Human-adjudicate it without detector output.
3. Record labels and adjudication metadata.
4. Canonicalize and hash each case under the manifest policy.
5. Execute the frozen detector on the held-out cases for the first time.

No detector, prompt, model, threshold, output-schema, or decision-rule change is permitted after held-out reveal. If detector code changes after held-out content is revealed, the affected held-out evaluation is contaminated and must be reported as such; it cannot be presented as an uncontaminated held-out result.

## 8. Model and detector selection

Held-out data may never participate in model, prompt, threshold, rule, or output-schema selection. Development families may be used before the end-of-D9 freeze for model selection and diagnostics. MandateGuard does not use LLM confidence for authorization.

If multiple model configurations are evaluated on development data, the final configuration is selected lexicographically by:

1. lowest `violation → ALLOW` rate;
2. lowest `benign → BLOCK` rate;
3. lowest `REVIEW` rate; and
4. lower semantic-path latency/cost.

Later criteria are considered only when all earlier criteria tie. Candidate definitions, complete development results, and all ties must be retained in the audit record. The selected `model_id` and `prompt_version` are frozen before held-out content is revealed. No held-out winner may be cherry-picked.

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

## 14. Confidence diagnostic

If raw model confidence is available diagnostically, analyze it on development data only and report a reliability diagram and expected calibration error (ECE), including the binning definition used. Do not fit a production calibrator in V1, and do not use confidence to select `ALLOW` or `BLOCK`.

If the D5 structured output does not expose confidence, report this diagnostic as not applicable. The frozen semantic output schema must not be modified solely to make the benchmark confidence diagnostic available.

## 15. Latency measurement

Measure deterministic-only paths and semantic paths separately. Report p50 and p95 for each; do not combine them into a single latency statistic.

The report must record enough environment detail for interpretation, including relevant hardware, operating system, runtime and dependency versions, execution mode, sample count, warm-up or caching policy, and model/provider configuration where applicable. External model latency is inherently non-deterministic and must not be described as deterministic.

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
