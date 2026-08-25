# Tier C development authoring plan (D8-B0)

Status: **frozen before the first Tier C development case is authored**.

Frozen D8-A infrastructure commit:
`daed88178d0ff0308a2dc44e9bf996ba8f1abe6b`

This artifact fixes the D8 development authoring procedure. It creates no
benchmark case, contains no semantic scenario, calls no detector or model, and
does not modify the frozen benchmark manifest.

## Registered development inventory

Tier C development contains exactly 220 human-adjudicated cases: 120 violation
and 100 benign.

| Provenance | Count |
| --- | ---: |
| `developer_authored` | 88 |
| `external_defensive_corpus_adapted` | 66 |
| `separate_model_adversarial` | 66 |
| **Total** | **220** |

| Development family | Violation | Benign | Total |
| --- | ---: | ---: | ---: |
| `C-DEV-RECURRENCE` | 40 | 34 | 74 |
| `C-DEV-EXCLUSION` | 40 | 33 | 73 |
| `C-DEV-PURPOSE` | 40 | 33 | 73 |
| **Total** | **120** | **100** | **220** |

These are final human-label quotas. The requested class used while authoring a
candidate is only authoring intent and never populates `ground_truth`.

## Held-out boundary

Before the D9 detector freeze, no `C-HOLD-*` content may be authored,
inspected, selected, shortlisted, adapted, or generated. This prohibition
includes source items and passages, candidate text, prompts containing held-out
examples, and model-generated held-out material. Only the already-public,
frozen held-out family definitions may be known.

## Provenance-specific authoring allocations

### Developer-authored: 88

| Family | Violation-intended | Benign-intended | Total |
| --- | ---: | ---: | ---: |
| `C-DEV-RECURRENCE` | 16 | 14 | 30 |
| `C-DEV-EXCLUSION` | 16 | 13 | 29 |
| `C-DEV-PURPOSE` | 16 | 13 | 29 |
| **Total** | **48** | **40** | **88** |

For `provenance=developer_authored`, the initial semantic scenario content must
originate directly from the benchmark author. Claude Code, Codex, or another
LLM must not generate the initial scenario.

AI may validate formatting, import author-supplied content, identify schema
errors, and perform deterministic tooling. AI-generated initial content keeps
`separate_model_adversarial` provenance even if a person later edits it
substantially. Provenance records origin, not the final editor.

### External defensive corpus adapted: 66

| Family | Violation-intended | Benign-intended | Total |
| --- | ---: | ---: | ---: |
| `C-DEV-RECURRENCE` | 12 | 10 | 22 |
| `C-DEV-EXCLUSION` | 12 | 10 | 22 |
| `C-DEV-PURPOSE` | 12 | 10 | 22 |
| **Total** | **36** | **30** | **66** |

The registered repositories, domains, intended family mappings, and permitted
adaptation purposes are in `development_sources.json`. That file is a source
registry only and may never contain an individual source item or passage.

Every adapted case records its actual source item/reference and a concise
adaptation description in the case's D8-A provenance record. Do not copy long
source text verbatim. Prompt-injection strings, injection-task payloads, and
reusable attack text are prohibited; only legitimate user-task, environment,
and policy semantics may be adapted.

The requested class is authoring intent only. Primary human adjudication
assigns ground truth. If that label differs from the requested class, the human
label wins and quota validation determines whether replacement or rebalancing
is required.

### Separate-model adversarial: 66

| Family | Violation-intended | Benign-intended | Total |
| --- | ---: | ---: | ---: |
| `C-DEV-RECURRENCE` | 12 | 10 | 22 |
| `C-DEV-EXCLUSION` | 12 | 10 | 22 |
| `C-DEV-PURPOSE` | 12 | 10 | 22 |
| **Total** | **36** | **30** | **66** |

The exact versioned instruction is
`prompts/separate_model_dev_v1.txt`; its byte digest is recorded beside it.
The model and isolation procedure are fixed in
`SEPARATE_MODEL_AUTHORING.md`. The model produces candidates only and does not
assign authoritative ground truth.

## External-source version pinning

Both registry entries begin with `selected_commit_sha=null` and
`source_selected_at=null`. No external case may pass the import finalization
gate while either field is null.

Before opening or adapting the first individual item from a registered source:

1. Work in an isolated source-review checkout outside the MandateGuard
   repository.
2. Choose a repository commit or an immutable release/tag, then resolve it to
   the commit object without opening an individual benchmark item:

   ```text
   git -C <source-repository> rev-parse <tag-or-commit>^{commit}
   git -C <source-repository> cat-file -e <resolved-sha>^{commit}
   ```

3. In `development_sources.json`, set `selected_commit_sha` to the exact
   lowercase resolved commit SHA and set `source_selected_at` to the UTC
   RFC 3339 instant at which that pinned version is first opened for item
   selection.
4. Commit that registry update before importing the first adapted case.
5. In every adapted case, set `provenance_origin.source_version` to the same
   pinned SHA, record the actual item/reference, and record the item-specific
   `source_selected_at` at or after the registry timestamp.

Once any case is adapted from a registry entry, its pin must not be silently
changed. A later upstream version requires a new registry entry and version
identifier. Existing cases retain their original version and audit history.
Never invent a SHA.

## Primary human adjudication

```text
candidate authoring/import
        ↓
shuffle adjudication queue
        ↓
hide detector output (none exists)
        ↓
prefer hiding provenance and authoring-intended class
        ↓
human reads mandate constraint and semantic evidence
        ↓
human assigns violation or benign
        ↓
record adjudicated_at
        ↓
compute and finalize case_content_sha256
```

The primary human label is authoritative ground truth, subject to required
second review and disagreement resolution. No authoring-intended class is
copied into `ground_truth`. No Tier C detector execution may occur before every
required label and content hash exists for the relevant development evaluation
set.

## Independent second-human review

The registered second review must be performed by a second independent human.
A model does not satisfy the requirement, and no reviewer identity is selected
or invented in D8-B0.

After the final primary labels and content hashes exist, run the frozen
deterministic selection over the actual finalized development snapshot. It
selects the lowest-ranked `ceil(25% × stratum_size)` cases independently within
each family × final ground-truth × provenance stratum. Every case marked
ambiguous by the primary reviewer is added. The registered complete 440-case
benchmark has an expected deterministic minimum of 120; the registered
development allocation would contribute 60, but the tooling must compute the
requirement from the actual finalized snapshot rather than assume either
number.

The second reviewer does not see the primary label or detector output and
independently assigns `violation` or `benign`. Disagreements follow the frozen
D8-A resolution or exclusion workflow. If resolution changes the final label
or digest, recompute selection over the new final pre-execution snapshot.

## D8-B staging

| Stage | Required outcome |
| --- | --- |
| B1 | Author the developer-origin candidates. |
| B2 | Pin registered source versions, then author external adaptations. |
| B3 | Pin the authoring model, then generate separate-model candidates in isolated sessions. |
| B4 | Shuffle and complete primary human adjudication of all 220 candidates. |
| B5 | Hash every finalized primary-labelled case. |
| B6 | Run frozen deterministic second-review selection. |
| B7 | Obtain independent second-human labels and resolve disagreements. |
| B8 | Complete exact, normalized-text, and manual dedup/diversity review. |
| B9 | Make the frozen `final_development` validator pass. |
| B10 | Append exactly 220 Tier C development metadata records to `MANIFEST.yaml`. |

Only after B1-B10 are complete may development detector evaluation begin. No
model selection or detector evaluation occurs inside authoring.

## D8-B0 zero-case guarantee

At this freeze point:

- committed Tier C cases = **0**;
- Tier C JSONL corpus files = **0**;
- Tier C detector executions = **0**;
- `benchmark/MANIFEST.yaml` case records = **1,008**, all frozen Tier A/B; and
- held-out source items inspected or selected = **0**.

Changing this plan, the source registry, the model registry, or a versioned
prompt after authoring begins requires an explicit, auditable successor
artifact. A used `separate_model_dev_v1.txt` is never edited in place; a prompt
change creates `separate_model_dev_v2.txt` and its own digest.
