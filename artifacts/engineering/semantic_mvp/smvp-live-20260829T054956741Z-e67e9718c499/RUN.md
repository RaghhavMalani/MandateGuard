# First semantic MVP live diagnostic

- Run ID: `smvp-live-20260829T054956741Z-e67e9718c499`
- Started: `2026-08-29T05:49:56.735Z`
- Ended: `2026-08-29T05:51:39.226217Z`
- Execution Git SHA: `56c0dadecfed981ad2a4fc6891e3ebfcb0b32f65`
- Provider/model: `openai_responses` / `gpt-5.6-terra`
- Detector/prompt: `1.0` / `1.0`

| Fixture | Expected | Observed | Final action |
| --- | --- | --- | --- |
| REC PASS | PASS | PASS | ALLOW |
| REC VIOLATION | VIOLATION | VIOLATION | BLOCK |
| REC ABSTAIN | ABSTAIN | ABSTAIN | REVIEW |
| EXC PASS | PASS | PASS | ALLOW |
| EXC VIOLATION | VIOLATION | VIOLATION | BLOCK |
| EXC ABSTAIN | ABSTAIN | ABSTAIN | REVIEW |
| PUR PASS | PASS | PASS | ALLOW |
| PUR VIOLATION | VIOLATION | VIOLATION | BLOCK |
| PUR ABSTAIN | ABSTAIN | ABSTAIN | REVIEW |

## Engineering fixture diagnostic

- Semantic expectation matches: 9 / 9
- Controller action matches: 9 / 9
- Typed/provider errors: 0 / 9
- Latency (nearest-rank): min 1421 ms, p50 2008 ms, p95 4088 ms, max 4088 ms
- Mismatch fixture IDs: none
- Controller mapping failure IDs: none
- Errored fixture IDs: none
- Attempts: exactly one per fixture
- Retries: zero
- Tuning: zero

This is live semantic engineering latency for nine development fixtures, not production PSP latency.
