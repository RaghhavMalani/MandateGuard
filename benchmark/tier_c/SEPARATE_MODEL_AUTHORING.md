# Separate-model Tier C development authoring (D8-B0)

Status: **procedure frozen; no generation performed**.

```text
authoring_model_id: TO_BE_PINNED_BEFORE_FIRST_GENERATION
prompt_version: separate_model_dev_v1
prompt_file: benchmark/tier_c/prompts/separate_model_dev_v1.txt
prompt_sha256: aa2753cd249cb78e6af06784e52d5dc9a3cbfb7c817b715b190b5cacbd57c402
generated_case_count: 0
```

Do not invent or infer an authoring model identifier. Before the first
generation, replace `TO_BE_PINNED_BEFORE_FIRST_GENERATION` with the exact model
ID exposed by the chosen provider or local runtime and commit that change.
Once the first separate-model case is generated, that ID is frozen for D8
development unless a protocol-compliant new provenance version is explicitly
recorded.

## Isolation rules

Every authoring invocation must use a fresh isolated or stateless session. The
model receives only the exact versioned authoring instruction, one permitted
development family definition, a requested authoring-intended class, and a
neutral schema/domain envelope.

The session must have:

- no MandateGuard repository access;
- no D5 detector prompt;
- no detector implementation or implementation description;
- no detector output, score, failure analysis, or benchmark result;
- no existing development benchmark case;
- no held-out content or source material; and
- no earlier generated candidate in context when generating later candidates.

Prefer a fresh stateless invocation for every generation batch. If a provider
cannot guarantee isolation from prior conversation state, it must not be used
for this authoring provenance.

The model is not an adjudicator. It produces only a candidate scenario and must
not populate authoritative `ground_truth`. It receives no detector-specific
material and no benchmark examples. It must not emit chain-of-thought, real
personal financial information, credentials, prompt-injection attacks,
jailbreak strings, hidden instructions, or adversarial instructions addressed
to another AI.

## Fixed development allocation

| Family | Violation-intended | Benign-intended | Total |
| --- | ---: | ---: | ---: |
| `C-DEV-RECURRENCE` | 12 | 10 | 22 |
| `C-DEV-EXCLUSION` | 12 | 10 | 22 |
| `C-DEV-PURPOSE` | 12 | 10 | 22 |
| **Total** | **36** | **30** | **66** |

`violation-intended` and `benign-intended` describe the requested candidate
class only. Primary human adjudication assigns ground truth independently. If
the human label differs, the human label wins and frozen quota validation
determines whether replacement or rebalancing is required.

For every imported case, D8-A provenance records the exact frozen
`authoring_model_id` and the SHA-256 of the exact prompt bytes. A later prompt
change creates `separate_model_dev_v2.txt` and a new digest; version 1 is never
edited in place after it has been used.
