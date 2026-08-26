# Developer-authored Tier C candidate capture

This directory is authoring workspace, not the finalized Tier C corpus. Its TSV
rows do not count as benchmark cases, are not loaded by the Tier C corpus
loader, and must not be added to `benchmark/MANIFEST.yaml`.

## Semantic authorship boundary

Initial `semantic_constraint_text` and `semantic_evidence_text` must originate
from the human benchmark author. The capture tool does not generate,
paraphrase, autocomplete, trim, or otherwise transform either field. If AI
generated the original semantic content, do not use this workflow: the case
belongs to `separate_model_adversarial` provenance even if a human later edits
it.

`authoring_intent` is allocation metadata for the drafting stage only. It does
not become `ground_truth` and is deliberately kept outside the Tier C case
model. Ground truth is added later by independent human adjudication.

## Worksheet

Edit `developer_candidates.tsv` as UTF-8 TSV. It contains all 88 neutral IDs,
their fixed development families, the frozen authoring-intent allocation, and
blank semantic fields. A row with two blank semantic fields is scaffolding, not
a candidate. Once work starts on a row, both semantic fields are required.

Partial validation permits entirely blank or missing rows while authoring:

```bash
python scripts/capture_developer_tier_c_candidates.py
```

The command writes complete candidates to stdout and a status summary to
stderr. To create a separate candidate JSONL artifact, supply a new output path
under this authoring directory:

```bash
python scripts/capture_developer_tier_c_candidates.py \
  --output benchmark/tier_c/authoring/dev/developer_candidate_drafts.jsonl
```

The tool refuses to overwrite an existing output file. It forces
`provenance=developer_authored`, records `authored_at` at capture time, installs
the deterministic clean payment envelope, and creates an unadjudicated frozen
Tier C object. Candidate serialization contains neither `ground_truth` nor
`case_content_sha256`.

Final-candidate mode requires all 88 rows, all semantic fields, and the exact
16/14, 16/13, and 16/13 intended allocations:

```bash
python scripts/capture_developer_tier_c_candidates.py \
  --mode final_candidates \
  --output benchmark/tier_c/authoring/dev/developer_candidate_drafts.jsonl
```

No candidate output is committed at this tooling milestone. The worksheet's
semantic fields remain blank.
