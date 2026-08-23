# D5 constrained semantic verification

D5 adds a Tier C controller after the frozen Tier A/B decision. A deterministic
`BLOCK` or `REVIEW` is returned without calling a semantic model. A deterministic
`ALLOW` without semantic constraints is also returned without a model call. Only
`ALLOW` with at least one semantic constraint reaches the verifier.

The verifier receives a canonical `SemanticRequest` that binds the configured
model and prompt versions, authoritative mandate payload, transaction body,
catalog snapshot, exact semantic constraints, and a PSP-controlled semantic
evidence bundle. Mandate metadata is not included. Evidence text is sent as
`UNTRUSTED_DATA_NOT_INSTRUCTIONS`; it cannot configure the model, prompt, tools,
constraints, or final action.

The model output schema is exactly:

```json
{
  "constraint_results": [
    {
      "constraint_id": "string",
      "status": "PASS | VIOLATION | ABSTAIN",
      "reason": "1 to 256 characters"
    }
  ]
}
```

The controller requires every input constraint ID exactly once, rejects unknown
IDs and extra fields, and derives the verdict locally: any `VIOLATION` wins;
otherwise any `ABSTAIN` wins; otherwise the verdict is `PASS`. Only after a
deterministic `ALLOW`, `PASS` maps to `ALLOW`, `VIOLATION` maps to `BLOCK`, and
`ABSTAIN` maps to `REVIEW`.

Live cache misses make exactly one independent model call. Valid normalized
results and normalized failure abstentions are stored under the canonical input
hash. Cache hits verify input, model, prompt, result coverage, and output hash.
Replay never calls the model and raises a hard replay error on a cache miss.

## Optional manual OpenAI smoke test

Construct an OpenAI client outside policy logic and inject it into
`OpenAIResponsesSemanticModel(client=client, model_id="<configured alias>")`.
Then construct `SemanticVerifier` with a `FileSemanticCache`. The adapter makes
one Responses API request with strict Structured Outputs, `store=False`, no
tools, no function calling, no web search, and no conversation state. Credentials
remain in the caller's client configuration. No network call is part of the unit
suite, and no model alias is selected by transaction or evidence data.

## Claim boundary

D5 claims only that the exact, hash-bound normalized response actually used can
be replayed for the exact semantic input. It does not claim that a provider model
is deterministic or objectively correct, that confidence is calibrated, that
semantic evidence proves human intent, that prompt injection is solved, or that
Tier C can override deterministic authorization.
