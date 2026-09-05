import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  CHECK_GROUPS,
  PLAYGROUND_RAIL,
  barBucket,
  checkGroupStatus,
  determiningReasons,
  escapeHtml,
  evidenceSummary,
  missingEvidence,
  progressBucket,
  railProgress,
  railStates,
  renderChosenProduct,
  renderClarificationRequired,
  renderGapFigure,
  renderJudgeHealth,
  renderJudgeTestStrip,
  renderNoMatch,
  renderOnboardedResult,
  renderOnboardingForm,
  renderPlaygroundCandidate,
  renderPlaygroundCandidates,
  renderPlaygroundExecution,
  renderPlaygroundFollowUps,
  renderPlaygroundMandate,
  renderPlaygroundRail,
  renderPlaygroundVerdict,
  renderPlaygroundWhy,
  renderMandateComparison,
  renderMandateGuardChecks,
  renderExecutionLab,
  renderRecurringProof,
  renderReadiness,
  renderScaleWorlds,
  renderScenarioGrid,
  renderSpendingLimitPrompt,
  renderTryThese,
  renderWhyFound,
} from "../../src/mandateguard/product/static/app.js";

const HTML = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/index.html", import.meta.url)),
  "utf8",
);
const CSS = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/app.css", import.meta.url)),
  "utf8",
);

const CANDIDATE = {
  catalog_product_id: "sandbox.0123456789abcdef01234567",
  merchant_id: "sandbox-relay-audio",
  merchant: "Relay Audio (Synthetic)",
  sku: "audio-headphones-026",
  name: "Kestrel Wireless Headphones M60",
  brand: "Kestrel",
  category: "Headphones",
  category_group: "Electronics",
  description: "A synthetic sandbox listing.",
  price_minor: 89900,
  currency: "INR",
  billing_model: "ONE_TIME",
  recurring: false,
  recurrence_declaration: "SETTLED_ONCE",
  effective_from: "2026-09-01T00:00:00Z",
  evidence_version: "v1",
  world: "SANDBOX",
  synthetic: true,
  why_found: {
    category_match: "Headphones",
    matched_terms: ["wireless", "headphones"],
    brand_match: null,
    within_budget: true,
    lexical_score: 18.4,
    category_score: 9,
    price_minor: 89900,
    max_total_minor: 500000,
    quantity: 1,
    currency: "INR",
    exact_phrase_match: "wireless headphones",
    total_score: 41.2,
  },
  readiness: {
    merchant_identity: "DECLARED",
    sku_evidence: "DECLARED",
    authoritative_price: "DECLARED",
    billing_model: "DECLARED",
    content_classification: "DECLARED",
    intended_use: "DECLARED",
    evidence_version: "CURRENT",
  },
};

/* ------------------------------------------------------------------ */
/* Navigation and page structure                                       */
/* ------------------------------------------------------------------ */

test("the Playground is the first tab and the default panel", () => {
  const order = [...HTML.matchAll(/data-view="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(order, [
    "playground",
    "observe",
    "attack",
    "scale",
    "evidence",
    "evaluation",
  ]);
  assert.match(HTML, /id="tab-playground"[\s\S]*?aria-selected="true"/);
  // Only the Playground panel is visible on load.
  assert.match(HTML, /<section id="view-playground" class="view"[^>]*tabindex="-1">/);
  assert.match(HTML, /<section id="view-observe"[^>]*hidden>/);
  assert.match(HTML, /<section id="view-scale"[^>]*hidden>/);
});

test("the sandbox is declared before the input, not after it", () => {
  const badge = HTML.indexOf("SIMULATED MERCHANT SANDBOX");
  const explanation = HTML.indexOf("Nothing here represents a live marketplace or real");
  const input = HTML.indexOf('id="pg-intent"');
  assert.ok(badge > 0 && explanation > badge);
  assert.ok(explanation < input, "the sandbox notice must precede the instruction field");
});

test("the first viewport states the claim, the rail, and the three answers", () => {
  const hero = HTML.indexOf("pg-hero");
  const console_ = HTML.indexOf('id="pg-console"');
  const results = HTML.indexOf("pg-results-region");
  assert.ok(hero > 0 && hero < console_ && console_ < results);
  for (const line of [
    "AI can choose what to buy",
    "It shouldn&rsquo;t decide what it&rsquo;s allowed to pay for",
    "MandateGuard verifies user constraints, trusted merchant evidence, and current",
    "consent before payment execution",
    "AI BUYER",
    "MANDATEGUARD",
    "PAYMENT",
    "matches mandate",
    "violates mandate",
    "insufficient trusted evidence",
    "TRY IT YOURSELF",
  ]) {
    assert.ok(HTML.includes(line), `missing opening line: ${line}`);
  }
});

test("the page asks its five questions in order", () => {
  const order = [
    "STEP 1 &mdash; WHAT DID YOU ASK THE AI TO BUY?",
    "STEP 2 &mdash; WHAT THE AI FOUND",
    "STEP 3 &mdash; MANDATEGUARD",
    "STEP 5 &mdash; EXECUTION",
  ];
  let cursor = HTML.indexOf("pg-hero");
  for (const step of order) {
    const index = HTML.indexOf(step);
    assert.ok(index > cursor, `step out of order: ${step}`);
    cursor = index;
  }
  // The decision sits between MandateGuard and execution.
  const checks = HTML.indexOf('id="pg-checks"');
  const verdict = HTML.indexOf('id="pg-verdict"');
  const execution = HTML.indexOf('id="pg-execution-region"');
  assert.ok(checks < verdict && verdict < execution);
});

test("research surfaces are demoted behind the product surfaces", () => {
  const primary = HTML.indexOf('class="mainnav__group" role="presentation"');
  const secondary = HTML.indexOf("mainnav__group--secondary");
  assert.ok(primary > 0 && secondary > primary);
  for (const view of ["playground", "observe", "attack", "scale"]) {
    assert.ok(HTML.indexOf(`data-view="${view}"`) < secondary, `${view} must stay primary`);
  }
  for (const view of ["evidence", "evaluation"]) {
    assert.ok(HTML.indexOf(`data-view="${view}"`) > secondary, `${view} must be secondary`);
  }
});

test("every Playground panel the script writes into exists in the markup", () => {
  for (const id of [
    "pg-catalog-meta",
    "pg-try-row",
    "pg-intent",
    "pg-search-button",
    "pg-error",
    "pg-rail-region",
    "pg-rail",
    "pg-results-region",
    "pg-results-meta",
    "pg-mandate-panel",
    "pg-limit-panel",
    "pg-candidates",
    "pg-nomatch",
    "pg-chosen-region",
    "pg-chosen-panel",
    "pg-compare",
    "pg-checks",
    "pg-outcome-region",
    "pg-verdict",
    "pg-why",
    "pg-execution-region",
    "pg-execution",
    "pg-followups",
    "pg-scenario-grid",
    "pg-gap-figure",
    "scale-worlds-panel",
    "judge-health-panel",
    "onboard-region",
    "onboard-panel",
  ]) {
    assert.ok(HTML.includes(`id="${id}"`), `missing element: ${id}`);
  }
});

/* ------------------------------------------------------------------ */
/* The live rail                                                       */
/* ------------------------------------------------------------------ */

test("the rail is the four questions a reader is actually holding", () => {
  assert.deepEqual(
    PLAYGROUND_RAIL.map((step) => step.id),
    ["MANDATE", "SELECTION", "MANDATEGUARD", "EXECUTION"],
  );
});

test("the rail is waiting before anything has been asked", () => {
  const states = railStates({});
  assert.equal(states.MANDATE, "waiting");
  assert.equal(states.MANDATEGUARD, "waiting");
  assert.equal(states.EXECUTION, "waiting");
  assert.equal(railProgress({}), 0);
});

test("the rail advances only as far as the run actually got", () => {
  const states = railStates({
    intent: "headphones under 5000",
    candidates: [CANDIDATE],
    selected: CANDIDATE,
    snapshot: { state: "RUNNING", result: null },
  });
  assert.equal(states.MANDATE, "done");
  assert.equal(states.SELECTION, "done");
  assert.equal(states.MANDATEGUARD, "active");
  // No verdict yet, so the rail must not show one.
  assert.equal(states.EXECUTION, "waiting");
});

test("the rail reads the decision from the run and never infers it", () => {
  for (const decision of ["ALLOW", "BLOCK", "REVIEW"]) {
    const states = railStates({
      intent: "x",
      candidates: [CANDIDATE],
      selected: CANDIDATE,
      snapshot: {
        state: "COMPLETE",
        result: { decision, execution: { status: decision === "ALLOW" ? "ORDER_CREATED" : "NOT_CALLED" } },
      },
    });
    assert.equal(states.MANDATEGUARD, decision.toLowerCase());
    assert.equal(states.EXECUTION, decision === "ALLOW" ? "done" : "stopped");
  }
});

test("a BLOCK never lights the execution stage", () => {
  const html = renderPlaygroundRail({
    intent: "x",
    candidates: [CANDIDATE],
    selected: CANDIDATE,
    snapshot: {
      state: "COMPLETE",
      result: { decision: "BLOCK", execution: { status: "NOT_CALLED", razorpay_calls: 0 } },
    },
  });
  assert.match(html, /data-stage="EXECUTION" data-state="stopped"|data-state="stopped" data-stage="EXECUTION"/);
  assert.match(html, /Not reached/);
  assert.doesNotMatch(html, /Simulated offline order created/);
});

test("the rail names all four steps in order", () => {
  const html = renderPlaygroundRail({ intent: "x" });
  let cursor = -1;
  for (const stage of PLAYGROUND_RAIL) {
    const index = html.indexOf(stage.label);
    assert.ok(index > cursor, `stage out of order: ${stage.label}`);
    cursor = index;
  }
});

/* ------------------------------------------------------------------ */
/* Candidates and readiness                                            */
/* ------------------------------------------------------------------ */

const READINESS = {
  merchant_identity: "DECLARED",
  billing_model: "NOT_DECLARED",
  content_classification: "CONFLICTED",
  evidence_version: "CURRENT",
};

test("evidence rows are addressable and neutrally worded", () => {
  const html = renderReadiness(READINESS);
  assert.match(html, /data-field="merchant_identity" data-tone="present"/);
  assert.match(html, /data-field="billing_model" data-tone="missing"/);
  assert.match(html, /data-field="content_classification" data-tone="conflict"/);
  assert.match(html, /AVAILABLE/);
  assert.match(html, /MISSING/);
  assert.match(html, /CONFLICT/);
  assert.match(html, /CURRENT/);
});

test("evidence never borrows the vocabulary of a decision", () => {
  const html = renderReadiness(READINESS);
  for (const word of ["VERIFIED", "PASS", "FAIL", "ALLOW", "BLOCK", "REVIEW", "AUTHORIZED"]) {
    assert.ok(!html.includes(word), `evidence must not say ${word}`);
  }
});

test("the evidence summary is completeness, and says so in one neutral word", () => {
  assert.equal(evidenceSummary(CANDIDATE.readiness).state, "AVAILABLE");
  const incomplete = evidenceSummary({ ...CANDIDATE.readiness, billing_model: "NOT_DECLARED" });
  assert.equal(incomplete.state, "INCOMPLETE");
  assert.deepEqual(incomplete.missing, ["Billing model"]);
  assert.equal(
    evidenceSummary({ ...CANDIDATE.readiness, content_classification: "CONFLICTED" }).state,
    "CONFLICT",
  );
  assert.equal(evidenceSummary(null), null);
});

test("a selection card shows product, price, merchant, category and why it was found", () => {
  const html = renderPlaygroundCandidate(CANDIDATE);
  assert.match(html, /Kestrel Wireless Headphones M60/);
  assert.match(html, /899/);
  assert.match(html, /Relay Audio \(Synthetic\)/);
  assert.match(html, /Headphones/);
  assert.match(html, /WHY FOUND/);
  assert.match(html, /SELECT PRODUCT/);
});

test("a card offers selection, never authorization", () => {
  const html = renderPlaygroundCandidate(CANDIDATE);
  assert.match(html, /TRUSTED EVIDENCE[\s\S]*?AVAILABLE/);
  assert.match(html, /AUTHORIZATION[\s\S]*?NOT YET CHECKED/);
  // Evidence must never be presented as a verdict on the purchase.
  assert.doesNotMatch(html, /ALLOW|BLOCK|REVIEW/);
  assert.doesNotMatch(html, /CHECK AUTHORIZATION/);
});

test("hashes, identifiers and the evidence matrix stay behind a disclosure", () => {
  const html = renderPlaygroundCandidate(CANDIDATE);
  const summaryIndex = html.indexOf("Technical detail");
  assert.ok(summaryIndex > 0);
  for (const hidden of [
    "audio-headphones-026",
    "sandbox-relay-audio",
    "v1",
    "2026-09-01T00:00:00Z",
    "SETTLED_ONCE",
    "PUBLISHED MERCHANT EVIDENCE",
  ]) {
    assert.ok(html.indexOf(hidden) > summaryIndex, `${hidden} must be collapsed`);
  }
});

test("the grid narrows to the chosen listing once one is chosen", () => {
  const payload = {
    candidates: [CANDIDATE, { ...CANDIDATE, catalog_product_id: "sandbox.other", name: "Other" }],
  };
  const collapsed = renderPlaygroundCandidates(payload, CANDIDATE.catalog_product_id, {
    collapsed: true,
  });
  assert.match(collapsed, /Kestrel Wireless Headphones M60/);
  assert.doesNotMatch(collapsed, /Other/);
  assert.match(collapsed, /id="pg-show-all"/);
  assert.match(collapsed, /SHOW ALL 2 RESULTS/);
  // Nothing is destroyed: asking for them all brings them back.
  const expanded = renderPlaygroundCandidates(payload, CANDIDATE.catalog_product_id);
  assert.match(expanded, /Other/);
  assert.doesNotMatch(expanded, /id="pg-show-all"/);
});

test("a renewing listing is flagged on the card before it is chosen", () => {
  const html = renderPlaygroundCandidate({ ...CANDIDATE, recurring: true });
  assert.match(html, /RENEWING PLAN/);
});

test("candidate rendering escapes anything a merchant record could contain", () => {
  const html = renderPlaygroundCandidate({
    ...CANDIDATE,
    name: '<img src=x onerror="alert(1)">',
    merchant: "</script><script>alert(2)</script>",
  });
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;img src=x/);
});

test("why-found names the three signals that put a listing on the page", () => {
  const html = renderWhyFound(CANDIDATE.why_found);
  for (const label of ["Budget", "Key terms", "Category match"]) {
    assert.ok(html.includes(label), `missing signal: ${label}`);
  }
  assert.match(html, /wireless, headphones/);
  assert.match(html, /₹899/);
  assert.match(html, /₹5,000/);
  assert.match(html, /Headphones/);
  assert.doesNotMatch(html, /semantic|similarity|score/i);
});


test("why-found reports an over-budget candidate honestly", () => {
  const html = renderWhyFound({ within_budget: false, matched_terms: ["lamp"] });
  assert.match(html, /above your limit/);
});


test("why-found admits when a signal is absent rather than inventing one", () => {
  const html = renderWhyFound({ within_budget: true, matched_terms: [] });
  assert.match(html, /related listing text/);
  assert.match(html, /no direct term match/);
  assert.match(html, /no limit stated yet/);
});

test("candidate list marks the chosen one", () => {
  const html = renderPlaygroundCandidates(
    { candidates: [CANDIDATE, { ...CANDIDATE, catalog_product_id: "sandbox.other" }] },
    "sandbox.other",
  );
  assert.match(html, /data-product="sandbox\.other"\s*\n?\s*data-selected="true"/);
});

/* ------------------------------------------------------------------ */
/* Mandate, spending limit, and the empty result                       */
/* ------------------------------------------------------------------ */

test("the mandate panel says what was read and disclaims its own authority", () => {
  const html = renderPlaygroundMandate({
    mandate_plain_english: [
      "Spend at most INR 5,000.00 in total (taken from your instruction).",
      "Nothing involving subscriptions.",
    ],
  });
  assert.match(html, /Spend at most INR 5,000\.00/);
  assert.match(html, /advisory step with no authority/);
  assert.match(html, /enforced by the controller, not by the reader/);
});

test("an instruction with no ceiling asks for one instead of inventing it", () => {
  const html = renderSpendingLimitPrompt({ spending_limit_required: true }, 129900);
  assert.match(html, /You did not state a spending limit/);
  assert.match(html, /will not authorize a purchase without one/);
  assert.match(html, /id="pg-limit-input"/);
  assert.match(html, /value="1299"/);
});

test("a stated ceiling does not raise the prompt", () => {
  assert.equal(renderSpendingLimitPrompt({ spending_limit_required: false }, 100), "");
});

test("an empty result names the constraint that excluded each near miss", () => {
  const html = renderNoMatch({
    candidates: [],
    no_match_message: "No suitable sandbox product matched all of your constraints.",
    constraints_applied: ["Price at most INR 200.00", "No renewing subscription"],
    near_misses: [
      {
        name: "Pinewood Laptop",
        price_minor: 3299900,
        currency: "INR",
        excluded_by: "MAX_TOTAL",
        explanation: "Priced at INR 32,999.00, above your INR 200.00 limit.",
      },
    ],
  });
  assert.match(html, /No suitable sandbox product matched all of your constraints\./);
  assert.match(html, /CONSTRAINTS APPLIED/);
  assert.match(html, /CLOSEST CANDIDATES, AND WHAT EXCLUDED THEM/);
  assert.match(html, /MAX_TOTAL/);
  assert.match(html, /above your INR 200\.00 limit/);
});

test("a result with candidates renders no empty-state panel", () => {
  assert.equal(renderNoMatch({ candidates: [CANDIDATE] }), "");
});

test("why-found budget explanation accounts for quantity", () => {
  const html = renderWhyFound({
    within_budget: true,
    matched_terms: ["bottle"],
    price_minor: 75000,
    max_total_minor: 200000,
    quantity: 2,
    currency: "INR",
  });
  assert.match(html, /2 × ₹750 = ₹1,500 ≤ ₹2,000/);
});

test("an absent category gets an honest no-direct-match with labelled alternatives", () => {
  const html = renderNoMatch({
    candidates: [],
    constraints_applied: ["No renewing subscription"],
    no_match: {
      headline: "NO DIRECT SANDBOX MATCH",
      message: "MandateGuard's sandbox does not currently contain this product category.",
      what_was_understood: "smartphones",
      closest_available_categories: [{ category_id: "cameras", label: "Cameras" }],
    },
    near_misses: [],
  });
  assert.match(html, /NO DIRECT SANDBOX MATCH/);
  assert.match(html, /smartphones/);
  assert.match(html, /CLOSEST AVAILABLE CATEGORIES/);
  assert.match(html, /alternatives, not direct matches/i);
});

/* ------------------------------------------------------------------ */
/* Chosen product and its evidence                                     */
/* ------------------------------------------------------------------ */

const SELECTION = {
  product: CANDIDATE,
  readiness: CANDIDATE.readiness,
  notice: "SIMULATED MERCHANT SANDBOX. No real money moves.",
  mandate: {
    raw_text: "Buy wireless headphones under INR 5,000. No subscriptions.",
    search_text: "wireless headphones",
    max_total_minor: 500000,
    currency: "INR",
    quantity: 1,
    recurring_allowed: false,
    recurrence_stated: true,
    exclusions: ["subscriptions"],
    brand_hints: [],
  },
  trusted_evidence: [
    {
      evidence_id: "sbev-audio-headphones-026-terms-v1",
      source_kind: "product_terms",
      scope: "PRODUCT",
      text: "Billing model: one-time purchase, settled once at checkout.",
    },
  ],
};

test("the chosen product shows the merchant's evidence text before the verdict", () => {
  const html = renderChosenProduct(SELECTION);
  assert.match(html, /Billing model: one-time purchase/);
  assert.match(html, /sbev-audio-headphones-026-terms-v1/);
  assert.match(html, /Product terms/);
  assert.match(html, /SIMULATED MERCHANT SANDBOX/);
  assert.match(html, /PRODUCT SELECTED/);
  assert.match(html, /TRUSTED EVIDENCE[\s\S]*?AVAILABLE/);
});

/* ------------------------------------------------------------------ */
/* The authorization workspace                                         */
/* ------------------------------------------------------------------ */

test("the workspace puts what you allowed beside what the agent proposed", () => {
  const html = renderMandateComparison({ playground_selection: SELECTION });
  assert.match(html, /WHAT YOU ALLOWED/);
  assert.match(html, /WHAT THE AGENT PROPOSED/);
  for (const row of [
    "Product / category",
    "Maximum spend",
    "Actual price",
    "Billing requirement",
    "Billing evidence",
    "Brand restriction",
    "Merchant",
    "Important exclusions",
  ]) {
    assert.ok(html.includes(row), `missing comparison row: ${row}`);
  }
  assert.match(html, /₹5,000/);
  assert.match(html, /₹899/);
  assert.match(html, /One-time payment only/);
  assert.match(html, /ONE_TIME/);
  assert.match(html, /subscriptions/);
});

test("the comparison reaches no conclusion of its own", () => {
  const html = renderMandateComparison({ playground_selection: SELECTION });
  // "WHAT YOU ALLOWED" is a column heading; a bare verdict word is not.
  for (const word of ["PASS", "FAIL", "ALLOW", "BLOCK", "REVIEW", "VERIFIED"]) {
    assert.doesNotMatch(
      html,
      new RegExp(`\\\\b${word}\\\\b`),
      `the comparison must not say ${word}`,
    );
  }
});

test("choosing a product announces that authorization has not happened", () => {
  const html = renderMandateGuardChecks({ playground_selection: SELECTION });
  assert.match(html, /data-checked="false"/);
  assert.match(html, /AUTHORIZATION/);
  assert.match(html, /NOT YET CHECKED/);
  for (const group of CHECK_GROUPS) {
    assert.ok(html.includes(group.label), `missing check: ${group.label}`);
  }
  // Every row is pending, and the only thing on offer is asking.
  assert.equal(
    (html.match(/class="pgchecks__status">NOT YET CHECKED</g) || []).length,
    CHECK_GROUPS.length,
  );
  assert.match(html, /class="pgchecks__pending">NOT YET CHECKED</);
  assert.doesNotMatch(html, /\bPASS\b|\bFAIL\b|\bALLOW\b|\bBLOCK\b|\bREVIEW\b/);
  assert.match(html, /data-check-authorization="sandbox\.0123456789abcdef01234567"/);
  assert.match(html, /CHECK AUTHORIZATION/);
});

test("the seven checks are the seven a person would ask about", () => {
  assert.deepEqual(
    CHECK_GROUPS.map((group) => group.label),
    [
      "Budget",
      "Product identity",
      "Merchant identity",
      "SKU evidence",
      "Billing terms",
      "Exclusions",
      "Consent",
    ],
  );
});

test("a check group is only as good as its worst recorded family", () => {
  const budget = CHECK_GROUPS[0];
  const pass = [{ family: "B6", status: "PASS" }, { family: "B7", status: "PASS" }];
  assert.equal(checkGroupStatus(budget, { tierB: pass }), "PASS");
  assert.equal(
    checkGroupStatus(budget, { tierB: [{ family: "B6", status: "FAIL" }, ...pass] }),
    "FAIL",
  );
  assert.equal(
    checkGroupStatus(budget, { tierA: [{ family: "A1", status: "NOT_EVALUABLE" }], tierB: pass }),
    "UNKNOWN",
  );
  // A group with nothing recorded for it never reports success.
  assert.equal(checkGroupStatus(budget, {}), "UNKNOWN");
});

test("an abstained semantic constraint is UNKNOWN, never a pass", () => {
  const exclusions = CHECK_GROUPS.find((group) => group.id === "exclusions");
  assert.equal(checkGroupStatus(exclusions, { semantic: [{ status: "PASS" }] }), "PASS");
  assert.equal(checkGroupStatus(exclusions, { semantic: [{ status: "ABSTAIN" }] }), "UNKNOWN");
  assert.equal(checkGroupStatus(exclusions, { semantic: [{ status: "VIOLATION" }] }), "FAIL");
  // Nothing was stated, so nothing is claimed either way.
  assert.equal(checkGroupStatus(exclusions, { semantic: [] }), "NONE STATED");
});

test("tier language is collapsed, not deleted", () => {
  const html = renderMandateGuardChecks({
    result: {
      authorization: {
        deterministic: {
          tier_a: [{ family: "A3", label: "Merchant binding", status: "PASS" }],
          tier_b: [{ family: "B6", label: "Price ceiling", status: "FAIL" }],
        },
        semantic: { checks: [{ constraint_id: "exclusion.1", constraint: "No gambling", status: "PASS" }] },
      },
    },
  });
  const summary = html.indexOf("Constraint families");
  assert.ok(summary > 0);
  assert.ok(html.indexOf("Merchant binding") > summary);
  assert.ok(html.indexOf("Price ceiling") > summary);
  assert.ok(html.indexOf(">A3<") > summary);
  assert.match(html, /data-check="merchant" data-status="PASS"/);
  assert.match(html, /data-check="budget" data-status="FAIL"/);
});

/* ------------------------------------------------------------------ */
/* Verdict, reasons, execution                                         */
/* ------------------------------------------------------------------ */

test("each verdict gets its own headline and its own surface", () => {
  const cases = {
    ALLOW: "This purchase matches your mandate. Payment execution may proceed.",
    BLOCK: "MandateGuard stopped this before payment.",
    REVIEW: "MandateGuard refused to guess.",
  };
  for (const [decision, headline] of Object.entries(cases)) {
    const html = renderPlaygroundVerdict({
      result: { decision },
      explanation: { headline },
    });
    assert.match(html, new RegExp(`data-decision="${decision}"`));
    assert.match(html, /FINAL DECISION/);
    assert.match(html, new RegExp(`pgverdict__word">${decision}<`));
    assert.ok(html.includes(headline));
  }
});

test("the decision copy survives a follow-up answer that carries no narration", () => {
  // `/execute` and `/mutate` reply with the run and no explanation. The word
  // and its sentence must still be right rather than blank.
  for (const [decision, copy] of [
    ["ALLOW", "This purchase matches your mandate. Payment execution may proceed."],
    ["BLOCK", "MandateGuard stopped this before payment."],
    ["REVIEW", "MandateGuard refused to guess."],
  ]) {
    const html = renderPlaygroundVerdict({ result: { decision } });
    assert.ok(html.includes(copy), `missing fallback copy for ${decision}`);
  }
});

test("a BLOCK on the ceiling shows the limit against the price", () => {
  const html = renderPlaygroundVerdict({
    result: {
      decision: "BLOCK",
      buyer: { price_minor: 799900, currency: "INR" },
      execution: { status: "NOT_CALLED", razorpay_calls: 0, external_network_calls: 0 },
    },
    playground_selection: { mandate: { max_total_minor: 200000 } },
    explanation: {
      headline: "MandateGuard stopped this before payment.",
      why: [
        "INR 7,999.00 > INR 2,000.00 stated limit",
        "Price ceiling: declared order total exceeds the mandate ceiling",
      ],
      failed_constraints: ["B6"],
      failed_constraint_labels: ["Price ceiling"],
      provider_calls: 0,
      external_network_calls: 0,
    },
  });
  assert.match(html, /YOUR LIMIT/);
  assert.match(html, /₹2,000/);
  assert.match(html, /PROPOSED PRICE/);
  assert.match(html, /₹7,999/);
  assert.match(html, /FAILED/);
  assert.match(html, /Price ceiling/);
  assert.match(html, /Provider calls: 0/);
});

test("a BLOCK that is not about money does not invent a price comparison", () => {
  const html = renderPlaygroundVerdict({
    result: {
      decision: "BLOCK",
      buyer: { price_minor: 100000, currency: "INR" },
      execution: { status: "NOT_CALLED" },
    },
    playground_selection: { mandate: { max_total_minor: 500000 } },
    explanation: {
      headline: "MandateGuard stopped this before payment.",
      failed_constraints: ["exclusion.1"],
      failed_constraint_labels: ["Excluded product characteristic: gambling."],
      why: ["VIOLATION: Excluded product characteristic: gambling. — evidence records it"],
      provider_calls: 0,
    },
  });
  assert.doesNotMatch(html, /YOUR LIMIT/);
  assert.match(html, /gambling/);
});

test("a REVIEW names the evidence it is waiting for rather than leaving it to be inferred", () => {
  const snapshot = {
    result: {
      decision: "REVIEW",
      buyer: { price_minor: 149900, currency: "INR" },
      execution: { status: "NOT_CALLED", razorpay_calls: 0, external_network_calls: 0 },
      authorization: {
        semantic: {
          checks: [
            {
              constraint_id: "exclusion.1",
              constraint: "Excluded characteristic: subscriptions.",
              status: "ABSTAIN",
            },
          ],
        },
      },
    },
    playground_selection: {
      readiness: {
        ...CANDIDATE.readiness,
        billing_model: "NOT_DECLARED",
        intended_use: "NOT_DECLARED",
      },
    },
    explanation: {
      headline: "MandateGuard refused to guess.",
      why: ["ABSTAIN: Excluded characteristic: subscriptions. — trusted evidence is insufficient"],
      failed_constraints: ["exclusion.1"],
      failed_constraint_labels: ["Excluded characteristic: subscriptions."],
      provider_calls: 0,
      external_network_calls: 0,
    },
  };
  // The merchant fields come first: they are the ones a merchant can publish.
  assert.deepEqual(missingEvidence(snapshot), ["Billing model", "Intended use"]);
  // When the paperwork is complete and the verifier still abstained, the
  // constraint it abstained on is what is missing.
  assert.deepEqual(
    missingEvidence({
      ...snapshot,
      playground_selection: { readiness: CANDIDATE.readiness },
    }),
    ["Excluded characteristic: subscriptions."],
  );
  const html = renderPlaygroundVerdict(snapshot);
  assert.match(html, /MandateGuard needs more trusted evidence\./);
  assert.match(html, /MISSING/);
  assert.match(html, /Billing model/);
  assert.match(html, /Intended use/);
  assert.match(html, /Payment not reached\./);
  assert.match(html, /Provider calls: 0/);
  // A REVIEW is not a failure: nothing broke, the evidence ran out.
  assert.match(html, /UNRESOLVED/);
  assert.doesNotMatch(html, /pgverdict__k">FAILED</);
});

test("an ALLOW says a capability was issued and offers the next step", () => {
  const html = renderPlaygroundVerdict({
    result: {
      decision: "ALLOW",
      buyer: { price_minor: 219900, currency: "INR" },
      execution: { status: "AUTHORIZED", razorpay_calls: 0, external_network_calls: 0 },
    },
    explanation: {
      headline: "This purchase matches your mandate. Payment execution may proceed.",
      why: [
        "INR 2,199.00 <= INR 5,000.00 stated limit",
        "Merchant identity and SKU evidence verified",
        "Consent ACTIVE at the moment of decision",
      ],
      failed_constraints: [],
      failed_constraint_labels: [],
      provider_calls: 0,
      external_network_calls: 0,
    },
  });
  assert.match(html, /WHY/);
  assert.match(html, /2,199\.00 &lt;= INR 5,000\.00/);
  assert.match(html, /CAPABILITY ISSUED/);
  assert.match(html, /id="pg-continue"/);
  assert.match(html, /CONTINUE TO EXECUTION/);
});

test("no capability is claimed when none was issued", () => {
  const html = renderPlaygroundVerdict({
    result: { decision: "REVIEW", execution: { status: "NOT_CALLED" } },
    explanation: { headline: "MandateGuard refused to guess." },
  });
  assert.doesNotMatch(html, /CAPABILITY ISSUED/);
  assert.doesNotMatch(html, /CONTINUE TO EXECUTION/);
});

test("the determining reasons are the recorded failures, not the whole record", () => {
  assert.deepEqual(
    determiningReasons({
      why: ["INR 100 <= INR 500 stated limit", "Price ceiling: over budget", "something else"],
      failed_constraint_labels: ["Price ceiling"],
    }),
    ["Price ceiling: over budget"],
  );
  // An ALLOW has no failures, so the run's own opening lines stand.
  assert.deepEqual(determiningReasons({ why: ["a", "b", "c", "d", "e"] }), ["a", "b", "c", "d"]);
  assert.deepEqual(determiningReasons(null), []);
});

test("the full reason record is kept, and kept out of the way", () => {
  const html = renderPlaygroundWhy({
    explanation: {
      why: ["INR 7,999.00 > INR 4,000.00 stated limit"],
      failed_constraints: ["B6"],
      provider_calls: 0,
      external_network_calls: 0,
      controller: "EXISTING_FROZEN_MANDATEGUARD_CONTROLLER",
    },
  });
  assert.match(html, /^\s*<details/);
  assert.match(html, /Full reason record/);
  assert.match(html, /FAILED CONSTRAINT/);
  assert.match(html, /<code>B6<\/code>/);
  assert.match(html, /PROVIDER CALLS/);
  assert.match(html, /EXISTING_FROZEN_MANDATEGUARD_CONTROLLER/);
});

test("an ALLOW execution is labelled a simulated offline order and nothing stronger", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: { merchant: "sandbox-acme-audio", sku: "audio-headphones-026", currency: "INR" },
      execution: {
        status: "ORDER_CREATED",
        order: { order_id: "order_offline_abc", amount: 89900, currency: "INR" },
        razorpay_calls: 1,
        external_network_calls: 0,
      },
    },
  });
  assert.match(html, /SIMULATED OFFLINE ORDER/);
  assert.match(html, /order_offline_abc/);
  assert.match(html, /No external network call was made/);
  assert.match(html, /nothing was captured or settled at a payment provider/);
  // The claim never becomes a Razorpay settlement claim.
  assert.doesNotMatch(html, /captured by Razorpay|settled by Razorpay|payment received/i);
});

test("a stopped execution says payment was not reached and counts zero calls", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: { merchant: "sandbox-x", sku: "y" },
      execution: { status: "NOT_CALLED", razorpay_calls: 0, external_network_calls: 0 },
    },
  });
  assert.match(html, /PAYMENT NOT REACHED/);
  assert.match(html, /not created/);
  assert.doesNotMatch(html, /SIMULATED OFFLINE ORDER/);
});

test("a revoked execution shows the consent state at spend time", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: { merchant: "sandbox-x", sku: "y" },
      execution: {
        status: "REJECTED_BEFORE_NETWORK",
        reason: "MANDATE_REVOKED",
        consent: { status: "REVOKED" },
        razorpay_calls: 0,
        external_network_calls: 0,
      },
    },
  });
  assert.match(html, /CONSENT AT SPEND TIME/);
  assert.match(html, /REVOKED/);
  assert.match(html, /MANDATE_REVOKED/);
});

test("a price mutation shows both transactions and all execution-gate checks", () => {
  const snapshot = {
    result: {
      buyer: { currency: "INR" },
      execution: {
        reason: "TRANSACTION_HASH_MISMATCH",
        lab: {
          mutation: "PRICE",
          authorized: { price_minor: 349900, sku: "headphones-042", merchant_id: "merchant-a" },
          attempted: { price_minor: 799900, sku: "headphones-042", merchant_id: "merchant-a" },
          reason: "TRANSACTION_HASH_MISMATCH",
          provider_additional_calls: 0,
          checks: {
            signed: true,
            expired: false,
            mandate_active: true,
            transaction_matches: false,
            provider_reached: false,
          },
        },
      },
    },
  };
  const html = renderExecutionLab(snapshot);
  assert.match(html, /AUTHORIZED PRICE/);
  assert.match(html, /₹3,499/);
  assert.match(html, /ATTEMPTED PRICE/);
  assert.match(html, /₹7,999/);
  assert.match(html, /VALID SIGNATURE/);
  assert.match(html, /TRANSACTION_HASH_MISMATCH/);
  assert.match(html, /REJECTED BEFORE NETWORK/);
  assert.match(html, /EXTERNAL CALLS/);
  for (const label of [
    "SIGNATURE VALID",
    "EXPIRED",
    "CONSENT ACTIVE",
    "TRANSACTION MATCH",
    "PROVIDER REACHED",
  ]) {
    assert.ok(html.includes(label), `missing gate condition: ${label}`);
  }
});

test("the merchant row appears only when a capability recorded a merchant binding", () => {
  const lab = {
    mutation: "MERCHANT",
    authorized: { price_minor: 349900, sku: "h-042", merchant_id: "merchant-a" },
    attempted: { price_minor: 349900, sku: "h-042", merchant_id: "merchant-b" },
    reason: "MERCHANT_MISMATCH",
    provider_additional_calls: 0,
    checks: {
      signed: true,
      expired: false,
      mandate_active: true,
      transaction_matches: false,
      provider_reached: false,
    },
  };
  const withCapability = renderExecutionLab({
    result: { buyer: { currency: "INR" }, execution: { lab, capability: { merchant_bound: false } } },
  });
  assert.match(withCapability, /MERCHANT MATCH/);
  const without = renderExecutionLab({ result: { buyer: { currency: "INR" }, execution: { lab } } });
  assert.doesNotMatch(without, /MERCHANT MATCH/);
});

test("an authorized run names the exact transaction the capability covers", () => {
  const html = renderPlaygroundExecution({
    result: {
      buyer: {
        merchant: "sandbox-relay-audio",
        sku: "audio-headphones-026",
        price_minor: 219900,
        currency: "INR",
      },
      execution: {
        status: "AUTHORIZED",
        capability: { signature_verified: true },
        consent: { status: "ACTIVE" },
        razorpay_calls: 0,
        external_network_calls: 0,
      },
    },
  });
  assert.match(html, /AUTHORIZED TRANSACTION/);
  assert.match(html, /sandbox-relay-audio/);
  assert.match(html, /audio-headphones-026/);
  assert.match(html, /₹2,199/);
  assert.match(html, /CAPABILITY/);
  assert.match(html, /CONSENT/);
  assert.match(html, /ACTIVE/);
});

test("the recurring scenario compares the mandate to trusted merchant billing evidence", () => {
  const html = renderRecurringProof({
    scenario_id: "recurring-billing",
    playground_selection: { product: { billing_model: "RECURRING" } },
    explanation: { failed_constraint_labels: ["Catalog recurrence", "Recurrence permission"] },
    result: { execution: { razorpay_calls: 0 } },
  });
  assert.match(html, /USER REQUIRED/);
  assert.match(html, /ONE_TIME/);
  assert.match(html, /TRUSTED MERCHANT EVIDENCE/);
  assert.match(html, /RECURRING/);
  assert.match(html, /PROVIDER CALLS/);
  assert.match(html, />0</);
});

test("follow-ups are offered only when the run supports them", () => {
  const authorized = renderPlaygroundFollowUps({
    result: { decision: "ALLOW", execution: { status: "AUTHORIZED" } },
  });
  assert.match(authorized, /id="pg-revoke"/);
  assert.match(authorized, /id="pg-execute"/);
  assert.match(authorized, /id="pg-mutate-price"/);
  assert.match(authorized, /id="pg-mutate-sku"/);
  assert.match(authorized, /id="pg-mutate-merchant"/);
  for (const label of [
    ">EXECUTE<",
    ">MUTATE PRICE<",
    ">SWAP SKU<",
    ">CHANGE MERCHANT<",
    ">REPLAY CAPABILITY<",
    ">REVOKE CONSENT<",
  ]) {
    assert.ok(authorized.includes(label), `missing execution-lab action: ${label}`);
  }

  const executed = renderPlaygroundFollowUps({
    result: { decision: "ALLOW", execution: { status: "ORDER_CREATED" } },
  });
  assert.match(executed, /id="pg-replay"/);
  assert.doesNotMatch(executed, /id="pg-revoke"/);

  const blocked = renderPlaygroundFollowUps({
    result: { decision: "BLOCK", execution: { status: "NOT_CALLED" } },
  });
  assert.equal(blocked, "");
});

test("an unrecoverable REVIEW says so instead of offering a button that cannot work", () => {
  const html = renderPlaygroundFollowUps({
    result: {
      decision: "REVIEW",
      execution: { status: "NOT_CALLED" },
      recovery: { action: { enabled: false } },
    },
  });
  assert.doesNotMatch(html, /id="pg-recover"/);
  assert.match(html, /No trusted evidence provider is configured/);
});

test("a replayed capability reports the rejection and the zero extra calls", () => {
  const html = renderPlaygroundFollowUps({
    result: {
      decision: "ALLOW",
      execution: {
        status: "ORDER_CREATED",
        replay: {
          status: "REJECTED_BEFORE_NETWORK",
          reason: "NONCE_ALREADY_USED",
          razorpay_additional_calls: 0,
        },
      },
    },
  });
  assert.match(html, /REJECTED_BEFORE_NETWORK/);
  assert.match(html, /NONCE_ALREADY_USED/);
  assert.match(html, /0 additional provider calls/);
});

/* ------------------------------------------------------------------ */
/* Scenarios, examples, the gap figure, the scale lab                  */
/* ------------------------------------------------------------------ */

test("scenario cards say which world they run in", () => {
  const html = renderScenarioGrid([
    { scenario_id: "safe-purchase", label: "SAFE PURCHASE", story: "s", world: "SANDBOX" },
    { scenario_id: "recoverable-review", label: "RECOVERABLE REVIEW", story: "r", world: "REGISTERED" },
  ]);
  assert.match(html, /data-scenario="safe-purchase"/);
  assert.match(html, /SANDBOX</);
  assert.match(html, /REGISTERED MERCHANT FIXTURES/);
});

test("the top judge strip exposes seven human-labelled real scenarios", () => {
  const html = renderJudgeTestStrip([
    { scenario_id: "safe-purchase", label: "SAFE PURCHASE", expectation: "ALLOW" },
    { scenario_id: "price-mutation", label: "PRICE MUTATION", expectation: "REJECT" },
  ]);
  assert.match(html, /data-scenario="safe-purchase"/);
  assert.match(html, /data-scenario="price-mutation"/);
  assert.match(HTML, /id="pg-judge-strip"/);
  assert.match(HTML, /Or run a prepared journey/i);
});

test("try-these prompts are human sentences, not test-case names", () => {
  const html = renderTryThese([
    { label: "Buy headphones under ₹5,000", intent: "Buy wireless headphones under INR 5,000." },
  ]);
  assert.match(html, /data-intent="Buy wireless headphones under INR 5,000\."/);
  assert.match(html, /Buy headphones under/);
  assert.doesNotMatch(html, /test_|case_|preset_id/);
});

test("the gap figure keeps the three populations apart", () => {
  const html = renderGapFigure({ marketplace: 17702, sandbox: 3960 });
  assert.match(html, /SEARCHABLE/);
  assert.match(html, /17,702/);
  assert.match(html, /trust gap/);
  assert.match(html, /AGENT-READY/);
  assert.match(html, /3,960/);
  assert.match(html, /AUTHORIZED NOW/);
  // No total anywhere: adding these numbers would be the one wrong move.
  assert.doesNotMatch(html, /20,762/);
});

test("the scale lab refuses to combine populations into one number", () => {
  const html = renderScaleWorlds(
    { discovery: { catalog: { listings: 17702 } }, system_scale: {} },
    { catalog: { products: 3960 } },
  );
  assert.match(html, /DISCOVERY REALITY/);
  assert.match(html, /JUDGE SANDBOX/);
  assert.match(html, /AUTHORIZATION SCALE/);
  assert.match(html, /MODEL QUALITY/);
  assert.match(html, /never combined into a single/);
});

test("the outcome-mix panel labels itself an experience target", () => {
  const html = renderJudgeHealth({
    queries: 120,
    overall: { rates: { ALLOW: 0.93, BLOCK: 0, REVIEW: 0.07, NO_RESULT: 0 }, candidate_found_rate: 1 },
    ordinary: { rates: { ALLOW: 0.99, BLOCK: 0, REVIEW: 0.01, NO_RESULT: 0 } },
    insistent_selection: { rates: { ALLOW: 0, BLOCK: 1, REVIEW: 0, NO_RESULT: 0 } },
  });
  assert.match(html, /120 fixed judge queries/);
  assert.match(html, /Insisting on a flagged listing/);
  assert.match(html, /experience target, not a safety contract/i);
});

test("the outcome-mix panel does not claim to be preregistered", () => {
  const html = renderJudgeHealth({
    queries: 120,
    overall: { rates: { ALLOW: 0.9, BLOCK: 0.02, REVIEW: 0.08, NO_RESULT: 0 }, candidate_found_rate: 1 },
    ordinary: { rates: { ALLOW: 0.99, BLOCK: 0, REVIEW: 0.01, NO_RESULT: 0 } },
    insistent_selection: { rates: { ALLOW: 0, BLOCK: 1, REVIEW: 0, NO_RESULT: 0 } },
  });
  // The questions and the first measured report landed in one commit, so the
  // panel says so rather than borrowing the credibility of a preregistration
  // that did not happen.
  assert.match(html, /landed in one\s+commit/);
  assert.match(html, /not an independently preregistered evaluation/);
  assert.doesNotMatch(html, /\bfrozen\b/);
});

test("a missing outcome report is reported as missing, not faked", () => {
  assert.match(renderJudgeHealth(null), /has not been generated/);
});

/* ------------------------------------------------------------------ */
/* Simulated merchant onboarding                                       */
/* ------------------------------------------------------------------ */

const ONBOARD_FORM = {
  form: {
    simulation: true,
    notice:
      "SIMULATION. This creates a new synthetic sandbox merchant record. The original marketplace listing is not modified and does not become trusted.",
    copied_from_listing: {
      listing_id: "flipkart.abc",
      title: "Tootpado Cartoon LED Desk Light",
      category_label: "Table Lamps",
      classification: "NEUTRAL_DISCOVERY_ATTRIBUTES",
      note: "Only the words and the shelf are carried across.",
    },
    required_declarations: [
      { field: "merchant_display_name", label: "Merchant identity", type: "text", why: "seller of record" },
      { field: "price_minor", label: "Authoritative price", type: "integer", why: "checked against" },
      { field: "billing_model", label: "Billing model", type: "choice", choices: ["ONE_TIME", "RECURRING"], why: "not inferred" },
      { field: "purposes", label: "Documented intended use", type: "multi-choice", choices: ["home use"], why: "checked against" },
    ],
    prefilled: { merchant_display_name: "Lamp Sandbox Merchant", price_minor: null },
  },
};

test("the onboarding form is labelled a simulation and asks for every trusted field", () => {
  const html = renderOnboardingForm(ONBOARD_FORM);
  assert.match(html, /SIMULATION\./);
  assert.match(html, /does not become trusted/);
  assert.match(html, /data-declare="merchant_display_name"/);
  assert.match(html, /data-declare="price_minor"/);
  assert.match(html, /data-declare="billing_model"/);
  assert.match(html, /data-purpose="true"/);
  assert.match(html, /Only the words and the shelf are carried across\./);
});

test("the onboarding form does not prefill a price from the crawled listing", () => {
  const html = renderOnboardingForm(ONBOARD_FORM);
  const priceField = html.slice(html.indexOf('data-declare="price_minor"'));
  assert.match(priceField.slice(0, 200), /value=""/);
});

test("the onboarded result keeps the original listing visibly untrusted", () => {
  const html = renderOnboardedResult({
    notice: "SIMULATION. A new synthetic sandbox merchant record was created.",
    merchant: {
      display_name: "Lamp Co (Synthetic)",
      merchant_id: "sandbox-onboarded-lamp-co-abc123",
      sku: "onboarded-lamp-abc123",
    },
    product: { catalog_product_id: "sandbox.def", name: "Lamp" },
    readiness: { merchant_identity: "DECLARED", billing_model: "DECLARED" },
    source_listing: {
      title: "Tootpado Cartoon LED Desk Light",
      still_untrusted: true,
      note: "The marketplace listing is unchanged.",
    },
  });
  assert.match(html, /NEW SYNTHETIC MERCHANT RECORD/);
  assert.match(html, /sandbox-onboarded-lamp-co-abc123/);
  assert.match(html, /THE ORIGINAL MARKETPLACE LISTING/);
  assert.match(html, /STILL UNTRUSTED/);
  assert.match(html, /data-untrusted="true"/);
  assert.match(html, /RUN AUTHORIZATION AGAINST THE NEW RECORD/);
});

/* ------------------------------------------------------------------ */
/* Accessibility, motion, and mobile                                   */
/* ------------------------------------------------------------------ */

test("the Playground tab participates in the roving tablist", () => {
  assert.match(HTML, /id="tab-playground"[\s\S]*?role="tab"/);
  assert.match(HTML, /aria-controls="view-playground"/);
  assert.match(HTML, /<section id="view-playground"[^>]*role="tabpanel"/);
  assert.match(HTML, /aria-labelledby="tab-playground"/);
});

test("the instruction field is labelled and the error region is announced", () => {
  assert.match(HTML, /<label class="pg-field__label" for="pg-intent">/);
  assert.match(HTML, /id="pg-error" role="alert"/);
  assert.match(HTML, /id="pg-rail" aria-live="polite"/);
});

test("every Playground control is a real button", () => {
  for (const html of [
    renderPlaygroundCandidate(CANDIDATE),
    renderScenarioGrid([{ scenario_id: "a", label: "A", story: "s", world: "SANDBOX" }]),
    renderTryThese([{ label: "x", intent: "y" }]),
    renderPlaygroundFollowUps({ result: { decision: "ALLOW", execution: { status: "AUTHORIZED" } } }),
  ]) {
    const clickable = [...html.matchAll(/<(\w+)[^>]*data-(?:authorize|scenario|intent)=/g)];
    for (const match of clickable) {
      assert.equal(match[1], "button");
    }
    assert.doesNotMatch(html, /<div[^>]*onclick/);
  }
});

test("reduced motion is honoured for the Playground as well", () => {
  const block = CSS.slice(CSS.indexOf("@media (prefers-reduced-motion: reduce)"));
  assert.match(block, /transition-duration: 1ms !important/);
  assert.match(block, /animation-duration: 1ms !important/);
});

test("the Playground reflows to a single column on a phone", () => {
  const mobile = CSS.slice(CSS.lastIndexOf("@media (max-width: 720px)"));
  for (const selector of [
    ".pg-candidates",
    ".pg-scenario-grid",
    ".gapfig__row",
    ".scaleworlds__row",
    ".rail__list",
    ".pgcompare__row",
    ".pg-flow",
  ]) {
    assert.ok(mobile.includes(selector), `no mobile rule for ${selector}`);
  }
  assert.match(mobile, /grid-template-columns: 1fr/);
  // Stacked, the comparison has to relabel its own columns or the two values
  // become indistinguishable.
  assert.match(mobile, /YOU ALLOWED/);
  assert.match(mobile, /AGENT PROPOSED/);
});

test("no Playground surface reaches outside the origin", () => {
  const playgroundCss = CSS.slice(CSS.indexOf("/* Playground"));
  assert.doesNotMatch(playgroundCss, /https?:\/\//);
  assert.doesNotMatch(playgroundCss, /@import/);
  assert.doesNotMatch(HTML.slice(HTML.indexOf('id="view-playground"'), HTML.indexOf('id="view-observe"')), /https?:\/\//);
});

test("escapeHtml is applied to every interpolated field on a candidate", () => {
  const hostile = '"><script>alert(1)</script>';
  const html = renderPlaygroundCandidate({
    ...CANDIDATE,
    catalog_product_id: hostile,
    sku: hostile,
    merchant_id: hostile,
    billing_model: hostile,
    recurrence_declaration: hostile,
    evidence_version: hostile,
    effective_from: hostile,
    category: hostile,
  });
  assert.doesNotMatch(html, /<script>/);
  assert.ok(html.includes(escapeHtml(hostile)));
});

/* ---------------- unresolved requirements ---------------- */

const UNRESOLVED_SEARCH = {
  candidates: [CANDIDATE],
  clarification_required: true,
  clarification_message:
    "I found matching products, but I could not safely interpret one of your requirements",
  constraint_coverage: {
    coverage_status: "UNRESOLVED_HARD_CONSTRAINT",
    recognized_constraints: ["MAX_TOTAL: INR 3,000.00"],
    unresolved_constraint_spans: [
      { cue: "only", strength: "STRONG", start: 28, end: 32, text: "vegan materials only" },
    ],
    blocks_authorization: true,
  },
};

test("an unresolved requirement is quoted back in the buyer's own words", () => {
  const html = renderClarificationRequired(UNRESOLVED_SEARCH);
  assert.match(html, /could not safely\s+interpret one of your requirements/);
  assert.match(html, /vegan materials only/);
  assert.match(html, /data-status="UNRESOLVED_HARD_CONSTRAINT"/);
});

test("the panel says plainly that nothing was issued and nothing was called", () => {
  const html = renderClarificationRequired(UNRESOLVED_SEARCH);
  assert.match(html, /No mandate was issued/);
  assert.match(html, /no capability exists/);
  assert.match(html, /no payment adapter was called/);
});

test("the panel still shows what the reader did understand", () => {
  const html = renderClarificationRequired(UNRESOLVED_SEARCH);
  assert.match(html, /WHAT IT DID READ AS ENFORCEABLE/);
  assert.match(html, /MAX_TOTAL: INR 3,000.00/);
});

test("a fully covered instruction renders no clarification panel at all", () => {
  assert.equal(renderClarificationRequired({ clarification_required: false }), "");
  assert.equal(renderClarificationRequired(null), "");
});

test("selection is not offerable while a requirement is unresolved", () => {
  const html = renderPlaygroundCandidates(UNRESOLVED_SEARCH);
  assert.match(html, /disabled/);
  assert.match(html, /CLARIFY YOUR REQUIREMENT FIRST/);
  assert.doesNotMatch(html, /SELECT PRODUCT/);
});

test("selection stays offerable when every requirement resolved", () => {
  const html = renderPlaygroundCandidates({
    candidates: [CANDIDATE],
    clarification_required: false,
  });
  assert.match(html, /SELECT PRODUCT/);
  assert.doesNotMatch(html, /disabled/);
});

test("the clarification panel escapes the text it quotes back", () => {
  const html = renderClarificationRequired({
    clarification_required: true,
    constraint_coverage: {
      coverage_status: "UNRESOLVED_HARD_CONSTRAINT",
      recognized_constraints: ['<img src=x onerror="alert(1)">'],
      unresolved_constraint_spans: [
        { cue: "only", strength: "</span><script>alert(2)</script>", text: "<script>alert(3)</script>" },
      ],
    },
  });
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;script&gt;/);
});

test("the Playground markup and stylesheet carry the clarification region", () => {
  assert.match(HTML, /id="pg-clarify-panel"/);
  assert.match(CSS, /\.pgclarify\b/);
  assert.match(CSS, /\.pgcard__go:disabled/);
});


/* ------------------------------------------------------------------ */
/* Content Security Policy                                             */
/*                                                                     */
/* The product is served under `style-src 'self'`. A style attribute -  */
/* written into a template, set with setAttribute, or assigned through  */
/* el.style - is refused by the browser and reported as a violation on  */
/* every load. The policy is not negotiable, so the presentation has to */
/* travel as classes and data attributes instead.                       */
/* ------------------------------------------------------------------ */

const APP_JS = readFileSync(
  fileURLToPath(new URL("../../src/mandateguard/product/static/app.js", import.meta.url)),
  "utf8",
);

test("no rendered markup carries a style attribute", () => {
  for (const [name, source] of [
    ["app.js", APP_JS],
    ["index.html", HTML],
  ]) {
    assert.ok(!/\sstyle\s*=\s*["']/.test(source), `${name} must not emit a style attribute`);
  }
});

test("no script path mutates style at runtime", () => {
  for (const pattern of [
    /\.style\.setProperty\s*\(/,
    /\.style\.[A-Za-z]/,
    /\.cssText\s*=/,
    /setAttribute\s*\(\s*["']style["']/,
    /insertRule\s*\(/,
  ]) {
    assert.ok(!pattern.test(APP_JS), `app.js must not use ${pattern}`);
  }
});

test("no stylesheet or script is fetched from off-origin", () => {
  assert.ok(!/<link[^>]+href="https?:/.test(HTML));
  assert.ok(!/<script[^>]+src="https?:/.test(HTML));
  assert.ok(!/@import/.test(CSS));
  // Inline <style> and <script> blocks are refused by the same policy.
  assert.ok(!/<style[\s>]/.test(HTML), "index.html must not carry an inline stylesheet");
  assert.ok(
    !/<script(?![^>]*\ssrc=)[^>]*>/.test(HTML),
    "index.html must not carry an inline script",
  );
});

test("proportional widths come from a finite ladder the stylesheet owns", () => {
  // Every value the renderer can produce must have a rule waiting for it.
  const produced = new Set();
  for (let value = 0; value <= 1000; value += 7) produced.add(barBucket(value, 1000));
  produced.add(barBucket(0, 1));
  produced.add(barBucket(9e9, 1));
  for (const bucket of produced) {
    assert.ok(
      CSS.includes(`.gapfig__bar[data-bar="${bucket}"]`),
      `no width rule for data-bar="${bucket}"`,
    );
  }
  assert.equal(barBucket(17702, 17702), "100");
  assert.equal(barBucket(0, 17702), "5");
});

test("progress is one of five states, never a computed length", () => {
  const seen = new Set();
  for (let step = 0; step <= 20; step += 1) seen.add(progressBucket(step / 20));
  assert.deepEqual([...seen].sort(), ["0", "100", "25", "50", "75"]);
  assert.equal(progressBucket(undefined), "0");
  assert.equal(progressBucket(-3), "0");
  assert.equal(progressBucket(9), "100");
});

test("the gap figure renders widths without a style attribute", () => {
  const html = renderGapFigure({ marketplace: 17702, sandbox: 3960 });
  assert.ok(!/style\s*=/.test(html));
  assert.match(html, /data-bar="100"/);
  assert.match(html, /data-bar="20"/);
});

/* ------------------------------------------------------------------ */
/* One dominant decision colour                                        */
/* ------------------------------------------------------------------ */

/** Every declaration block whose selector list mentions `fragment`. */
function blocksFor(fragment) {
  const blocks = [];
  for (const match of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].includes(fragment)) blocks.push(match[2]);
  }
  return blocks.join("\n");
}

test("evidence surfaces never use a decision colour", () => {
  for (const selector of [
    ".readiness",
    ".pgcard__evidence",
    ".pgcard__authz",
    ".pgchosen__evidence",
    ".pgcard__flag",
  ]) {
    const block = blocksFor(selector);
    assert.ok(block.length > 0, `no rules found for ${selector}`);
    for (const colour of ["--success", "--danger", "--warning", "#7CE8B0", "#FF9AA8", "#FFC96B"]) {
      assert.ok(
        !block.includes(colour),
        `${selector} must not use ${colour}: evidence is not a decision`,
      );
    }
  }
});

test("the decision panel is where the strong colours live", () => {
  assert.match(CSS, /\.pgverdict\[data-decision="ALLOW"\][^{]*\{[^}]*var\(--success\)/);
  assert.match(CSS, /\.pgverdict\[data-decision="BLOCK"\][^{]*\{[^}]*var\(--danger\)/);
  assert.match(CSS, /\.pgverdict\[data-decision="REVIEW"\][^{]*\{[^}]*var\(--warning\)/);
  // And it is the largest thing on the page once it exists.
  assert.match(CSS, /\.pgverdict__word\s*\{[\s\S]*?clamp\(46px/);
});

test("nothing outside the decision and its own rail step paints full-strength green", () => {
  const allowed = [
    ".pgverdict",
    '.rail__step[data-state="allow"]',
    ".gapfig__row",
    ".sysstate",
    ".outcome--allow",
    ".spine",
    ".journey",
    ".pipeline",
    ".listing",
    ".verdict",
    ".status",
    ".check",
    ".tier",
    ".claim",
    ".exchange",
    ".authority",
    ".ledger",
    ".figure",
    ".measured",
    ".attack",
    ".onboard",
    ".fact",
    ".health",
    ".knowledge",
    ".conflict",
    ".provenance",
    ".resolve",
    ".recovery",
    ".transact",
    ".readiness--listing",
  ];
  const playground = CSS.slice(CSS.indexOf("/* Playground"));
  for (const match of playground.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!/var\(--success\)|#7CE8B0/.test(match[2])) continue;
    const selector = match[1].trim();
    assert.ok(
      allowed.some((prefix) => selector.includes(prefix)),
      `unexpected full-strength green on ${selector}`,
    );
  }
});
