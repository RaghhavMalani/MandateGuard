# Product classifier, mismatch signal, and proposal analytics

Three advisory layers, each measured, each with a stated boundary it may not
cross. One of them was rejected by its own evaluation and is not shipped.

---

## 1. Supervised product category classifier

### Task

Predict a listing's top-level category from its **title + description**.

### Protocol

* **Baseline first.** TF-IDF (sublinear term frequency, L2, `min_df=3`,
  `max_features=20,000`) over the shared analyzer, then a linear classifier.
  Two candidates — `LinearSVC` and `LogisticRegression` — selected on the
  **validation** partition by macro F1.
* **Grouped by product family.** The unit assigned to a partition is a product
  family, not a row. A marketplace crawl lists one product many times — across
  sizes, colours, sellers, and re-postings — so a row-wise split puts a listing's
  near-identical twin in the training set and the resulting test score is partly a
  memorization score. Membership is
  `sha256(grouped_split_version | family_key)` thresholded within each label, and
  `family_key` is frozen under `discovery-product-family-v1`: it lowercases the
  title, strips variant tokens (colour, size, pack) and bare digits, and never
  merges across brands. 11,662 families over
  17,230 labelled rows.
* **Split frozen before any test evaluation.** `freeze_split` writes a manifest
  with a digest of the test-set ids, and the trainer **refuses to report test
  metrics** if that digest has since moved.
* **Test partition scored once**, after model selection was settled.
* **The row-wise split is refit and rescored too**, so the earlier claim stays
  visible and comparable instead of being silently replaced.

### Label set

22 classes. Two exclusions, both applied before the split and both recorded in
the artifact:

* `Uncategorized` — rows the importer could not place in the source taxonomy.
  Teaching it as a real class would teach the model to predict "I don't know" as
  a category.
* Labels with fewer than 40 examples — 472 rows in total across both rules.

### Results (grouped product-family test partition, 2,539 rows)

This is the headline, and it is the model that ships.

| Metric | Value |
| --- | ---: |
| Accuracy | **0.9756** |
| Macro F1 | **0.9444** |
| Weighted F1 | **0.9754** |
| Top-2 accuracy | 0.9905 |
| Top-3 accuracy | 0.9923 |
| Selected model | `LinearSVC` |

Split sizes: train 12,140 · validation
2,551 · test 2,539
(17,230 labelled rows,
472 dropped). Families:
train 8,163 · validation
1,751 · test 1,748.
Test-id digest `999f842b4faef1d6…`.
Validation macro F1 was 0.9198, so the test figure is not a
lucky draw.

#### The row-wise result, kept for comparison

A hostile review found near-duplicate product families crossing the original
row-wise partitions and expected the metric to fall once that path was closed.
It did not, materially:

| Split | Accuracy | Macro F1 | Weighted F1 |
| --- | ---: | ---: | ---: |
| Grouped product family (**quoted**) | 0.9756 | **0.9444** | 0.9754 |
| Row-wise (original) | 0.9749 | 0.9436 | 0.9747 |

The difference is +0.0008 macro F1 — the grouped number is very slightly
*higher*, which is within the noise of two refits on partitions of this size. The
honest reading is not "the leak did not exist"; it is that on this corpus the
category signal lives in ordinary product vocabulary that generalizes across
families, so removing the twins cost the model almost nothing. The grouped number
is the one quoted because it is the one whose construction supports the claim,
not because it is the larger of the two.

### Per-category support, honestly

Macro F1 is dragged down by exactly the categories you would expect — the ones
a marketplace taxonomy conflates:

| Weakest | F1 | Precision | Recall | Test n |
| --- | ---: | ---: | ---: | ---: |
| Pens & Stationery | 0.800 | 0.882 | 0.732 | 41 |
| Baby Care | 0.809 | 0.826 | 0.792 | 48 |
| Sports & Fitness | 0.857 | 0.875 | 0.840 | 25 |
| Toys & School Supplies | 0.869 | 0.827 | 0.915 | 47 |

Three categories score a perfect 1.000, and two of those have **6 and 7 test
examples**. A perfect score on seven items is not evidence of a perfect
classifier, and the confusion matrix
([`data/models/category_confusion.json`](../data/models/category_confusion.json))
is published so that is checkable rather than takeable on trust.

### What it may not do

The classifier is **advisory**. Its prediction cannot allow a payment, cannot
override a deterministic `BLOCK`, and cannot substitute for missing trusted
evidence. `CategoryPrediction.to_mapping()` carries
`authorization_authority: "NONE"`, and `as_signal().authorize()` raises rather
than returning a falsy value — so a caller who routes a model score into an
authorization decision fails loudly at the call site instead of quietly reading
zero.

It is also **not asked** about registered merchant products. The merchant is the
authority on its own catalog's shelf; asking a model trained on one
marketplace's taxonomy to adjudicate that would be a category error.

---

## 2. Listing / classifier mismatch signal

A listing that files itself under *Education* while its own text reads like
*Trading / Betting* has told us something. What it has told us is that this
listing deserves a closer look.

### Severity, and why it is not simply "disagreement"

| Severity | When |
| --- | --- |
| `NONE` | The claim matches the model's reading, or the listing carries no text the model recognizes |
| `LOW` | The pair is one a marketplace routinely conflates (Clothing/Footwear, Computers/Mobiles, …), or the listing's own claim is in the model's top 3 |
| `MEDIUM` | Genuine disagreement without a decisive margin |
| `HIGH` | Genuine disagreement, decisive margin, or a prediction in a category where a wrong claim has a money consequence |

The "in the model's top 3" rule is only applied when the taxonomy has more than
six classes. On a small one, every label is near the top, and applying the rule
there would quietly disable the signal.

### Permitted and forbidden effects

A mismatch **may**: raise investigation priority, trigger acquisition of
additional trusted evidence, surface `REVIEW`.

A mismatch **may not**, at any severity or confidence: authorize a payment,
override a deterministic `BLOCK`, or satisfy missing trusted evidence. Both
lists ship in the signal's own payload.

---

## 3. Proposal anomaly analytics

Eleven deterministic features, each answering one question a human reviewer
would ask:

| Feature | Question |
| --- | --- |
| `price_vs_category` | Is this priced like other products in its category? |
| `price_changed_after_authorization` | Did the amount move after we authorized it? |
| `category_listing_mismatch` | Does the declared category match its own text? |
| `title_description_mismatch` | Does the description describe the product in the title? |
| `sku_semantic_mismatch` | Does the identifier match the product it claims to be? |
| `merchant_mismatch` | Is the seller the one the mandate expects? |
| `stale_evidence` | Is the evidence recent enough to still be true? |
| `recurrence_cues` | Does the text hint at a recurring charge? |
| `missing_trusted_evidence` | Is there any authoritative evidence at all? |
| `consent_state` | Is consent currently active for this mandate? |
| `replay_attempt` | Has this exact authorization been presented before? |

Price comparison uses **median and interquartile fences**, not a mean, so a
handful of ₹5-lakh listings cannot drag the notion of "normal". A category with
fewer than eight priced listings declines to judge the price at all — three
listings is not a distribution.

Weights are **ordinal, not calibrated probabilities**. They order what a reviewer
looks at first. They never cross a threshold that permits a payment: the output
is `INVESTIGATION_PRIORITY_ONLY`.

---

## 4. Was a learned anomaly detector worth adding?

The brief was explicit: do not add IsolationForest so a README can say "ML". So
the question was asked properly.

### Protocol

600 proposals built from the real catalog, half ordinary and half
carrying one of eight named injected defects.

The ordinary rows are split deterministically into two disjoint controls: an
**ordinary training control** (151 rows) and an
**ordinary evaluation control** (149 rows). Every
metric below is scored on the same held-out population — the evaluation control
plus every defective row, 449 rows in all.

The **baseline** is the deterministic scorer, no fitting. The **candidate** is an
unsupervised `IsolationForest` (200 trees) fitted on the *training* control only.
Both scored by ROC AUC, average precision, and recall at 5% FPR. The candidate
ships only if it improves ROC AUC by more than 0.02.

> **Corrected.** An earlier revision fitted the forest on *all* ordinary rows and
> then scored those same rows. That gave the candidate an in-sample advantage on
> exactly the negatives it was judged against, so the comparison was not a
> comparison. The split above is the fix. The verdict did not change.

### Result: rejected

Scored on the 449 held-out rows.

| Model | ROC AUC | Average precision | Recall @ 5% FPR |
| --- | ---: | ---: | ---: |
| Deterministic baseline | **0.9913** | 0.9955 | 0.9733 |
| IsolationForest | 0.5362 | 0.7001 | 0.0233 |

ΔROC AUC = **-0.4552**. **`KEEP_DETERMINISTIC_BASELINE`.**
The learned detector is not shipped.

The result is not surprising in hindsight, and the reason is worth writing down:
an unsupervised detector over these features has no idea which directions
matter. It sees "consent revoked" as just another coordinate that moved. When
you already know what a defect looks like, an anomaly detector is the wrong
tool.

### The circularity problem, and what was done about it

Seven of the eight defect classes flip a field the deterministic features
already watch. The baseline scoring 0.994 on those proves **the features fire**,
and nothing more. It would be dishonest to present that as evidence the
analytics generalizes.

So the set contains one defect no rule-based field comparison can see:
`CATEGORY_LAUNDERED`. The listing keeps its declared category while its **title
and description** are replaced with another category's. Every structured field
is untouched — brand, category, price, currency, merchant, and every identifier.
Only the trained classifier's disagreement can catch it.

> **Corrected.** An earlier revision also replaced the donor's **brand**, which
> quietly contradicted the "every structured field stays untouched" claim and
> handed the deterministic features something to notice. Brand is now left alone,
> and the numbers below are from the corrected harness.

### On that non-circular case, the supervised model does earn its place

186 cases (149 held-out ordinary
controls + 37 laundered):

| Scorer | ROC AUC | Average precision | Recall @ 5% FPR |
| --- | ---: | ---: | ---: |
| Deterministic features, classifier signal **ablated** | 0.4272 | 0.1686 | 0.000 |
| Deterministic features **+ classifier mismatch** | **0.9497** | 0.7707 | 0.784 |

ΔROC AUC = **+0.5225**.
`CLASSIFIER_SIGNAL_ADDS_DETECTION_VALUE`.

Without the classifier the detector is at chance — 0.4272 is *worse*
than a coin flip, because the laundered listings look ordinary on every field the
rules inspect. This is the one place in this change where a trained model does
something the deterministic layer demonstrably cannot, and it is why the
classifier is kept.

It is kept as an **advisory** signal, and the boundary is not rhetorical. This
signal may raise `REVIEW` or open an investigation. It cannot `ALLOW`, cannot
issue a capability, cannot override the controller or a revocation, and cannot
create trusted evidence. This detector is advisory. It may raise REVIEW or open an investigation. It cannot ALLOW, cannot issue a capability, cannot override the controller or a revocation, and cannot create trusted evidence.

### Scope limit

Defects are injected by the harness. A detector that finds them is **not**
thereby shown to find fraud in production traffic. These are synthetic
engineering scenarios, not merchant traffic.

Full report:
[`artifacts/engineering/discovery/anomaly_evaluation.json`](../artifacts/engineering/discovery/anomaly_evaluation.json).

---

## Reproducing

```bash
pip install -r requirements-train.txt
python scripts/train_discovery_models.py
python scripts/evaluate_discovery_anomaly.py
```
