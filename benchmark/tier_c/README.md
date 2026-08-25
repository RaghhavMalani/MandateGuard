# Tier C benchmark infrastructure (D8-A)

**This milestone contains infrastructure only.** It builds the machinery needed
to author, adjudicate, hash, validate, freeze, and later execute Tier C
semantic benchmark cases. It contains no Tier C benchmark case content of any
kind.

## State at this commit

| Quantity | Count |
| --- | ---: |
| Tier C committed benchmark cases | **0** |
| Development cases authored | **0 / 220** |
| Held-out cases authored | **0 / 220** |
| Held-out source passages selected or inspected | **0** |
| Tier C detector executions | **0** |
| `benchmark/MANIFEST.yaml` case records | **1,008** (unchanged Tier A/B) |

No model provider was called. No `SemanticVerifier`, `authorize_transaction`,
or policy evaluation ran against any Tier C content, because no Tier C content
exists to run against.

This document is a description of infrastructure. It deliberately contains no
example case, no sample mandate text, and no sample semantic evidence, so that
reading it cannot contaminate later authoring.

## Authority

Three frozen artifacts control everything below and are unmodified by this
milestone:

- `TAXONOMY.md` — threat model, family definitions, held-out difficulty prediction.
- `benchmark/PROTOCOL.md` — sampling, adjudication, execution, reporting (Amendment A1 in force).
- `benchmark/MANIFEST.yaml` — case record schema, enums, hash policy, freeze policy.

Where this README explains a rule, the frozen artifact is the authority and the
section reference is given. Nothing here reinterprets them.

## Family allocation

Tier C is exactly 440 human-adjudicated cases: 240 violation, 200 benign
(PROTOCOL §2.3).

| Context | Family | Violation | Benign | Total |
| --- | --- | ---: | ---: | ---: |
| Development | `C-DEV-RECURRENCE` | 40 | 34 | 74 |
| Development | `C-DEV-EXCLUSION` | 40 | 33 | 73 |
| Development | `C-DEV-PURPOSE` | 40 | 33 | 73 |
| **Development subtotal** | | **120** | **100** | **220** |
| Held-out | `C-HOLD-BUNDLE` | 40 | 34 | 74 |
| Held-out | `C-HOLD-COMPATIBILITY` | 40 | 33 | 73 |
| Held-out | `C-HOLD-FULFILLMENT` | 40 | 33 | 73 |
| **Held-out subtotal** | | **120** | **100** | **220** |

`split` is a pure function of the family prefix and of nothing else
(PROTOCOL §2.4): `C-DEV-*` → `dev`, `C-HOLD-*` → `held_out`, regardless of
ground truth. `benign_control` is not used for Tier C in V1; it remains a
reserved manifest enum value and the validator rejects it for Tier C. A benign
held-out case is fully held out.

Generalization claims apply only to the Tier C held-out families.

These numbers live in code as `TIER_C_ALLOCATION` — validation constants only.
D8-A generates no case to fill them, and the validator never materializes a
placeholder to satisfy a quota.

## Provenance

Exactly one of three values per case, describing **origin**, never the final
editor (PROTOCOL §3.1.1). A case may not be relabelled `developer_authored`
because a developer later rewrote it. The type system enforces this: each
provenance value admits exactly one origin-metadata type, and the metadata is
part of hashed content, so changing provenance produces a new digest and a new
audit record rather than a silent edit.

| Provenance | Required metadata |
| --- | --- |
| `developer_authored` | authoring timestamp |
| `external_defensive_corpus_adapted` | authoring timestamp, source selection timestamp, source name, source reference, source version (if available), adaptation description |
| `separate_model_adversarial` | authoring timestamp, authoring model identifier, SHA-256 of the authoring prompt |

Per 40-case violation stratum: 16 / 12 / 12. Per 34-case benign stratum:
14 / 10 / 10. Per 33-case benign stratum: 13 / 10 / 10.

Registered totals, in the order developer authored / external defensive adapted
/ separate model adversarial:

| Scope | Developer | External | Separate model | Total |
| --- | ---: | ---: | ---: | ---: |
| Development violation | 48 | 36 | 36 | 120 |
| Development benign | 40 | 30 | 30 | 100 |
| **Development total** | **88** | **66** | **66** | **220** |
| Held-out violation | 48 | 36 | 36 | 120 |
| Held-out benign | 40 | 30 | 30 | 100 |
| **Held-out total** | **88** | **66** | **66** | **220** |
| **All Tier C** | **176** | **132** | **132** | **440** |

Each split is therefore **88 / 66 / 66**, and Tier C overall is
**176 / 132 / 132**, matching PROTOCOL §3.

The separate-model origin record has no field for the raw prompt, for provider
credentials, or for model reasoning traces, so none can be stored. Only the
prompt digest is retained, which is what the protocol requires.

## Adjudication

Ground truth is binary: `violation` or `benign`. `REVIEW` is a detector action
and abstention state, never a label (PROTOCOL §5). Every label is assigned by a
human who is not shown detector output; the adjudication record types have no
field capable of holding a detector action, semantic result, model response, or
score.

`ground_truth` is a **derived** property of the adjudication record rather than
a field stored beside it. A Tier C case therefore cannot carry a label that no
human assigned — `label_source=human_adjudication` is structural, not
declarative.

Lifecycle:

```
UNADJUDICATED → PRIMARY_LABELLED → DOUBLE_LABELLED → RESOLVED
                                 ↘ DISAGREEMENT ↗
                                 ↘ EXCLUDED
```

Invariants the validator enforces:

- an executable case has a final binary ground truth;
- an unresolved disagreement cannot execute;
- an excluded case cannot execute, and its `case_id` is retired permanently and
  never reused;
- resolution happens before detector execution, from case content alone; and
- after `first_run_at` is non-null, `ground_truth`, `family_id`, `split`,
  `provenance`, `evaluation_inputs`, `semantic_evidence`,
  `case_content_sha256`, **and the provenance-origin audit timestamps** may
  never change.

### Post-first-run immutability covers two distinct things

**Content immutability** covers everything bound by `case_content_sha256`. Any
change there is already detectable as a digest change.

**Audit immutability** covers the `provenance_origin` fields that are
deliberately *not* hashed — `authored_at` for every provenance, plus
`source_selected_at` for `external_defensive_corpus_adapted`. These stay out of
the digest because they are audit metadata rather than benchmark content, and
the fix for this does **not** move them into the digest.

They are protected separately because they are the mechanical evidence the
held-out isolation audit reads (PROTOCOL §3.1, §7.1). Without that protection,
an already-executed held-out case could have its authoring or source-selection
timestamp rewritten *after* results were observed, retro-fitting the isolation
guard while the content digest stayed perfectly valid.

The audit projection is defined as the **structural complement** of the content
projection over each origin type's declared fields, not as a hand-written list.
Every origin field is therefore in exactly one of the two projections, so a
field added to an origin type in future cannot silently escape both the digest
and the audit check.

"Ambiguous" is a flag the primary adjudicator sets; it is never a final
ground-truth value. Its only effect is to force a second review.

Replacement (PROTOCOL §5.2): an excluded case stays in audit history with its
reason and timestamp; the replacement gets a **new** `case_id`, comes from the
identical family × ground-truth × provenance stratum, and is independently
adjudicated and hashed.

## Content hashing

`case_content_sha256` is the SHA-256 of MandateGuard canonical JSON (UTF-8,
sorted keys, no floats, no insignificant whitespace) over exactly:

```
case_schema_version, evidence_tier, family_id, provenance,
provenance_origin, split, ground_truth, label_source, evaluation_inputs
```

where `evaluation_inputs` carries the mandate, transaction, catalog snapshot,
server time, nonce state, PSP committed hashes, replay seed, evaluated-at
timestamp, and the trusted semantic evidence.

Excluded as audit-only: `case_id`, the digest itself, `label_recorded_at`,
`first_run_at`, adjudicator identities, review timestamps, authoring and
source-selection timestamps, the exclusion record, and every later execution
value.

**Relationship to the frozen policy.** `MANIFEST.yaml`
`field_rules.case_content_sha256` requires the digest to cover *evaluation
inputs, `family_id`, `evidence_tier`, `provenance`, `split`, `ground_truth`,
`label_source`*, and to exclude the audit-only `first_run_at`; PROTOCOL §6
states the same. All seven are included here, and the single explicit exclusion
is honoured. Two components go beyond that enumeration, both deliberately:

- `case_schema_version`, which binds the schema the content is read under. The
  frozen, already-executed Tier A/B projection in `mandateguard.benchmark.codec`
  includes it, so this follows existing repository convention rather than
  inventing one.
- `provenance_origin` (excluding its timestamps), which binds the source
  identity and adaptation, or the authoring model and prompt digest. PROTOCOL
  §3.1.1 makes provenance hashed content and requires that changing it produce a
  new digest; binding only the one-word provenance label while leaving the
  claimed source unhashed would let the source be swapped silently.

The manifest enumeration is treated as a required floor with one explicit
exclusion, never as licence to drop a field. Nothing the frozen policy requires
hashed is excluded.

Semantic evidence sits inside `evaluation_inputs` because it is literally an
authorization input to the frozen D5 verifier, which is where the manifest
already places it.

## Second-review selection

Deterministic and non-discretionary (PROTOCOL §5.1):

```
second_review_rank = SHA256(UTF8("mandateguard-second-review-v1") || UTF8(case_content_sha256))
```

Within each `family_id` × `ground_truth` × `provenance` stratum, sort ascending
by rank and select the lowest `ceil(0.25 × stratum_size)`. Every case the
primary adjudicator marked ambiguous is second-reviewed in addition; a case in
both sets counts once. Ties on rank break by ascending `case_id` — that requires
a SHA-256 collision and carries no quality claim, it exists only so the function
is total.

Selection reads six values per case and nothing else: case ID, family, ground
truth, provenance, content digest, ambiguity flag. It has no access to detector
behavior, detector output, or any benchmark result.

### Lifecycle ordering

`ground_truth` is hashed content, so the ordering is:

```
author → primary adjudication → record label → compute digest
      → compute deterministic selection → second review
      → resolve disagreements → if the final label changed, the digest changes
```

A resolution that flips the label produces a new digest under PROTOCOL §6, which
moves the rank and can move the stratum. This is **not** circular, and it is
resolved conservatively rather than conveniently:

- selection is never cached; it is a pure function recomputed from whatever
  corpus snapshot it is given; and
- the execution gate requires the selection computed over the **final
  pre-execution corpus state** to be fully covered (PROTOCOL §17, whose gate is
  stated over the case's stratum at execution time).

Relative to the earlier snapshot this can only ever *add* a required second
review, never drop one — exactly what §5.1 demands when it says the stratified
minimum "is a lower bound and never a target: no required second review is
skipped because some total has already been reached". Reviews already performed
against an earlier snapshot remain valid and are retained. Because every second
review happens before execution and without detector output, recomputation
cannot be influenced by results.

## Duplicate and near-duplicate review

PROTOCOL §4.1 requires this review before first execution on a split, and
explicitly does not require an embedding system for V1. None is implemented:
there is no embedding, no model call, and no learned similarity anywhere.

Three layers:

1. **Exact canonical duplicate detection** over canonical case content and
   `case_content_sha256` — mandatory, and a hard validation failure.
2. **Normalized-text comparison** under a fixed, documented criterion: Unicode
   NFKC, casefold, replace every non-alphanumeric character with a space,
   collapse whitespace. Identical normalized text is a hard failure.
3. **Manual near-duplicate review** within each stratum. The tooling produces
   *candidate pairs* — same family and ground truth, token-set Jaccard overlap
   at or above 9/10, computed in exact integer arithmetic so no float enters the
   decision. A candidate is a prompt for a human to look at, never an automatic
   declaration that two cases are semantic duplicates.

The report structure is `exact_duplicate_groups`,
`normalized_text_duplicate_groups`, `manual_review_candidates`.

## Case IDs

Format: `CDEV-REC-001`, `CDEV-EXC-001`, `CDEV-PUR-001`, `CHOLD-BUN-001`,
`CHOLD-CMP-001`, `CHOLD-FUL-001`.

Ground truth is deliberately **not** encoded in the ID. The Tier A/B corpus uses
a class segment (`A1-V-001`) because those labels are mechanically derived from
an invariant at generation time. Tier C labels are adjudicated *after* the case
is authored, and a disagreement resolution may change one. An ID asserting a
label that adjudication later contradicts would be actively misleading, and
because retired IDs are never reused it could not simply be rewritten. A
label-neutral ID stays correct across the whole adjudication and replacement
lifecycle.

The format is validated syntactically. No actual ID is issued at D8-A.

## Held-out guards

The held-out family definitions are public and frozen, so guardrails are
implemented now. No held-out content is created, and no held-out source has
been selected or inspected.

Implemented as validation and workflow guards only:

- a `C-HOLD-*` case must have `split=held_out`; nothing can move it out;
- **source isolation audit** — for held-out cases, authoring timestamp must be
  at or after detector freeze, and for externally adapted cases the source
  *selection* timestamp must be too, since PROTOCOL §3.1 makes reading the
  future held-out passages contamination even if no case file is ever written.
  This is an audit over recorded timestamps, not proof of human behavior, and it
  is described as such;
- **batch finalization** — the checkpoint validator requires all four PROTOCOL
  §7.2 counts to be exactly 220 (total, ground truth recorded, content hash
  recorded, `first_run_at == null`), and cross-checks each against the actual
  corpus rather than trusting the declared number. The counts are over
  *distinct* `case_id`s, so a duplicated ID cannot pad a stratum to 220; and
- **no partial execution** — the gate has no per-case variant. A pilot, smoke,
  sanity, or calibration subset is unreachable through this API by construction.

The checkpoint is a **complete standalone gate**. It does not assume the caller
already ran the corpus validator: it runs the full `held_out_final` validation
itself and then adds the declared-count checks. A caller cannot slip a
malformed 220-record set past it — a duplicate `case_id`, a duplicate content
digest, a wrong family or provenance quota, an unresolved adjudication, a
missing label or hash, a wrong split, or a non-null `first_run_at` — merely by
invoking the checkpoint directly. Correctness does not depend on undocumented
call ordering. Omitting the detector freeze timestamp makes the gate report
`MISSING_DETECTOR_FREEZE` rather than silently skipping the isolation audit.

Delegating this way is safe and non-recursive: `validate_tier_c_corpus` never
invokes checkpoint logic, so the dependency runs strictly one way, and a test
asserts that it stays that way.

Held-out execution itself is not implemented at D8-A and belongs to D10.

## Development corpus validator

`validate_tier_c_corpus(cases, mode, ...)` reports invalid family, incorrect
split, wrong evidence tier, invalid provenance metadata, missing semantic
constraints, incomplete evaluation inputs, missing primary label, unresolved
disagreement, duplicate case ID, duplicate content hash, quota excess,
incorrect provenance strata, non-null `first_run_at` before first execution,
label/hash mismatch, incomplete second review, retired-ID reuse, exact and
normalized duplicates, and held-out isolation failures.

| Mode | Meaning |
| --- | --- |
| `partial_development` | Valid while authoring. Quotas may be incomplete but may never be exceeded. **An empty corpus is valid** — that is this commit's state. |
| `final_development` | Exactly 220 development cases, every quota exact, every label recorded, every disagreement resolved, required second reviews complete. |
| `held_out_final` | The same for the 220 held-out cases, plus the source-isolation audit. Used at or before D10. |

## Storage layout

Intended layout, **not created and not populated** at D8-A:

```
benchmark/cases/tier_c/dev/recurrence.jsonl
benchmark/cases/tier_c/dev/exclusion.jsonl
benchmark/cases/tier_c/dev/purpose.jsonl
benchmark/cases/tier_c/held_out/bundle.jsonl
benchmark/cases/tier_c/held_out/compatibility.jsonl
benchmark/cases/tier_c/held_out/fulfillment.jsonl
```

No empty `.jsonl` file is committed: an empty `recurrence.jsonl` is
indistinguishable from a finalized corpus that happens to hold no cases. The
loader treats an absent directory as zero authored cases, which is an accurate
description of the current state.

`benchmark/MANIFEST.yaml` is not modified. Tier C manifest records are produced
by pure helpers that carry exactly the ten frozen `required_fields` and omit the
optional `expected_action`, as the frozen `field_rules` require for Tier C. The
manifest schema is not extended; Tier C metadata beyond those ten fields lives
in the corpus records and audit files.

## Detector isolation

No module under `src/mandateguard/benchmark/tier_c/` imports
`mandateguard.policy`, `mandateguard.semantic`, `mandateguard.execution`, or
`mandateguard.replay`. Authoring and adjudication infrastructure must not be
able to consult a detector, and this is enforced both by a static source check
and by a subprocess import check in the test suite.

One consequence is visible in the code: the Tier C semantic-evidence records
mirror the frozen D5 `SemanticEvidenceBundle` and `SemanticEvidenceEntry`
field-for-field instead of importing them, because the frozen
`mandateguard.semantic` package `__init__` eagerly re-exports `SemanticVerifier`,
`authorize_transaction`, and the OpenAI adapter — importing the evidence model
at all would load the entire detector. This follows the precedent already frozen
in `mandateguard.benchmark.models`, whose `EvaluationInputs` mirrors
`replay.scenario.ReplayScenario` for exactly the same reason. The mirror is
exact and is pinned against the frozen D5 classes by a test that inspects them
in a subprocess, so it cannot silently drift.

## Tooling

```bash
python scripts/validate_tier_c_corpus.py --split dev --mode partial_development
```

```bash
python scripts/select_tier_c_second_review.py --split dev
```

```bash
python scripts/import_tier_c_case.py proposed_case.json
```

The importer validates one proposed case, computes its canonical digest, and
refuses duplicate IDs and duplicate content. It never calls a detector, never
calls a model, and never assigns a ground truth: an unadjudicated case is
rejected, not labelled. There is no automatic case authoring anywhere in this
milestone.

## Next: D8-B

D8-B authors the 220 **development** cases only:

1. select development external-corpus sources (permitted before freeze;
   a source used for a development case may never be reused held-out);
2. author development cases across the three provenances to the registered
   strata;
3. human-adjudicate each without detector output;
4. compute content digests;
5. run the deterministic second-review selection and perform those reviews;
6. resolve disagreements, exclude and replace where unresolvable; and
7. complete and record the §4.1 duplicate and near-duplicate review, then append
   the 220 development records to `benchmark/MANIFEST.yaml`.

Held-out authoring does not begin until after detector freeze at the end of D9,
and held-out execution is a single closed batch at D10.
