# Failure Log

## D3 — decision-time evidence was incompletely bound

The initial event records bound outcomes but omitted some decision-time evidence. Adversarial review exposed identical event hashes for materially different evidence. Schema 1.1 fixed this by binding server time, PSP commitments, and the nonce snapshot digest.

Lesson: audit provenance must bind evidence, not only outcome.
