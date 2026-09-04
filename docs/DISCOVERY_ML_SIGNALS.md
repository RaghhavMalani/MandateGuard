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
* **Split frozen before any test evaluation.** Membership is
  `sha256(split_version | catalog_product_id)` thresholded within each label, so
  it does not depend on row order, on a RNG seed surviving a refactor, or on a
  saved file. `freeze_split` writes a manifest with a digest of the test-set
  ids, and the trainer **refuses to report test metrics** if that digest has
  since moved.
* **Test partition scored once**, after model selection was settled.

### Label set

22 classes. Two exclusions, both applied before the split and both recorded in
the artifact:

* `Uncategorized` — rows the importer could not place in the source taxonomy.
  Teaching it as a real class would teach the model to predict "I don't know" as
  a category.
* Labels with fewer than 40 examples — 472 rows in total across both rules.

### Results (frozen test partition, 2,586 rows)

| Metric | Value |
| --- | ---: |
| Accuracy | **0.9749** |
| Macro F1 | **0.9436** |
| Weighted F1 | **0.9747** |
| Top-2 accuracy | 0.9896 |
| Top-3 accuracy | 0.9923 |
| Selected model | `LinearSVC` |

Split sizes: train 12,061 · validation 2,583 · test 2,586 (17,230 labelled rows).
Validation macro F1 was 0.9398, so the test figure is not a lucky draw.

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

600 proposals built from the real catalog, half ordinary and half carrying one
of eight named injected defects. The **baseline** is the deterministic scorer,
no fitting. The **candidate** is an unsupervised `IsolationForest` (200 trees)
fitted on the ordinary half only, scoring the same feature vectors. Both scored
by ROC AUC, average precision, and recall at 5% FPR. The candidate ships only if
it improves ROC AUC by more than 0.02.

### Result: rejected

| Model | ROC AUC | Average precision | Recall @ 5% FPR |
| --- | ---: | ---: | ---: |
| Deterministic baseline | **0.9942** | 0.9942 | 0.9767 |
| IsolationForest | 0.5618 | 0.5831 | 0.1433 |

ΔROC AUC = **−0.4324**. **`KEEP_DETERMINISTIC_BASELINE`.** The learned detector
is not shipped.

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
`CATEGORY_LAUNDERED`. The listing keeps its declared category while its text is
replaced with another category's. Every structured field is untouched. Only the
trained classifier's disagreement can catch it.

### On that non-circular case, the supervised model does earn its place

337 cases (300 ordinary + 37 laundered):

| Scorer | ROC AUC | Average precision | Recall @ 5% FPR |
| --- | ---: | ---: | ---: |
| Deterministic features, classifier signal **ablated** | 0.4519 | 0.0993 | 0.000 |
| Deterministic features **+ classifier mismatch** | **0.9647** | 0.7332 | 0.811 |

ΔROC AUC = **+0.5128**. `CLASSIFIER_SIGNAL_ADDS_DETECTION_VALUE`.

Without the classifier the detector is at chance — 0.4519 is *worse* than a coin
flip, because the laundered listings look ordinary on every field the rules
inspect. This is the one place in this change where a trained model does
something the deterministic layer demonstrably cannot, and it is why the
classifier is kept.

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
