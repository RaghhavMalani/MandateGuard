# Failure Log

## D3 — decision-time evidence was incompletely bound

The initial event records bound outcomes but omitted some decision-time evidence. Adversarial review exposed identical event hashes for materially different evidence. Schema 1.1 fixed this by binding server time, PSP commitments, and the nonce snapshot digest.

Lesson: audit provenance must bind evidence, not only outcome.

## D6 — execution objects were bound without proving their relationship

Initial D6 capability issuance cryptographically bound the mandate,
transaction, `AuthorizationResult`, and provider request individually, but did
not prove those objects belonged to the same authorization context.

Adversarial review demonstrated that an `ALLOW` result from a permissive
mandate could be paired with a restrictive mandate and still reach a reserved
execution grant. This was a review finding, not a production incident.

Repair: issuance now reruns the actual authorization policy from the complete
recorded `ReplayScenario` and requires the reproduced `AuthorizationResult`
hash to match before signing. Semantic decisions are reproduced only from an
exact replay cache record; missing or invalid replay state fails closed.

Lesson: cryptographically binding several objects is insufficient unless their
relationship is also verified.
