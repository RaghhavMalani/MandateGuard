# Frozen Resolve recovery evaluation fixtures

Twenty independently authored synthetic worlds for the MandateGuard Resolve
bounded-recovery evaluation, plus the preregistration records that freeze them.

- `worlds/<case-id>.json` — one merchant, SKU, catalogue entry, mandate,
  transaction, initial evidence selection, and trusted source manifest set.
- `evidence/<merchant-id>.json` — the complete bundle a trusted evidence
  provider returns for that merchant, in the strict fixture format
  `mandateguard.semantic.evidence` decodes.
- `preregistration_plan.json` — the frozen plan.
- `preregistration_freeze.json` — plan hashes, fixture hashes, registry hash.
- `preregistration_commit.json` — the second freeze step, naming the commit that
  introduced the plan.

These files are frozen. Every manifest record hash re-derives from its evidence
entry, and every file hash is committed in the freeze record, so any edit is
detected by `scripts/validate_resolve_preregistration.py` and refuses the
runner. See `docs/RESOLVE_EVALUATION_PROTOCOL.md`.
