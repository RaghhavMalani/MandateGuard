# INT-3 NON-BENCHMARK LEAVE-ONE-QUERY-OUT ENGINEERING EVALUATION

- Run ID: `sufficiency-loqo-20260831T143044Z-43f94887`
- Execution SHA-256: `4fb67d8be059d3354bde30684b98acea7199b82d8335ef95e0b22a73ffa1ca3c`
- Source artifact commit: `fdf9b6e95904792c9256b991324d7b93362c0aa7`
- Source subset run: `subset-live-recovery-20260831T135210Z-737beff7`
- Start: `2026-08-31T14:30:44.174094Z`
- End: `2026-08-31T14:31:07.091831Z`
- Observations: `62` (`35` stable / `27` unstable)
- Independent grouping unit: `6 synthetic queries`
- Classification threshold: `0.5`
- External API calls: `0`
- Buyer calls: `0`
- Razorpay calls: `0`

The target is single-execution action stability relative to the frozen full-evidence action. The 62 rows are correlated evidence subsets, not independent commerce cases. In this corpus every unstable subset ended in REVIEW; no instability was an ALLOW/BLOCK reversal.

## Three-approach comparison

| Approach | Pooled Brier | Macro-query Brier | False-SUFFICIENT | False-INSUFFICIENT | Predicted review rate | Pooled ROC-AUC |
|---|---:|---:|---:|---:|---:|---:|
| TRAIN-PREVALENCE | 0.247320 | 0.241847 | 27 | 0 | 0.000000 | 0.444444 |
| EVIDENCE-FRACTION-ONLY | 0.197671 | 0.192099 | 14 | 5 | 0.290323 | 0.743386 |
| FROZEN-14-FEATURE | 0.020001 | 0.055076 | 0 | 1 | 0.451613 | 0.984127 |

False-SUFFICIENT rate uses predicted-SUFFICIENT as its denominator. False-INSUFFICIENT rate uses predicted-INSUFFICIENT as its denominator. Overall and true-class-conditioned rates are retained in `fold_metrics.json`.

## Per-query results

| Held-out query | N | Stable/unstable | Full Brier | Full FS/FI | Full review | Full AUC | Fraction Brier | Fraction FS/FI | Fraction review | Fraction AUC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| INT2-Q-STUDYGLOW | 15 | 8/7 | 0.013158 | 0/0 | 0.466667 | 1.000000 | 0.201629 | 4/1 | 0.266667 | 0.741071 |
| INT2-Q-NOTEBOOK | 15 | 8/7 | 0.003256 | 0/0 | 0.466667 | 1.000000 | 0.201629 | 4/1 | 0.266667 | 0.741071 |
| INT2-Q-STUDY-CLUB | 15 | 8/7 | 0.003561 | 0/0 | 0.466667 | 1.000000 | 0.201629 | 4/1 | 0.266667 | 0.741071 |
| INT2-Q-MARKET-EDGE | 7 | 4/3 | 0.000116 | 0/0 | 0.428571 | 1.000000 | 0.192395 | 1/1 | 0.428571 | 0.750000 |
| INT2-Q-TAX-GUIDE | 7 | 4/3 | 0.002130 | 0/0 | 0.428571 | 1.000000 | 0.192395 | 1/1 | 0.428571 | 0.750000 |
| INT2-Q-FLEXI | 3 | 3/0 | 0.308236 | 0/1 | 0.333333 | NOT_DEFINED | 0.162918 | 0/0 | 0.000000 | NOT_DEFINED |

## False-SUFFICIENT cases

- None

## Standardized coefficient diagnostics

| Feature | Median coefficient | Sign consistency |
|---|---:|---|
| evidence_count | -0.003836 | MIXED_ACROSS_FOLDS |
| evidence_fraction | 0.580224 | POSITIVE_IN_ALL_6_FOLDS |
| sku_scoped_evidence_fraction | 0.983619 | POSITIVE_IN_ALL_6_FOLDS |
| merchant_scope_evidence_present | -0.231272 | NEGATIVE_IN_ALL_6_FOLDS |
| product_scope_evidence_present | 1.273832 | POSITIVE_IN_ALL_6_FOLDS |
| max_score | 0.893184 | POSITIVE_IN_ALL_6_FOLDS |
| mean_score | 0.796015 | POSITIVE_IN_ALL_6_FOLDS |
| score_margin | 0.395275 | POSITIVE_IN_ALL_6_FOLDS |
| source_kind_count | -0.179169 | MIXED_ACROSS_FOLDS |
| source_kind_diversity | -0.253664 | NEGATIVE_IN_ALL_6_FOLDS |
| constraint_count | 0.046965 | MIXED_ACROSS_FOLDS |
| constraint_family_purpose | 0.046965 | MIXED_ACROSS_FOLDS |
| constraint_family_exclusion | 0.000000 | ZERO_IN_ALL_6_FOLDS |
| evidence_text_kchars_mean | 0.506665 | POSITIVE_IN_ALL_6_FOLDS |

## Verdict

**USEFUL ADDITIONAL SIGNAL**

The classification compares both pooled and macro-query Brier scores, not one aggregate metric alone.
It also compares both false-SUFFICIENT and false-INSUFFICIENT counts at the frozen 0.5 threshold.
The evidence-fraction-only comparison was selected after descriptive subset-ablation inspection but before learned-model performance was seen.

No full-data diagnostic fit, threshold tuning, calibration, VoI evaluation, policy simulation, runtime integration, or alternative model search was performed.
