# The Judge Playground and the sandbox commerce world

## Why this exists

MandateGuard's historical marketplace corpus is 17,702 crawled 2015–2016
listings. They can be searched, classified and deduplicated, and almost none of
them carry the authoritative merchant evidence an authorization controller
needs. So almost every journey through them correctly ends:

```
DISCOVERED → MATCHED → EVIDENCE INCOMPLETE → REVIEW REQUIRED
```

That is a true and useful security result. It is also a terrible first
impression, because it answers a question nobody asked yet. Somebody meeting the
product for the first time needs to see a purchase permitted, a purchase
stopped, and a purchase deferred, and needs to be able to try their own words
without falling into the same wall every time.

The wrong fix would have been to treat crawled rows as trusted. The fix taken
instead is a second, clearly separated world: **synthetic merchants that publish
exactly the evidence a real merchant would have to publish.**

Two worlds, never mixed:

| | Historical marketplace | Judge sandbox |
|---|---|---|
| Source | Public 2015–2016 dataset, crawled | Generated server-side from a versioned template |
| Merchant of record | None | Declared, per listing |
| Authoritative price | None (a listing claim) | Declared, and matched to the store record |
| Billing model | Not published | Published, undeclared, or in conflict — by construction |
| What it answers | "How many listings are ready for AI commerce?" | "What does MandateGuard do when they are?" |
| Can be authorized | No | Yes — and may still be BLOCK or REVIEW |

## What the sandbox does not change

Nothing about how a decision is made. A Playground run goes through
`run_agentic_checkout` with a different `TrustedCommerceStore` and nothing else
altered: the same Tier A deterministic checks, the same Tier B constraint
evaluation, the same Tier C semantic verifier, the same capability issuance, the
same execution ledger, the same consent registry, the same replay protection.

There is no branch anywhere that reads "sandbox" and returns ALLOW. The
Playground service constructs *runs*, never decisions —
`tests/test_judge_playground_journeys.py::test_no_scenario_can_force_an_allow`
asserts that no verdict literal appears on the path that starts one.

## The generator

`src/mandateguard/sandbox/`

| Module | What it owns |
|---|---|
| `templates.py` | The frozen construction vocabulary: 34 categories, 50 merchants, brands, price bands, purposes, and the evidence sentence templates. Versioned by `WORLD_VERSION`. |
| `universe.py` | Deterministic generation. Every field is a pure function of `(WORLD_VERSION, WORLD_SEED, category_id, index)`. |
| `store.py` | Projection into `TrustedCommerceStore`, plus the declaration scan that produces the readiness signals. |
| `intent.py` | Reading an arbitrary buying instruction into a bounded mandate. |
| `search.py` | Field-weighted lexical retrieval plus category-synonym matching, and the near-miss explanation. |
| `buyer.py` | The commerce agent, through the same four-function tool boundary. |
| `session.py` | Ephemeral per-visitor scoping. |
| `onboarding.py` | Simulated merchant onboarding. |
| `scenarios.py` | The eight one-click journeys. |
| `health.py` | The measured outcome mix. |

### Determinism

```bash
python scripts/freeze_judge_sandbox.py
```

writes `data/eval/judge-playground/SANDBOX_FREEZE.json`, which records counts
and digests and **no outcomes**. `tests/test_judge_sandbox_universe.py` fails if
the generator drifts from it. Regenerating the freeze is something that
accompanies a `WORLD_VERSION` change, not a way to silence that test.

### Evidence families

A family says what the merchant has *published*, never what the controller will
answer. The same listing can end ALLOW, BLOCK or REVIEW depending entirely on
what the buyer asked for.

| Family | The world state it builds |
|---|---|
| `EVIDENCE_COMPLETE` | Billing model, content classification and intended use all recorded, consistently. |
| `RECURRING_DECLARED` | An unambiguous renewing subscription. |
| `PROHIBITED_CONTENT_DECLARED` | A syllabus that records gambling content as present. |
| `BILLING_UNDECLARED` | A listing whose merchant never wrote down a billing model. |
| `AUTHORITY_CONFLICT` | Two current merchant records that contradict each other. |

The readiness signals shown beside a candidate are produced by **scanning the
published evidence text** (`store.scan_declarations`), not by reading the family
label. A signal that consulted generator metadata would be measuring the fixture
rather than the evidence.

## Reading an arbitrary instruction

`sandbox/intent.py` sits on the agent side and has authority NONE. Two rules
govern it:

**Never drop a stated constraint.** A ceiling, an exclusion, or a recurrence
stance the buyer typed survives into the mandate. This milestone *widened* the
shared exclusion grammar in `discovery/intent.py` so that `nothing involving X`,
`nothing containing X`, `free of X` and `free from X` are captured alongside
`no X` and `without X` — each phrasing it missed was a constraint the buyer
wrote down and the mandate did not carry.

**Never invent one either.** A purpose constraint is asserted only when the text
uses a recognised purpose phrase from the same closed vocabulary the sandbox
merchants publish against. "camera for beginners" contains the word "for"; a
naive extractor turns that into a declared purchase purpose of "beginners" that
no merchant on earth has evidence for, and the REVIEW that follows describes the
parser rather than the world.

### The spending limit

MandateGuard will not authorize an unbounded purchase, so an instruction that
states no ceiling cannot be authorized as typed. The Playground asks for one
rather than inventing a number, and the rule is asymmetric on purpose:

* a ceiling written into the instruction **always** wins;
* a client-supplied ceiling is accepted **only** when the instruction states
  none.

A browser that could quietly widen a ceiling its user typed could turn a BLOCK
into an ALLOW from the client side. `test_a_stated_ceiling_always_beats_a_client_supplied_one`
holds that line.

## Simulated merchant onboarding

The one place the two worlds touch, and the rule at that seam is a single
sentence: **a crawled row never becomes trusted; a new record is created beside
it.**

```
SEARCHABLE → NOT TRANSACTABLE → MERCHANT PUBLISHES TRUSTED EVIDENCE
           → FRESH AUTHORIZATION → ALLOW / BLOCK / REVIEW
```

The listing contributes only `NeutralDiscoveryAttributes` — the words and
roughly which shelf it sits on. Price, seller of record, billing model, content
classification and intended use are **not** copied: those are claims, and a
claim scraped off a page in 2016 is not evidence anybody has vouched for today.
The merchant declares each of them on the record, a brand-new
`sandbox-onboarded-*` merchant and SKU are created inside the visitor's own
session, and authorization runs from scratch against a store containing exactly
that one listing.

After onboarding, the original marketplace listing is byte-identical and still
reports `REVIEW REQUIRED` /
`transactable: false`. `test_onboarding_leaves_the_marketplace_row_untrusted`
compares the discovery selection before and after.

## Sessions

Demo scoping, not authentication. A session identifier proves only that whoever
presents it started a Playground session on this server; it establishes no
identity and grants no privilege.

What it buys is isolation: separate runs, mandates, capabilities, replay history
and onboarded merchants per visitor. Without it, one visitor revoking consent
would cancel another visitor's capability — which would not merely be confusing,
it would be a false demonstration of revocation.

Sessions travel in an `X-MandateGuard-Session` header (never a query string, so
they do not land in an access log or a shared URL), expire after two hours idle,
and are capped at 512 live, 8 onboarded merchants each, 64 runs each.

## The measured outcome mix

```bash
python scripts/evaluate_judge_playground.py
```

runs a fixed set of 120 realistic buying instructions
(`fixtures/playground/judge_queries.json`) through search, selection and the
real controller, and writes
`data/eval/judge-playground/JUDGE_QUERY_REPORT.json`.

The query set records the *kind* of question each entry asks and never an
expected verdict, so there is nothing to tune the world towards.

This is a **fixed engineering UX evaluation, not a preregistered one.** The
questions and the first measured report landed in the same commit, so nothing
in the repository proves the questions were written before the outcomes were
seen, and this document does not claim otherwise. What the version and digest
fields do establish is that a later change to the questions is visible: the set
is versioned (`query_set_version`) and the report records the world digest it
was measured against.

Two passes are measured, because they answer different questions:

* **Top candidate** — what a person is *offered*. Search withholds listings that
  break a stated constraint, so its first result usually satisfies the mandate.
  This measures the agent behaving well.
* **Insistent selection** — authorizing the first listing search had set aside,
  exactly as somebody clicking past the warning would. This measures the gate.

These are **experience targets, not safety contracts**. If ordinary requests
start ending in REVIEW, the thing to fix is the sandbox data. Never the
controller.

## Scale, kept apart

Four populations that are never added together, because no single number would
be true of all of them:

| Population | Count | What it is |
|---|---|---|
| Discovery reality | 17,702 | Historical marketplace listings. Searchable only. |
| Judge sandbox | 3,060 | Synthetic evidence-complete products. |
| Authorization scale | see `docs/AUTHORIZATION_SCALE_PROTOCOL.md` | Synthetic benchmark cases. |
| Model quality | retrieval + classifier metrics | Advisory. Never authorization. |

## Deployment

The sandbox world is generated in-process and ships no catalogue artifact: the
image copies `src/` and one report. Generation costs about a second and happens
on a daemon thread once the port is bound, so a health check is answered
immediately and the lazy path remains as the fallback.

Every interactive journey works with no OpenAI key, no Hugging Face API and no
Razorpay HTTP. Execution uses the offline Razorpay-style test adapter, which is
labelled a **simulated offline order** everywhere it appears and never claims
that a payment provider captured or settled anything.

## Screenshots

```bash
python scripts/run_commerce_lab.py &
PLAYWRIGHT_MODULE=<path to playwright/index.mjs> \
  node scripts/capture_playground_screenshots.mjs http://127.0.0.1:8080
```

Every screen is driven the way a person would drive it — type, search, click a
candidate, read the verdict — so a screenshot showing ALLOW is a screenshot of
the controller having answered ALLOW.
