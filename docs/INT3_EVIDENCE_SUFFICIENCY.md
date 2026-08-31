# INT-3 evidence sufficiency and value of information

## Scope and thesis

INT-3 is non-benchmark engineering infrastructure. It asks one narrow question:

> Given a subset of trusted evidence, is that subset sufficient to preserve the
> authorization decision made using the full eligible trusted evidence?

For a future executed subset, the target is defined only as:

```text
decision_stable = subset_final_action == full_reference_action
```

This is an engineering decision-stability target. It is not a label for human
intent, policy correctness, factual truth, or whether the full-evidence action
was correct. Semantic expectation labels are never reused as sufficiency labels.

INT-2 demonstrated a retrieval-recall / decision-stability mismatch on the six
frozen engineering cases: annotated retrieval quality varied while the observed
downstream authorization actions remained stable across the evidence-bearing
conditions. That result motivates the sufficiency hypothesis. It does **not**
prove which evidence was necessary, that a learned sufficiency model will work,
or that any result generalizes beyond these six synthetic cases.

## Frozen source and full-evidence reference

INT-3A reads, without modifying:

- the six frozen Stage-B cases and their `ReplayScenario` values;
- the frozen query and relevance manifests;
- the eligible merchant evidence and catalog;
- Stage-A retrieval scores; and
- condition E (`PRODUCTION DEFAULT`) from the frozen Stage-B observations.

Condition E retrieved the complete eligible trusted-evidence set for each case.
Its already-recorded semantic behavior, final action, semantic input hash, model,
prompt version, and detector version become the full-evidence reference. INT-3A
does not recompute that result and does not make a semantic-model call.

## Subset plan

Every non-empty subset is enumerated in stable `(subset size, eligible order)`
order. A stable bit mask over eligible-evidence order forms the observation ID:

```text
INT3:<query_id>:m<eligible-order-bitmask>
```

The per-query plan sizes are:

| Query | Eligible evidence | Non-empty subsets |
| --- | ---: | ---: |
| `INT2-Q-STUDYGLOW` | 4 | 15 |
| `INT2-Q-NOTEBOOK` | 4 | 15 |
| `INT2-Q-STUDY-CLUB` | 4 | 15 |
| `INT2-Q-MARKET-EDGE` | 3 | 7 |
| `INT2-Q-TAX-GUIDE` | 3 | 7 |
| `INT2-Q-FLEXI` | 2 | 3 |
| **Total** |  | **62** |

Building a semantic request and hashing its canonical input is local input
construction, not semantic inference. Exact semantic-input equivalence classes
are recorded for future call deduplication. Every planned row retains null
`future_subset_observed_semantic_behavior`,
`future_subset_observed_final_action`, and `decision_stable` values.

INT-3A creates only:

```text
artifacts/engineering/int3/subset_plan.jsonl
```

`subset_results.jsonl` and `sufficiency_dataset.csv` are future artifact names;
they are intentionally absent because no live subset labels exist.

## Pre-inference features and leakage protection

All model features are available before subset semantic inference. The frozen
feature vector contains:

- evidence amount: count, eligible fraction, SKU-scoped count/fraction, and
  merchant/product scope presence;
- retrieval availability and generic max/mean/min/margin statistics;
- lexical, semantic, and hybrid max/mean/min/margin statistics;
- source-kind count and diversity;
- required-annotation and relevant-annotation fractions;
- constraint count and purpose/exclusion indicators;
- explicit one-hot case families (`PURPOSE_AND_EXCLUSION`, `EXCLUSION_ONLY`,
  `PURPOSE_ONLY`, and `OTHER`); and
- total and mean evidence text length in thousands of characters.

The generic score statistics intentionally equal the fixed INT-2 production
hybrid channel; explicit hybrid names are also retained so all requested score
channels are discoverable.

Target leakage is prevented in three layers:

1. `SubsetFeatureInput` has no field for a subset verdict, final action,
   engineering expectation, full-reference result, or target.
2. `FEATURE_NAMES` is frozen and checked against forbidden fields and leaky
   name fragments at import time.
3. Dataset provenance, features, and `decision_stable` are structurally
   separated. Model matrices contain only the feature mapping.

The reference action and semantic behavior are valid plan provenance, but they
can never enter the model feature vector.

## Leave-one-query-out evaluation

Future evaluation uses six-fold leave-one-query-out splitting. Each fold holds
out every subset from exactly one query and trains on every subset from the
other five queries. No random subset split is supported. The split validator
proves that train and test query sets are disjoint, the test set contains every
subset of its held-out query, and each fold partitions all rows exactly once.

This avoids leaking shared mandate, transaction, catalog, evidence text, and
query structure from one query's subsets into both train and test.

## Interpretable baseline

The baseline is scikit-learn L2-regularized logistic regression with `fit`,
`predict_proba`, and `predict`. Fitted coefficients and the intercept are stored
in immutable model state, and probability inference evaluates the logistic link
directly. INT-3A does not fit the model on real rows because all 62 targets are
null.

Logistic regression was selected instead of deep learning because the future
engineering dataset is small, coefficients are inspectable, probabilistic
outputs are suitable for calibration analysis, and additional model complexity
would not be justified by six query groups. This choice is a baseline design,
not evidence that the model will be calibrated or useful.

## Evaluation metrics

Future folds report:

- Brier score;
- false-SUFFICIENT count and the unsafe share of predicted-SUFFICIENT rows;
- false-SUFFICIENT rate among truly unstable rows and over all rows;
- false-INSUFFICIENT count and corresponding prediction-, class-, and
  whole-fold-denominator rates;
- review/escalation count and rate; and
- ROC-AUC only when both target classes exist in the fold.

Generic accuracy is deliberately absent. False-SUFFICIENT is the
safety-sensitive metric because it marks a subset predicted safe to decide on
that did not preserve the frozen full-evidence action.

## Expected-loss controller

For an explicit `p_sufficient` and engineering costs, the pure controller uses:

```text
L(DECIDE)        = (1 - p_sufficient) * C_UNSTABLE_DECISION
L(RETRIEVE_MORE) = C_RETRIEVE
L(REVIEW)        = C_REVIEW
```

It returns all three losses, the minimum-loss action, and the substituted
calculation as its reason. There is no unexplained probability threshold.
Equal losses use the explicit safety order `REVIEW`, `RETRIEVE_MORE`, `DECIDE`.

`DECIDE` means only “proceed to the existing semantic decision path.” It does
not mean authorization `ALLOW`.

## Counterfactual value of information

For every remaining eligible evidence item `e`, the VoI planner constructs the
pre-inference feature vector for `E ∪ {e}` and computes:

```text
delta_p = P(sufficient | E + e) - P(sufficient | E)
voi     = delta_p / acquisition_cost(e)
```

Costs must be finite and strictly positive. Candidates are ranked by descending
VoI, then probability gain, then frozen eligible order. The planner neither
fetches evidence nor invokes the semantic provider.

## Safety boundary

INT-3 has no `ALLOW` route and cannot override:

- Tier A/B `BLOCK`;
- Tier A/B `REVIEW`;
- the existing semantic verifier;
- execution capability and ledger checks; or
- the Razorpay execution gate.

A Tier A/B `BLOCK` or `REVIEW` is authoritative. After Tier A/B `ALLOW`, the
only learned-layer routes are `PROCEED_TO_SEMANTIC`, `RETRIEVE_MORE`, and
`REVIEW`. Payment execution remains outside INT-3.

## Offline synthetic demonstration

`scripts/run_int3_offline_demo.py` uses fixed synthetic probabilities and
costs. It demonstrates:

- A: high sufficiency -> `DECIDE`;
- B: low sufficiency plus valuable missing evidence -> `RETRIEVE_MORE`; and
- C: low sufficiency plus low-value expensive evidence -> `REVIEW`.

The demo makes zero semantic-provider, evidence-fetch, buyer, or Razorpay calls.

Generate the plan with Python 3.12 from the repository root (with `src` on the
Python import path):

```powershell
python scripts/generate_int3_subset_plan.py
```

## Future research path

If live subset execution later supplies defensible labels, follow-on work could
study calibration, active learning for selecting informative subset executions,
contextual bandits for cost-aware evidence acquisition, and constrained
reinforcement learning for sequential retrieval policies. None of active
learning, contextual bandits, or constrained RL is implemented in INT-3A.
