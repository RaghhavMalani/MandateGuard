# INT-3 evidence sufficiency and value of information

## Scope, target, and limits

INT-3 is non-benchmark engineering infrastructure. It asks whether a subset of
trusted evidence preserves the authorization action observed once with the
frozen full eligible-evidence input:

```text
decision_stable = subset_final_action == frozen_full_evidence_final_action
```

The registered name of this target is **SINGLE-EXECUTION ACTION STABILITY**.
It is not true human intent, semantic correctness, probability of correctness,
or causal evidence necessity. It is also not evidence that the frozen
full-evidence action was correct. Semantic expectation labels are never reused
as sufficiency labels.

No INT-3 subset semantic execution or stability label was observed before the
methodology and artifacts in this document were frozen. The future live dataset
uses one no-retry semantic execution for each previously unseen unique exact
semantic input.

## Frozen sources and subset plan

INT-3 reads the six frozen Stage-B cases, queries, catalog, eligible merchant
evidence, Stage-A retrieval scores, and Stage-B condition E full-evidence
references without modifying INT-1 or INT-2. It does not recompute the
full-evidence references.

Every non-empty subset is enumerated in stable `(subset size, eligible order)`
order. A stable bit mask over eligible-evidence order forms the observation ID:

```text
INT3:<query_id>:m<eligible-order-bitmask>
```

| Query | Eligible evidence | Non-empty subsets |
| --- | ---: | ---: |
| `INT2-Q-STUDYGLOW` | 4 | 15 |
| `INT2-Q-NOTEBOOK` | 4 | 15 |
| `INT2-Q-STUDY-CLUB` | 4 | 15 |
| `INT2-Q-MARKET-EDGE` | 3 | 7 |
| `INT2-Q-TAX-GUIDE` | 3 | 7 |
| `INT2-Q-FLEXI` | 2 | 3 |
| **Total** |  | **62** |

The 62 observations are correlated subsets, not 62 independent commerce
cases. The independent grouping unit is the six frozen queries.

## Diagnostic features versus deployable model features

The original 36-feature extractor remains available for diagnostics and
artifact inspection. It is not the model schema. The deployable model uses the
following frozen ordered 14-feature manifest:

1. `evidence_count`
2. `evidence_fraction`
3. `sku_scoped_evidence_fraction`
4. `merchant_scope_evidence_present`
5. `product_scope_evidence_present`
6. `max_score`
7. `mean_score`
8. `score_margin`
9. `source_kind_count`
10. `source_kind_diversity`
11. `constraint_count`
12. `constraint_family_purpose`
13. `constraint_family_exclusion`
14. `evidence_text_kchars_mean`

All 14 are available at runtime before subset semantic inference. The
purpose/exclusion indicators are derived from actual runtime mandate constraint
`kind` values, never query ID, case ID, or an engineering fixture label.
Case-family one-hot diagnostics are consequently excluded as redundant.

The 22 diagnostic-only fields are:

```text
sku_scoped_evidence_count
retrieval_scores_available
min_score
lexical_max_score
lexical_mean_score
lexical_min_score
lexical_score_margin
semantic_max_score
semantic_mean_score
semantic_min_score
semantic_score_margin
hybrid_max_score
hybrid_mean_score
hybrid_min_score
hybrid_score_margin
required_annotation_fraction
relevant_annotation_fraction
case_family_purpose_and_exclusion
case_family_exclusion_only
case_family_purpose_only
case_family_other
evidence_text_kchars_total
```

In particular, `required_annotation_fraction` and
`relevant_annotation_fraction` depend on manually authored evaluation oracles.
They are useful diagnostics but unavailable for ordinary production requests,
so they cannot enter learning or inference.

No model feature encodes query/case identity, engineering expectation,
full-reference action or verdict, subset outcome, `decision_stable`, or a
relevance/required oracle label. Dataset APIs keep the 36-feature diagnostic
matrix separate from the 14-feature model matrix.

The preregistration artifact is
`artifacts/engineering/int3/model_feature_manifest.json`. Its canonical SHA-256
is:

```text
b5201911ac47dd1f17059431d88f4a2c4287875a1025821ebabcf8330a811f20
```

## Frozen model pipeline

The deliberately small baseline is a scikit-learn pipeline:

```text
StandardScaler(with_mean=True, with_std=True)
LogisticRegression(
    penalty="l2",
    C=1.0,
    solver="lbfgs",
    max_iter=2000,
    fit_intercept=True,
    random_state=0,
    class_weight=None,
    tol=0.0001,
)
```

No class weighting or hyperparameter tuning may be selected after INT-3 labels
are observed. The small linear model is deliberate: there are only six grouped
engineering units, and interpretability and calibration matter more than model
capacity.

## Evaluation protocol and metrics

Evaluation remains six-fold leave-one-query-out. Each fold holds out every
subset from one query and trains on the other five. Random subset splits and
generic random-split performance are not supported because they would put
shared mandate, transaction, evidence, catalog, and query structure into both
train and test.

Future fold reports retain Brier score, false-SUFFICIENT, and
false-INSUFFICIENT counts/rates. ROC-AUC is reported only when both target
classes exist. Generic accuracy is not a headline metric.

## One-step expected-loss controller

For current sufficiency probability `p_current`:

```text
L_DECIDE = (1 - p_current) * C_UNSTABLE_DECISION
L_REVIEW = C_REVIEW
```

For each missing eligible evidence item `e`, the model evaluates the
counterfactual runtime features for `E union {e}` and supplies `p_after_e`:

```text
L_RETRIEVE(e) = C_ACQUIRE(e) + min(
    (1 - p_after_e) * C_UNSTABLE_DECISION,
    C_REVIEW,
)

best_retrieval = argmin_e L_RETRIEVE(e)
overall_action = argmin(L_DECIDE, L_REVIEW, best L_RETRIEVE)
```

The controller returns the full decomposition: current decide/review losses
and, for every candidate, acquisition cost, post-acquisition decide/review
losses, best terminal action/loss, and total retrieval loss. When no evidence
remains, `RETRIEVE_MORE` is unavailable. Equal overall losses use the explicit
safety order `REVIEW`, `RETRIEVE_MORE`, `DECIDE`.

## Net value of information

The primary VoI is expected engineering-loss reduction, not probability gain
divided by cost:

```text
L_BASE = min(L_DECIDE, L_REVIEW)

L_AFTER(e) = C_ACQUIRE(e) + min(
    (1 - p_after_e) * C_UNSTABLE_DECISION,
    C_REVIEW,
)

NET_VOI(e) = L_BASE - L_AFTER(e)
```

Higher is better; positive `NET_VOI` means acquisition lowers expected loss.
`delta_p = p_after_e - p_current` is retained only as a diagnostic and
tie-breaker. The planner constructs counterfactual features locally and never
fetches evidence or invokes the semantic provider.

## Safety boundary

The learned controller emits only `DECIDE`, `RETRIEVE_MORE`, or `REVIEW`.
`DECIDE` means proceed to the existing semantic path; it does not mean
authorization `ALLOW`. The sufficiency layer never emits `ALLOW` or `BLOCK` and
never overrides Tier A/B `BLOCK` or `REVIEW`, the semantic verifier, signed
capability and ledger checks, or the Razorpay execution gate.

## Exact prior-result reuse and frozen live plan

Prior reuse is bound to the immutable INT-2 Stage-B
`stage_b_observations.jsonl` at commit
`3946aa50c477881b1b085e35b60c9a411b6c8d64`. The source file SHA-256 is:

```text
311ec367c29299bbc0d90831c35c08d87e0c8ed5acd1353bcd5777cbc9bd0a75
```

Reuse requires exact equality of `semantic_input_sha256`. Fuzzy matching,
same-product assumptions, and same-evidence-ID assumptions are forbidden. A
matched subset carries the immutable observed semantic result and source
commit/file/run/observation provenance, and requires zero future API calls.
Unmatched results remain null and `decision_stable` remains null in the plan.

The frozen plan contains:

| Measure | Count |
| --- | ---: |
| Nominal subset observations | 62 |
| Unique exact semantic-input hashes | 62 |
| Prior exact-result matches | 15 |
| New unique live inputs | 47 |
| Predicted future semantic API calls | 47 |

Each future new exact input permits one attempt and zero retries. The plan is at
`artifacts/engineering/int3/subset_live_execution_plan.json`; its canonical
SHA-256 is:

```text
ed6f5c57cbea9ca0399b021c3516e829fa5cb51f7e025a1f003e9b0b1cfd284d
```

The file was built before live subset execution and must not be modified after
outcomes are observed.

## Offline commands

From the repository root with Python 3.12 and `src` on the import path:

```powershell
python scripts/generate_int3_subset_plan.py
python scripts/freeze_int3_methodology.py --created-at <timezone-aware-ISO-8601>
python scripts/run_int3_offline_demo.py
```

These commands perform local construction only. The methodology freeze and
synthetic demo make zero semantic-provider, evidence-fetch, buyer, or Razorpay
calls. `subset_results.jsonl` and `sufficiency_dataset.csv` remain future
artifacts because no new INT-3 labels exist.

## Future research path

After authorized live execution supplies defensible labels, follow-on work may
study calibration, active selection of informative subsets, contextual bandits,
or constrained sequential retrieval. None of those methods is implemented or
justified by the current six-query engineering dataset.
